"""Tray application (pystray) with tkinter windows for pairing — the Windows desktop surface.

Thread model: the pystray icon owns the main thread (its message loop), the asyncio agent runs in a
background thread, and a dedicated Tk thread owns every tkinter object (requests arrive through a
queue). Tray menu actions are posted to the agent loop with ``run_coroutine_threadsafe``
(:meth:`TrayUI._submit`); a window that needs the outcome attaches a done callback that posts it back to
the Tk thread, so the Tk thread never waits on the agent. ``DOME_AGENT_HEADLESS=1`` skips all of this
(:func:`dome_agent.cli` runs the agent directly) so the agent works on servers, in CI and on Linux
without a display. pystray/tkinter/Pillow are imported lazily so importing this module never needs a
display.

Menu: status line · **Disable/Enable remote control** (local flag; no remote frame can change it) ·
Stop manual input · Paired phones ▸ (per phone: Allow touchpad / Allow keyboard, with the broad-scope
explanation on first enable) · Pair a phone… · Add an app… · Approved apps… (list, add, re-approve,
remove, *Full screen with F11* per app: :class:`ApprovedAppsWindow`) · Start at login · Reconnect ·
Diagnostics… (saves the bundle and shows the status in plain words) · Quit. ``show_main`` (second launch
of DoMe) opens the status window and notifies.
"""

from __future__ import annotations

import asyncio
import ntpath
import os
import queue
import sys
import threading
from collections.abc import Callable, Coroutine, Iterable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .approved_apps import (
    ApprovalError,
    clean_display_name,
    explain_approval_error,
    f11_built_in,
    find_approved_by_path,
    nearest_existing_dir,
    normalise_picked_path,
    pick_warning,
    programs_folder,
    suggest_app_id,
    suggest_display_name,
)
from .logsetup import get_logger
from .pairing import INPUT_CAPABILITIES, INPUT_SCOPE_EXPLANATION
from .settings import Settings
from .store import ApprovedAppRow
from .ui import PairingApproval, PairingDisplay, StatusView

log = get_logger(__name__)

T = TypeVar("T")
Submit = Callable[[Coroutine[Any, Any, Any]], "Future[Any] | None"]
Post = Callable[[Callable[[], None]], None]

_COLOURS = {
    "connected": (46, 160, 67),
    "connecting": (210, 153, 34),
    "reconnecting": (210, 153, 34),
    "superseded": (200, 60, 60),
    "stopped": (120, 120, 120),
    "offline": (120, 120, 120),
    "disabled": (200, 60, 60),
}


def _icon_image(colour: tuple[int, int, int]) -> Any:
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=colour + (255,))
    draw.rounded_rectangle((20, 18, 44, 46), radius=6, fill=(255, 255, 255, 255))
    draw.ellipse((28, 36, 36, 44), fill=colour + (255,))
    return image


class TkThread:
    """Owns a hidden Tk root; every Tk call is marshalled onto this thread."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="dome-tk", daemon=True)
        self._root: Any = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)

    def post(self, fn: Callable[[], None]) -> None:
        self._queue.put(fn)

    def _run(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            log.error("tkinter is not available; pairing windows disabled (use the CLI)")
            self._ready.set()
            return
        try:
            self._root = tk.Tk()
        except Exception as exc:  # noqa: BLE001 - TclError: no display / desktop to open windows on
            log.error("Tk could not start; pairing windows disabled (use the CLI)", error=exc.__class__.__name__)
            self._ready.set()
            return
        self._root.withdraw()
        self._root.title("DoMe")
        self._ready.set()
        self._pump()
        self._root.mainloop()

    def _pump(self) -> None:
        # Scheduled first: a posted function that opens a modal dialog keeps Tk's event loop running
        # underneath, and agent results posted meanwhile must still be delivered.
        if self._root is not None:
            self._root.after(100, self._pump)
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                log.warning("tk callback failed", error=exc.__class__.__name__)

    @property
    def root(self) -> Any:
        return self._root


class TrayUI:
    """AgentUI implementation backed by pystray + tkinter."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.agent: Any = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._tk = TkThread()
        self._icon: Any = None
        self._status = StatusView()
        self._pairing_window: Any = None
        self._approval_windows: dict[str, Any] = {}
        self._apps_window: ApprovedAppsWindow | None = None

    # ----- wiring ---------------------------------------------------------------------------------------------
    def bind(self, agent: Any, loop: asyncio.AbstractEventLoop) -> None:
        self.agent = agent
        self.loop = loop

    def _submit(self, coro: Coroutine[Any, Any, T]) -> Future[T] | None:
        """Run ``coro`` on the agent loop and return its future, or None (the coroutine closed) while the loop
        is not running yet or already closed. From the Tk thread never wait on the future: attach a done
        callback that posts the outcome back with ``self._tk.post``."""
        loop = self.loop
        if loop is None or loop.is_closed():
            coro.close()
            return None
        try:
            return asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:  # the loop closed in between (DoMe is quitting)
            coro.close()
            return None

    def _call(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Fire and forget (menu and pairing buttons); a failure is logged (class name only), never lost."""
        action = getattr(coro, "__qualname__", "action")
        fut = self._submit(coro)
        if fut is None:
            return

        def _done(f: Future[Any]) -> None:
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None:
                log.warning("tray action failed", action=action, error=exc.__class__.__name__)

        fut.add_done_callback(_done)

    # ----- AgentUI --------------------------------------------------------------------------------------------
    def update_status(self, status: StatusView) -> None:
        self._status = status
        if self._icon is not None:
            key = "disabled" if not status.remote_enabled else status.connection
            try:
                self._icon.icon = _icon_image(_COLOURS.get(key, _COLOURS["offline"]))
                self._icon.title = f"DoMe — {self._status_line()}"
                self._icon.update_menu()
            except Exception:  # noqa: BLE001, S110 - tray backends raise during teardown
                pass

    def notify(self, title: str, message: str) -> None:
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:  # noqa: BLE001, S110 - notifications are best effort
                pass

    def show_pairing_code(self, display: PairingDisplay) -> None:
        self._tk.post(lambda: self._open_pairing_window(display))

    def show_pairing_request(self, approval: PairingApproval) -> None:
        self._tk.post(lambda: self._open_approval_window(approval))

    def show_main(self) -> None:
        """Second launch of DoMe: bring the status window up and say so (spec §10 single instance)."""
        self.notify("DoMe is already running", "DoMe is running in this session; here is its status.")
        if self.agent is None:
            return
        text = _status_text(self.agent)
        self._tk.post(lambda: self._show_text_window("DoMe", text))

    def pairing_finished(self, pairing_id: str, decision: str) -> None:
        def _close() -> None:
            win = self._approval_windows.pop(pairing_id, None)
            if win is not None:
                win.destroy()
            if self._pairing_window is not None and decision == "approve":
                self._pairing_window.destroy()
                self._pairing_window = None

        self._tk.post(_close)

    # ----- tk windows (Tk thread only) ------------------------------------------------------------------------
    def _open_pairing_window(self, display: PairingDisplay) -> None:
        import tkinter as tk

        import qrcode
        from PIL import ImageTk

        root = self._tk.root
        if root is None:
            return
        if self._pairing_window is not None:
            self._pairing_window.destroy()
        win = tk.Toplevel(root)
        win.title("Pair a phone with DoMe")
        win.resizable(False, False)
        tk.Label(win, text="On your phone, open DoMe and scan this code or type it in.", font=("Segoe UI", 11)).pack(
            padx=16, pady=(16, 8)
        )
        qr = qrcode.QRCode(border=1, box_size=6)
        qr.add_data(display.qr_url)
        qr.make(fit=True)
        image = ImageTk.PhotoImage(qr.make_image(fill_color="black", back_color="white").convert("RGB"))
        label = tk.Label(win, image=image)
        label.image = image  # type: ignore[attr-defined]  # keep a reference alive
        label.pack(padx=16, pady=8)
        tk.Label(win, text=display.code_formatted, font=("Consolas", 18, "bold")).pack(padx=16, pady=8)
        expiry = tk.Label(win, text="", font=("Segoe UI", 9))
        expiry.pack(padx=16, pady=(0, 12))

        def tick() -> None:
            from dome_protocol import now_utc, parse_rfc3339

            remaining = int((parse_rfc3339(display.expires_at) - now_utc()).total_seconds())
            if remaining <= 0:
                expiry.configure(text="This code expired. Start pairing again.")
                return
            expiry.configure(text=f"Expires in {remaining // 60}:{remaining % 60:02d}")
            win.after(1000, tick)

        tick()

        def on_close() -> None:
            self._pairing_window = None
            win.destroy()
            if self.agent is not None and self.agent.pairing is not None:
                self.agent.pairing.cancel()

        win.protocol("WM_DELETE_WINDOW", on_close)
        self._pairing_window = win

    def _open_approval_window(self, approval: PairingApproval) -> None:
        import tkinter as tk

        root = self._tk.root
        if root is None:
            return
        win = tk.Toplevel(root)
        win.title("Approve this phone?")
        win.resizable(False, False)
        tk.Label(win, text="A phone wants to control this PC", font=("Segoe UI", 12, "bold")).pack(
            padx=16, pady=(16, 4)
        )
        tk.Label(win, text=f"Name shown by the phone: {approval.controller_display_name}", font=("Segoe UI", 10)).pack(
            padx=16, pady=2
        )
        tk.Label(win, text="Check that the phone shows this code:", font=("Segoe UI", 10)).pack(padx=16, pady=(12, 2))
        tk.Label(win, text=approval.verification_code, font=("Consolas", 26, "bold")).pack(padx=16, pady=2)
        base = [c for c in approval.requested_capabilities if c not in INPUT_CAPABILITIES]
        tk.Label(win, text="Requested permissions: " + ", ".join(base), font=("Segoe UI", 10)).pack(
            padx=16, pady=(8, 4)
        )
        input_vars: dict[str, Any] = {}
        requested_input = [c for c in INPUT_CAPABILITIES if c in approval.requested_capabilities]
        if requested_input:
            tk.Label(win, text=INPUT_SCOPE_EXPLANATION, wraplength=420, justify="left", font=("Segoe UI", 9)).pack(
                padx=16, pady=(4, 4)
            )
            labels = {"pointer": "Allow touchpad / mouse (pointer)", "keyboard": "Allow keyboard"}
            for cap in requested_input:
                var = tk.BooleanVar(value=False)  # off until the PC owner ticks it (spec §10A-D)
                input_vars[cap] = var
                tk.Checkbutton(win, text=labels[cap], variable=var, font=("Segoe UI", 10)).pack(anchor="w", padx=24)
        buttons = tk.Frame(win)
        buttons.pack(padx=16, pady=(8, 16))

        def approve() -> None:
            if self.agent is not None and self.agent.pairing is not None:
                granted = base + [cap for cap, var in input_vars.items() if bool(var.get())]
                self._call(self.agent.pairing.approve(approval.pairing_id, granted))

        def decline() -> None:
            if self.agent is not None and self.agent.pairing is not None:
                self._call(self.agent.pairing.decline(approval.pairing_id))

        tk.Button(buttons, text="Decline", width=12, command=decline).pack(side="left", padx=8)
        tk.Button(buttons, text="Approve", width=12, command=approve, default="active").pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", decline)
        self._approval_windows[approval.pairing_id] = win

    def _show_text_window(self, title: str, text: str) -> None:
        import tkinter as tk

        root = self._tk.root
        if root is None:
            return
        win = tk.Toplevel(root)
        win.title(title)
        box = tk.Text(win, width=80, height=24, wrap="word")
        box.insert("1.0", text)
        box.configure(state="disabled")
        box.pack(padx=12, pady=12)
        _bring_to_front(win)

    def _open_apps_window(self, add: bool = False) -> None:
        """Tk thread: show the Approved apps window (one at a time); ``add`` then opens the file picker."""
        root = self._tk.root
        if root is None or self.agent is None:
            return
        window = self._apps_window
        if window is None or not window.alive():
            window = ApprovedAppsWindow(
                root=root, agent=self.agent, submit=self._submit, post=self._tk.post, on_closed=self._apps_closed
            )
            self._apps_window = window
        else:
            window.refresh()
        window.show()
        if add:
            window.add_soon()

    def _apps_closed(self) -> None:
        self._apps_window = None

    # ----- tray -------------------------------------------------------------------------------------------------
    def _status_line(self) -> str:
        s = self._status
        if not s.linked:
            return "Not linked — run DoMe link"
        if s.relink_required:
            return "Re-link required"
        if not s.remote_enabled:
            return "Remote control OFF"
        return {
            "connected": "Connected",
            "connecting": "Connecting…",
            "reconnecting": "Reconnecting…",
            "superseded": "Replaced by another agent",
            "stopped": "Stopped",
            "offline": "Offline",
        }.get(s.connection, s.connection)

    def _open_apps_from_tray(self, *, add: bool) -> None:
        """Tray thread: hand the Approved apps window to the Tk thread, or say why it cannot open."""
        if self.agent is None:
            self.notify("DoMe", "DoMe is still starting. Try again in a moment.")
            return
        if self._tk.root is None:  # no tkinter: the Tk thread never pumps, so nothing posted would run
            self.notify("DoMe", "DoMe cannot open windows here. Use: dome-agent approve-app <name> <path to .exe>")
            return
        self._tk.post(lambda: self._open_apps_window(add=add))

    def _menu(self) -> Any:
        import pystray

        def status_item(_: Any) -> str:
            return self._status_line()

        def toggle_label(_: Any) -> str:
            return "Enable remote control" if not self._status.remote_enabled else "Disable remote control"

        def toggle_remote(icon: Any, item: Any) -> None:
            if self.agent is not None:
                self._call(self.agent.set_remote_enabled(not self._status.remote_enabled))

        def pair(icon: Any, item: Any) -> None:
            if self.agent is not None and self.agent.pairing is not None:
                self._call(self.agent.pairing.start())
            else:
                self.notify("DoMe", "Link this PC to your account first (dome-agent link).")

        def add_app(icon: Any, item: Any) -> None:
            self._open_apps_from_tray(add=True)

        def apps(icon: Any, item: Any) -> None:
            self._open_apps_from_tray(add=False)

        def startup_checked(_: Any) -> bool:
            try:
                return bool(self.agent.platform.startup.get_start_at_login()) if self.agent else False
            except Exception:  # noqa: BLE001
                return False

        def toggle_startup(icon: Any, item: Any) -> None:
            if self.agent is None:
                return
            enabled = not startup_checked(None)
            try:
                # Turning it off only removes the Run entry, so it must work even when no command can be built.
                command = startup_command() if enabled else ""
                self.agent.platform.startup.set_start_at_login(enabled, command)
                self.agent.store.set_bool("start_at_login", enabled)
            except Exception as exc:  # noqa: BLE001
                self.notify("DoMe", f"Could not change start at login: {exc.__class__.__name__}")

        def reconnect(icon: Any, item: Any) -> None:
            if self.agent is not None and self.agent.relay is not None:
                self.agent.relay.request_reconnect()

        def diagnostics(icon: Any, item: Any) -> None:
            if self.agent is None:
                return
            from .diagnostics import write_bundle

            text = _status_text(self.agent)
            try:
                path = write_bundle(self.settings, self.agent.status())
            except Exception as exc:  # noqa: BLE001 - a full disk must not take the menu down
                log.warning("diagnostics bundle not written", error=exc.__class__.__name__)
                self.notify("DoMe", f"Could not save the diagnostics file ({exc.__class__.__name__}).")
                text += "\n\nThe diagnostics file could not be saved."
            else:
                self.notify("DoMe diagnostics saved", str(path))
                text += f"\n\nDiagnostics file (send it when you report a problem):\n{path}"
            if self._tk.root is not None:
                self._tk.post(lambda: self._show_text_window("DoMe diagnostics", text))

        def stop_input(icon: Any, item: Any) -> None:
            if self.agent is not None:
                self._call(self.agent.input.end("stopped"))

        def phones_items() -> list[Any]:
            """One submenu per paired phone with checkable 'Allow touchpad' / 'Allow keyboard' items.

            Read again whenever the tray rebuilds its menu (every status update): the icon is created before
            the agent thread binds, and phones are paired while DoMe runs, so a list built once stays empty."""
            if self.agent is None:
                return [pystray.MenuItem("(starting…)", None, enabled=False)]
            items = [
                pystray.MenuItem(f"{g.display_name} ({g.controller_id[:8]}…)", _phone_menu(g.controller_id))
                for g in self.agent.store.list_grants()
            ]
            return items or [pystray.MenuItem("(no paired phones)", None, enabled=False)]

        def _phone_menu(controller_id: str) -> Any:
            def checked(cap: str) -> Callable[[Any], bool]:
                def _checked(_: Any) -> bool:
                    if self.agent is None:
                        return False
                    g = self.agent.store.get_grant(controller_id)
                    return bool(g is not None and cap in g.capabilities)

                return _checked

            def toggle(cap: str) -> Callable[[Any, Any], None]:
                def _toggle(icon: Any, item: Any) -> None:
                    if self.agent is None:
                        return
                    enabling = not checked(cap)(None)
                    if enabling:
                        self.notify("DoMe — manual input", INPUT_SCOPE_EXPLANATION)
                    self._call(
                        self.agent.update_grant_capabilities(
                            controller_id, add=[cap] if enabling else [], remove=[] if enabling else [cap]
                        )
                    )

                return _toggle

            return pystray.Menu(
                pystray.MenuItem("Allow touchpad", toggle("pointer"), checked=checked("pointer")),
                pystray.MenuItem("Allow keyboard", toggle("keyboard"), checked=checked("keyboard")),
                pystray.MenuItem(
                    "What this allows…",
                    lambda icon, item: self._tk.post(
                        lambda: self._show_text_window("Touchpad and keyboard access", INPUT_SCOPE_EXPLANATION)
                    ),
                ),
            )

        def quit_app(icon: Any, item: Any) -> None:
            if self.agent is not None:
                self.agent.request_stop()
            icon.stop()

        return pystray.Menu(
            pystray.MenuItem(status_item, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(toggle_label, toggle_remote),
            pystray.MenuItem("Stop manual input", stop_input),
            pystray.MenuItem("Paired phones", pystray.Menu(phones_items)),
            pystray.MenuItem("Pair a phone…", pair),
            pystray.MenuItem("Add an app…", add_app),
            pystray.MenuItem("Approved apps…", apps),
            pystray.MenuItem("Start at login", toggle_startup, checked=startup_checked),
            pystray.MenuItem("Reconnect", reconnect),
            pystray.MenuItem("Diagnostics…", diagnostics),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit DoMe", quit_app),
        )

    def run(self, agent_main: Callable[[TrayUI], None]) -> None:
        """Start the Tk thread and the agent thread, then block in the tray loop (main thread)."""
        import pystray

        self._tk.start()
        agent_thread = threading.Thread(target=agent_main, args=(self,), name="dome-agent-loop", daemon=True)
        agent_thread.start()
        self._icon = pystray.Icon("DoMe", _icon_image(_COLOURS["offline"]), "DoMe", menu=self._menu())
        self._icon.run()
        agent_thread.join(timeout=10)


def _bring_to_front(win: Any) -> None:
    """A tray program is not the foreground app, so its new windows open behind others: lift once."""
    win.deiconify()
    win.lift()
    win.attributes("-topmost", True)
    win.after(300, lambda: _drop_topmost(win))
    win.focus_force()


def _drop_topmost(win: Any) -> None:
    try:
        win.attributes("-topmost", False)
    except Exception:  # noqa: BLE001, S110 - the window was closed in the meantime
        pass


# ----- Approved apps window ---------------------------------------------------------------------------------------
APPS_INTRO = (
    "Your phone can open, bring to the front, maximise and close only the programs listed here. Nothing else "
    "on this PC can be started from the phone."
)
PROGRAM_FILETYPES = [("Programs", "*.exe")]
FULLSCREEN_F11_LABEL = "Full screen with F11"
FULLSCREEN_F11_BUILT_IN_LABEL = "Full screen with F11 (always on for this app)"
FULLSCREEN_F11_EXPLAINED = (
    "When your phone asks for full screen, DoMe presses the F11 key in this app, so turn this on only if F11 "
    "makes the app full screen: some apps use F11 for something else."
)
APP_STATUS_TEXT = {
    "checking": "Checking…",
    "ok": "Ready",
    "changed": "Changed (an update?): click Re-approve…",
    "missing": "Not found: click Find it again…",
    "unreadable": "DoMe can't read the program file",
}
_ERROR_FG = "#b00020"
_OK_FG = "#1b6e2a"


@dataclass(frozen=True, slots=True)
class AppListRow:
    app_id: str
    name: str
    status: str
    status_text: str
    path: str
    fullscreen: str = "Off"  # the "Full screen" column: "F11" when DoMe may press F11 in the app


@dataclass(frozen=True, slots=True)
class F11Checkbox:
    """How *Full screen with F11* shows for an app: ticked, changeable, and its text."""

    checked: bool
    enabled: bool
    text: str


def f11_checkbox(exe_path: str | None, fullscreen_f11: bool = False, *, busy: bool = False) -> F11Checkbox:
    """The checkbox for the selected app (``exe_path`` None: nothing selected) or for a program being added
    (``fullscreen_f11`` False: so it is pre-ticked only for an app on the built-in F11 list). An app on that
    list gets F11 whatever is stored, so its box is ticked and cannot be changed."""
    if exe_path is None:
        return F11Checkbox(False, False, FULLSCREEN_F11_LABEL)
    if f11_built_in(exe_path):
        return F11Checkbox(True, False, FULLSCREEN_F11_BUILT_IN_LABEL)
    return F11Checkbox(bool(fullscreen_f11), not busy, FULLSCREEN_F11_LABEL)


def app_list_rows(rows: Iterable[ApprovedAppRow], statuses: Mapping[str, str]) -> list[AppListRow]:
    """The window's rows, by name; ``checking`` until the statuses (file hashes) arrive."""
    out: list[AppListRow] = []
    for r in sorted(rows, key=lambda r: (r.display_name.casefold(), r.app_id)):
        status = statuses.get(r.app_id, "checking")
        fullscreen = "F11" if f11_checkbox(r.exe_path, r.fullscreen_f11).checked else "Off"
        out.append(
            AppListRow(r.app_id, r.display_name, status, APP_STATUS_TEXT.get(status, status), r.exe_path, fullscreen)
        )
    return out


def _file_stem(path: str) -> str:
    return ntpath.splitext(ntpath.basename(path))[0]


class ApprovedAppsWindow:
    """*Approved apps…*: the programs the phone may open, with Add an app… / Re-approve… / Remove, and the
    selected app's *Full screen with F11* checkbox.

    Every method runs on the Tk thread. The store is read directly (it is lock-protected, and the menu
    already reads it off the loop); approving, removing and hashing go to the agent loop through ``submit``
    (the agent hashes in a worker thread) and the outcome comes back through ``post``. Modal dialogs are
    opened only from button commands and Tk timers, never from a function posted to the Tk queue."""

    def __init__(self, *, root: Any, agent: Any, submit: Submit, post: Post, on_closed: Callable[[], None]) -> None:
        self._root = root
        self._agent = agent
        self._submit = submit
        self._post = post
        self._on_closed = on_closed
        self._rows: dict[str, ApprovedAppRow] = {}
        self._statuses: dict[str, str] = {}
        self._generation = 0
        self._busy = False
        self._picking = False
        self._last_path = ""
        self._win: Any = None
        self._build()
        self.refresh()

    # ----- lifecycle ------------------------------------------------------------------------------------
    def alive(self) -> bool:
        try:
            return self._win is not None and bool(self._win.winfo_exists())
        except Exception:  # noqa: BLE001 - TclError once the window is gone
            return False

    def show(self) -> None:
        _bring_to_front(self._win)

    def close(self) -> None:
        if self.alive():
            self._win.destroy()
        self._win = None
        self._on_closed()

    def add_soon(self) -> None:
        """The tray's *Add an app…*: open the file picker from a Tk timer, not from the posted function."""
        self._win.after(150, self.add_from_file)

    # ----- layout ----------------------------------------------------------------------------------------
    def _build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        win = tk.Toplevel(self._root)
        win.title("Approved apps – DoMe")
        win.minsize(760, 400)
        win.protocol("WM_DELETE_WINDOW", self.close)
        tk.Label(win, text=APPS_INTRO, wraplength=640, justify="left", font=("Segoe UI", 10)).pack(
            anchor="w", padx=16, pady=(16, 8)
        )
        frame = tk.Frame(win)
        frame.pack(fill="both", expand=True, padx=16)
        tree = ttk.Treeview(
            frame, columns=("name", "fullscreen", "status", "path"), show="headings", selectmode="browse", height=8
        )
        for col, heading, width in (
            ("name", "App", 170),
            ("fullscreen", "Full screen", 80),
            ("status", "Status", 250),
            ("path", "Program file", 330),
        ):
            tree.heading(col, text=heading)
            tree.column(col, width=width, stretch=col == "path")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        option = tk.Frame(win)
        option.pack(fill="x", padx=16, pady=(8, 0))
        self._f11_var: Any = tk.BooleanVar(master=win, value=False)
        self._f11_check: Any = tk.Checkbutton(
            option, text=FULLSCREEN_F11_LABEL, variable=self._f11_var, command=self._toggle_f11, state="disabled"
        )
        self._f11_check.pack(anchor="w")
        tk.Label(option, text=FULLSCREEN_F11_EXPLAINED, wraplength=640, justify="left", font=("Segoe UI", 9)).pack(
            anchor="w", padx=(22, 0)
        )
        buttons = tk.Frame(win)
        buttons.pack(fill="x", padx=16, pady=8)
        self._add_btn: Any = tk.Button(buttons, text="Add an app…", command=self.add_from_file, default="active")
        self._reapprove_btn: Any = tk.Button(buttons, text="Re-approve…", command=self._reapprove)
        self._remove_btn: Any = tk.Button(buttons, text="Remove", command=self._remove)
        for b in (self._add_btn, self._reapprove_btn, self._remove_btn):
            b.pack(side="left", padx=(0, 8))
        tk.Button(buttons, text="Close", command=self.close).pack(side="right")
        self._message: Any = tk.Label(win, text="", anchor="w", justify="left", wraplength=640, font=("Segoe UI", 9))
        self._message.pack(fill="x", padx=16, pady=(0, 12))
        self._tree: Any = tree
        self._win = win

    # ----- list --------------------------------------------------------------------------------------------
    def refresh(self, select: str | None = None) -> None:
        rows = self._agent.apps.list()
        self._rows = {r.app_id: r for r in rows}
        self._statuses = {}
        self._generation += 1
        generation = self._generation
        self._render(select)
        if not rows:
            self._say("No apps yet. Click “Add an app…” and choose a program your phone may open.")
            return
        fut = self._submit(asyncio.to_thread(self._agent.apps.statuses))  # hashes every file: off this thread
        if fut is not None:
            fut.add_done_callback(lambda f: self._post(lambda: self._statuses_ready(generation, f)))

    def _statuses_ready(self, generation: int, fut: Future[Any]) -> None:
        if not self.alive() or generation != self._generation or fut.cancelled():
            return
        exc = fut.exception()
        if exc is not None:
            log.warning("approved app status check failed", error=exc.__class__.__name__)
            return
        self._statuses = dict(fut.result())
        self._render(None)

    def _render(self, select: str | None) -> None:
        current = select or self._selected_id()
        self._tree.delete(*self._tree.get_children())
        for row in app_list_rows(self._rows.values(), self._statuses):
            self._tree.insert("", "end", iid=row.app_id, values=(row.name, row.fullscreen, row.status_text, row.path))
        if current and self._tree.exists(current):
            self._tree.selection_set(current)
            self._tree.see(current)
        self._update_buttons()

    def _selected_id(self) -> str | None:
        selection = self._tree.selection()
        return str(selection[0]) if selection else None

    def _selected_row(self) -> ApprovedAppRow | None:
        app_id = self._selected_id()
        return self._rows.get(app_id) if app_id else None

    def _update_buttons(self) -> None:
        row = self._selected_row()
        status = self._statuses.get(row.app_id, "checking") if row else "checking"
        idle = "disabled" if self._busy else "normal"
        self._add_btn.configure(state=idle)
        self._remove_btn.configure(state=idle if row else "disabled")
        self._reapprove_btn.configure(
            text="Find it again…" if status == "missing" else "Re-approve…",
            state=idle if row and status in ("changed", "missing", "unreadable") else "disabled",
        )
        box = f11_checkbox(row.exe_path if row else None, bool(row and row.fullscreen_f11), busy=self._busy)
        if not self._busy:  # while a change is saved the box keeps showing the owner's click
            self._f11_var.set(box.checked)
        self._f11_check.configure(text=box.text, state="normal" if box.enabled else "disabled")

    # ----- messages and agent calls ---------------------------------------------------------------------
    def _say(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        self._message.configure(text=text, fg=_ERROR_FG if error else _OK_FG if ok else "black")

    def _run(self, coro: Coroutine[Any, Any, Any], on_ok: Callable[[Any], None], *, busy_text: str) -> None:
        fut = self._submit(coro)
        if fut is None:
            self._say("DoMe is still starting or is shutting down. Nothing was changed.", error=True)
            return
        self._busy = True
        self._update_buttons()
        self._win.configure(cursor="watch")
        self._say(busy_text)
        fut.add_done_callback(lambda f: self._post(lambda: self._deliver(f, on_ok)))

    def _deliver(self, fut: Future[Any], on_ok: Callable[[Any], None]) -> None:
        if not self.alive():
            return
        self._busy = False
        self._win.configure(cursor="")
        self._update_buttons()
        if fut.cancelled():
            self._say("DoMe is shutting down. Nothing was changed.", error=True)
            return
        exc = fut.exception()
        if exc is None:
            on_ok(fut.result())
        elif isinstance(exc, ApprovalError):
            self._say(explain_approval_error(exc, self._last_path), error=True)
        else:
            log.warning("approved apps action failed", error=exc.__class__.__name__)
            self._say(f"Something went wrong ({exc.__class__.__name__}). Nothing was changed.", error=True)

    def _ask_for_program(self, title: str, initialdir: str) -> str:
        from tkinter import filedialog

        self._picking = True
        try:
            picked = filedialog.askopenfilename(
                parent=self._win, title=title, initialdir=initialdir, filetypes=PROGRAM_FILETYPES
            )
        finally:
            self._picking = False
        return normalise_picked_path(picked)  # Tk answers C:/forward/slashes

    # ----- add -------------------------------------------------------------------------------------------
    def add_from_file(self) -> None:
        if self._busy or self._picking or not self.alive():
            return
        # os.environ itself (not a copy): it ignores case on Windows
        path = self._ask_for_program("Choose a program your phone may open", programs_folder(os.environ))
        if path:
            self._consider(path)

    def _consider(self, path: str) -> None:
        from tkinter import messagebox

        self._last_path = path
        existing = find_approved_by_path(self._rows.values(), path)
        if existing is not None:
            self._render(existing.app_id)
            self._say(f"“{existing.display_name}” is already in the list.")
            return
        warning = pick_warning(path, os.environ)
        if warning is not None and warning.blocking:
            self._say(warning.text, error=True)
            return
        if warning is not None and not messagebox.askyesno(
            "Add this program?", f"{warning.text}\n\nAdd it anyway?", parent=self._win
        ):
            return
        self._ask_name(path, suggest_display_name(path))

    def _ask_name(self, path: str, suggested: str) -> None:
        import tkinter as tk

        dlg = tk.Toplevel(self._win)
        dlg.title("Add an app")
        dlg.transient(self._win)
        dlg.resizable(False, False)
        tk.Label(dlg, text="Name on your phone:", font=("Segoe UI", 10)).pack(anchor="w", padx=16, pady=(16, 2))
        name = tk.StringVar(value=suggested)
        entry = tk.Entry(dlg, textvariable=name, width=40, font=("Segoe UI", 11))
        entry.pack(padx=16, fill="x")
        hint = tk.Label(dlg, text="", font=("Segoe UI", 9))
        hint.pack(anchor="w", padx=16, pady=(4, 0))
        tk.Label(dlg, text=f"Program: {path}", wraplength=420, justify="left", font=("Segoe UI", 8)).pack(
            anchor="w", padx=16, pady=(8, 0)
        )
        box = f11_checkbox(path)  # pre-ticked only for an app on the built-in F11 list
        f11 = tk.BooleanVar(master=dlg, value=box.checked)
        tk.Checkbutton(
            dlg, text=box.text, variable=f11, state="normal" if box.enabled else "disabled", font=("Segoe UI", 10)
        ).pack(anchor="w", padx=12, pady=(8, 0))
        tk.Label(dlg, text=FULLSCREEN_F11_EXPLAINED, wraplength=420, justify="left", font=("Segoe UI", 8)).pack(
            anchor="w", padx=(34, 16)
        )
        problem = tk.Label(dlg, text="", fg=_ERROR_FG, wraplength=420, justify="left", font=("Segoe UI", 9))
        problem.pack(anchor="w", padx=16)

        def update_hint(*_: Any) -> None:
            shown = clean_display_name(name.get()) or "…"
            hint.configure(text=f"Your phone will list it as “{shown}”.")

        def finish(add: bool) -> None:
            if not add:
                dlg.destroy()
                return
            final = clean_display_name(name.get())
            current = self._agent.apps.list()  # fresh: `dome-agent approve-app` may have added one meanwhile
            if not final:
                problem.configure(text="Please type a name.")
                return
            if any(r.display_name.casefold() == final.casefold() for r in current):
                problem.configure(text=f"Another app is already called “{final}”. Choose a different name.")
                return
            dlg.destroy()
            existing = find_approved_by_path(current, path)
            if existing is not None:
                self.refresh(select=existing.app_id)
                self._say(f"“{existing.display_name}” is already in the list.")
                return
            # A new, unused id (never an existing one: approving under a taken id would be refused anyway)
            app_id = suggest_app_id(_file_stem(path), (r.app_id for r in current))
            self._run(
                self._agent.approve_app_local(app_id, path, final, fullscreen_f11=bool(f11.get())),
                self._added,
                busy_text="Checking the program…",
            )

        name.trace_add("write", update_hint)
        update_hint()
        row = tk.Frame(dlg)
        row.pack(pady=(8, 16))
        tk.Button(row, text="Cancel", width=12, command=lambda: finish(False)).pack(side="left", padx=8)
        tk.Button(row, text="Add", width=12, command=lambda: finish(True), default="active").pack(side="left", padx=8)
        dlg.bind("<Return>", lambda _e: finish(True))
        dlg.bind("<Escape>", lambda _e: finish(False))
        dlg.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        entry.select_range(0, "end")
        entry.focus_set()
        try:
            dlg.grab_set()
        except tk.TclError:  # not viewable yet on some window managers; the dialog still works
            pass

    def _toggle_f11(self) -> None:
        """The *Full screen with F11* checkbox was clicked: store the choice for the selected app."""
        row = self._selected_row()
        if row is None or self._busy or not f11_checkbox(row.exe_path, row.fullscreen_f11).enabled:
            self._update_buttons()  # back to what is stored
            return
        enabled = bool(self._f11_var.get())

        def saved(new: ApprovedAppRow | None) -> None:
            if new is None:  # removed meanwhile (dome-agent remove-app)
                self.refresh()
                self._say(f"“{row.display_name}” is no longer in the list. Nothing was changed.", error=True)
                return
            self._rows[new.app_id] = new
            self._render(new.app_id)
            self._say(
                f"Full screen with F11 is now {'on' if new.fullscreen_f11 else 'off'} for “{new.display_name}”.",
                ok=True,
            )

        self._run(self._agent.set_app_fullscreen_f11_local(row.app_id, enabled), saved, busy_text="Saving…")

    def _added(self, row: ApprovedAppRow) -> None:
        self.refresh(select=row.app_id)
        self._say(f"Added “{row.display_name}”. It now shows in the Apps list on your phone.", ok=True)

    # ----- remove / re-approve -------------------------------------------------------------------------------
    def _remove(self) -> None:
        from tkinter import messagebox

        row = self._selected_row()
        if row is None or self._busy:
            return
        if not messagebox.askyesno(
            "Remove app",
            f"Remove “{row.display_name}”?\n\nYour phone will no longer be able to open or control it.",
            parent=self._win,
        ):
            return

        def removed(_: Any) -> None:
            self.refresh()
            self._say(f"Removed “{row.display_name}”.", ok=True)

        self._run(self._agent.remove_app_local(row.app_id), removed, busy_text="Removing…")

    def _reapprove(self) -> None:
        from tkinter import messagebox

        row = self._selected_row()
        if row is None or self._busy or self._picking:
            return
        path = row.exe_path
        replace = False
        if self._statuses.get(row.app_id) == "missing":
            path = self._ask_for_program(
                f"Where is {row.display_name} now?",
                nearest_existing_dir(row.exe_path) or programs_folder(os.environ),
            )
            if not path:
                return
            warning = pick_warning(path, os.environ)
            other = find_approved_by_path(self._rows.values(), path)
            if other is not None and other.app_id != row.app_id:
                self._say(f"That program is already in the list as “{other.display_name}”.", error=True)
                return
            if warning is not None and warning.blocking:
                self._say(warning.text, error=True)
                return
            replace = True  # same app_id, new file: the phone's routines and phrases keep working
        elif not messagebox.askyesno(
            "Allow the new version?",
            f"“{row.display_name}” changed since you added it, usually because it updated itself.\n\n"
            f"{row.exe_path}\n\nAllow this version?",
            parent=self._win,
        ):
            return
        self._last_path = path

        def done(new: ApprovedAppRow) -> None:
            self.refresh(select=new.app_id)
            self._say(f"“{new.display_name}” is ready again.", ok=True)

        self._run(
            self._agent.approve_app_local(row.app_id, path, row.display_name, replace=replace),
            done,
            busy_text="Checking the program…",
        )


# ----- status text ------------------------------------------------------------------------------------------------
_READ_FAILURE_TEXT = {
    "volume": "Volume: DoMe can't read this PC's volume, so your phone can't show or change it.",
    "media": "Media: DoMe can't see what is playing, so your phone can't show or control it.",
    "session": "Lock screen: DoMe can't tell whether this PC is locked.",
}


def _problem_lines(status: Mapping[str, Any]) -> list[str]:
    """``bridge_unavailable_reason``, ``control_unavailable_reason`` and ``read_failures`` in plain words, each
    followed by the technical detail for a support request."""
    lines: list[str] = []
    bridge = status.get("bridge_unavailable_reason")
    if bridge:
        lines.append(
            "YouTube: the browser extension can't connect to DoMe right now, so your phone can't control "
            "YouTube. Quit any older DoMe in Task Manager, or sign out of Windows and back in."
        )
        lines.append(f"    detail: {bridge}")
    control = status.get("control_unavailable_reason")
    if control:
        lines.append(
            "Commands: `dome-agent` commands on this PC (and opening DoMe a second time) can't reach this DoMe "
            "right now. Quit any older DoMe in Task Manager, or sign out of Windows and back in."
        )
        lines.append(f"    detail: {control}")
    for source, failure in sorted((status.get("read_failures") or {}).items()):
        lines.append(_READ_FAILURE_TEXT.get(source, f"{source}: DoMe can't read this right now."))
        what = failure.get("code") or failure.get("error") or "error"
        detail = failure.get("message") or (f"OS error {failure['os_error']}" if "os_error" in failure else "")
        lines.append(
            f"    detail: failing since {failure.get('since')} ({failure.get('failures', 1)} tries): {what}"
            + (f": {detail}" if detail else "")
        )
    return lines


def _status_text(agent: Any) -> str:
    status = agent.status()
    ident = status.get("identity", {})
    lines = [
        f"DoMe agent {status.get('agent_version')} (pid {os.getpid()})",
        f"PC: {ident.get('pc_name')}  linked: {ident.get('linked')}",
        f"connection: {status.get('connection')}  remote control: {'on' if status['store'].get('remote_enabled') else 'OFF'}",
        f"extension: {status.get('extension_connected')}",
        "",
    ]
    problems = _problem_lines(status)
    lines.extend(["Problems DoMe found:", *(f"  {line}" for line in problems)] if problems else ["No problems found."])
    lines.extend(["", "Paired phones:"])
    for g in status.get("grants", []):
        if g.get("revoked_at"):
            continue
        lines.append(f"  {g.get('display_name')}: {', '.join(g.get('capabilities', []))}")
    session = (status.get("input") or {}).get("session")
    if session:
        lines.append("")
        lines.append(
            f"Manual input session: {session['state']} (pointer={session['pointer']}, keyboard={session['keyboard']})"
        )
    lines.append("")
    lines.append("Use the tray menu for pairing, apps, permissions, Disable remote control and Quit.")
    return "\n".join(lines)


def startup_command() -> str:
    """Command written to the HKCU Run key: the frozen executable, or the module entry in a venv run by
    the venv's windowless ``pythonw.exe``. Never ``python.exe``: it is a console program, so Windows would
    open a console window at sign-in, and closing it would stop DoMe."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" run'
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe") if interpreter.name else None
    if windowless is not None and windowless.is_file():
        return f'"{windowless}" -m dome_agent.cli run'
    if sys.platform == "win32":
        log.error("start at login not registered: no pythonw.exe next to the Python interpreter")
        raise RuntimeError("pythonw.exe was not found next to the Python interpreter")
    return f'"{interpreter}" -m dome_agent.cli run'


def host_executable_path() -> Path:
    """Location of dome-native-host next to the agent executable (frozen) or the venv script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("dome-native-host.exe")
    scripts = Path(sys.executable).parent
    candidate = scripts / ("dome-native-host.exe" if sys.platform == "win32" else "dome-native-host")
    return candidate

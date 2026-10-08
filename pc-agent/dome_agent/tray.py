"""Tray application (pystray) with tkinter windows for pairing — the Windows desktop surface.

Thread model: the pystray icon owns the main thread (its message loop), the asyncio agent runs in a
background thread, and a dedicated Tk thread owns every tkinter object (requests arrive through a
queue). Tray menu actions are posted to the agent loop with ``run_coroutine_threadsafe``.
``DOME_AGENT_HEADLESS=1`` skips all of this (:func:`dome_agent.cli` runs the agent directly) so the
agent works on servers, in CI and on Linux without a display. pystray/tkinter/Pillow are imported
lazily so importing this module never needs a display.

Menu: status line · **Disable/Enable remote control** (local flag; no remote frame can change it) ·
Pair a phone… · Approved apps… · Start at login · Reconnect · Diagnostics… · Quit.
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .logsetup import get_logger
from .settings import Settings
from .ui import PairingApproval, PairingDisplay, StatusView

log = get_logger(__name__)

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
        self._root = tk.Tk()
        self._root.withdraw()
        self._root.title("DoMe")
        self._ready.set()
        self._pump()
        self._root.mainloop()

    def _pump(self) -> None:
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                log.warning("tk callback failed", error=exc.__class__.__name__)
        if self._root is not None:
            self._root.after(100, self._pump)

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

    # ----- wiring ---------------------------------------------------------------------------------------------
    def bind(self, agent: Any, loop: asyncio.AbstractEventLoop) -> None:
        self.agent = agent
        self.loop = loop

    def _call(self, coro: Any) -> None:
        if self.loop is None:
            coro.close()
            return
        asyncio.run_coroutine_threadsafe(coro, self.loop)

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
        tk.Label(
            win, text="Requested permissions: " + ", ".join(approval.requested_capabilities), font=("Segoe UI", 10)
        ).pack(padx=16, pady=(8, 12))
        buttons = tk.Frame(win)
        buttons.pack(padx=16, pady=(0, 16))

        def approve() -> None:
            if self.agent is not None and self.agent.pairing is not None:
                self._call(self.agent.pairing.approve(approval.pairing_id))

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

        def apps(icon: Any, item: Any) -> None:
            if self.agent is None:
                return
            rows = self.agent.apps.list()
            text = "Approved applications (add with: dome-agent approve-app <app_id> <path to .exe>)\n\n" + "\n".join(
                f"{r.app_id:20} {r.display_name:24} {r.exe_path}" for r in rows
            )
            self._tk.post(lambda: self._show_text_window("Approved apps", text))

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
                self.agent.platform.startup.set_start_at_login(enabled, startup_command())
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

            path = write_bundle(self.settings, self.agent.status())
            self.notify("DoMe diagnostics saved", str(path))

        def quit_app(icon: Any, item: Any) -> None:
            if self.agent is not None:
                self.agent.request_stop()
            icon.stop()

        return pystray.Menu(
            pystray.MenuItem(status_item, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(toggle_label, toggle_remote),
            pystray.MenuItem("Pair a phone…", pair),
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


def startup_command() -> str:
    """Command written to the HKCU Run key: the frozen executable, or the module entry in a venv."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" run'
    return f'"{sys.executable}" -m dome_agent.cli run'


def host_executable_path() -> Path:
    """Location of dome-native-host next to the agent executable (frozen) or the venv script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("dome-native-host.exe")
    scripts = Path(sys.executable).parent
    candidate = scripts / ("dome-native-host.exe" if sys.platform == "win32" else "dome-native-host")
    return candidate

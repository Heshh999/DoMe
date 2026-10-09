"""``dome-agent`` command line.

    run                     start the agent (tray; headless with DOME_AGENT_HEADLESS=1 or --headless)
    link                    link this PC to a DoMe account (opens the browser; --no-browser prints the code)
    unlink                  forget the account link and credential (local grants are kept)
    pair                    show a pairing code + QR, wait for the phone and approve it
    pair-approve ID         approve a pending pairing request (headless)
    pair-decline ID         decline a pending pairing request
    status                  show link / connection / grants / entitlement
    enable | disable        local "remote control" switch (disable wins over every remote command)
    approve-app ID PATH     approve a local .exe for remote launch/focus/close
    remove-app ID           remove an approval
    revoke CONTROLLER_ID    revoke a paired phone locally
    grant CONTROLLER_ID [--pointer] [--keyboard] [--remove-pointer] [--remove-keyboard]
                            allow/withdraw manual touchpad (pointer) / keyboard input for a paired phone
    stop-input              end the live manual-input session on this PC and release what it holds
    install-native-host     register the Chrome/Edge native-messaging manifest (per user)
    uninstall-native-host
    repair                  re-register the native host and clean stale local endpoints; keeps identity,
                            credential, grants and approved apps; never kills a process
    reconnect               manual reconnect (after another agent superseded this one)
    diagnostics             write a redacted diagnostics bundle
    version

``run`` is single-instance per Windows user session: a second launch asks the running agent to show
its window and exits. Commands that need the running agent use the local control channel;
status/enable/disable/approve-app/revoke/grant fall back to the local store when the agent is not running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dome_protocol import ProtocolError

from . import __version__
from .logsetup import configure_logging, get_logger
from .settings import Settings, load_settings

log = get_logger(__name__)


def _settings(args: argparse.Namespace) -> Settings:
    state_dir = Path(args.state_dir).expanduser() if getattr(args, "state_dir", None) else None
    settings = load_settings(state_dir=state_dir)
    settings.ensure_dirs()
    return settings


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _control(settings: Settings) -> Any:
    from .control import ControlClient

    return ControlClient(settings.state_dir)


# ----- run ------------------------------------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    configure_logging(settings.log_level, settings.log_path)
    headless = settings.headless or bool(args.headless)
    from .single_instance import InstanceLock, describe_other_session, other_session_agent

    other = other_session_agent(settings.state_dir)
    if other is not None:
        # Same account, another Windows session: it owns this account's identity. Refuse; never compete.
        print(describe_other_session(other))
        log.warning("refusing to start: an agent of this account runs in another Windows session")
        return 1
    lock = InstanceLock.acquire(settings.state_dir)
    if lock is None:
        return _second_launch(settings)
    try:
        return _run_locked(args, settings, headless)
    finally:
        lock.release()


def _second_launch(settings: Settings) -> int:
    """Another agent of this user session holds the instance lock (spec §10): bring it up instead of
    competing with it. Never terminates anything."""
    from .control import ControlError
    from .single_instance import inspect

    try:
        shown = _control(settings).call("show")
        pid = shown.get("pid")
        print(f"DoMe is already running in this session (pid {pid}); its window was brought up. Nothing else started.")
        log.info("second launch: running instance asked to show itself", pid=pid)
        return 0
    except ControlError as exc:
        report = inspect(settings.state_dir)
        print(report.message)
        if report.lock_held_by_other and not report.control_responding:
            print(f"(control channel: {exc.code}) Run `dome-agent repair` to check the local endpoints.")
        log.warning("second launch: lock held but the running instance did not answer", code=exc.code)
        return 1


def _run_locked(args: argparse.Namespace, settings: Settings, headless: bool) -> int:
    log.info(
        "dome-agent starting",
        version=__version__,
        headless=headless,
        platform=sys.platform,
        fake_platform=settings.use_fake_platform,
    )
    from .agent import Agent

    if headless:
        agent = Agent(settings)

        async def _main() -> None:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, agent.request_stop)
                except (NotImplementedError, RuntimeError):
                    pass
            await agent.run_forever()

        try:
            asyncio.run(_main())
        except KeyboardInterrupt:
            pass
        return 0

    from .tray import TrayUI

    tray = TrayUI(settings)

    def agent_main(ui: TrayUI) -> None:
        agent = Agent(settings, ui=ui)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ui.bind(agent, loop)
        try:
            loop.run_until_complete(agent.run_forever())
        finally:
            loop.close()
            if ui._icon is not None:  # noqa: SLF001
                ui._icon.stop()  # noqa: SLF001

    tray.run(agent_main)
    return 0


# ----- link -----------------------------------------------------------------------------------------------------


def cmd_link(args: argparse.Namespace) -> int:
    settings = _settings(args)
    configure_logging(settings.log_level, settings.log_path)
    api_url = args.api_url or settings.api_url
    if not api_url:
        print("error: set DOME_AGENT_API_URL or pass --api-url", file=sys.stderr)
        return 2
    from .api import ApiClient, LinkStart
    from .identity import Identity
    from .link import LinkError, link_pc
    from .platform import build_platform
    from .store import Store

    identity = Identity(settings.state_dir)
    store = Store(settings.db_path)
    platform = build_platform(settings)

    def present(start: LinkStart) -> None:
        print("To link this PC, sign in to DoMe and enter this code:")
        print(f"\n    {start.user_code}\n")
        print(f"Or open: {start.verification_uri_complete}")
        print(f"The code expires in {start.expires_in // 60} minutes. Waiting for approval…")
        sys.stdout.flush()

    async def _main() -> int:
        api = ApiClient(api_url)
        try:
            outcome = await link_pc(
                api,
                identity,
                store,
                platform=platform.reported_platform,
                pc_name_hint=args.name,
                open_browser=not args.no_browser,
                present=present,
                enable_remote=True,
                max_wait_seconds=args.timeout,
            )
        except LinkError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        finally:
            await api.close()
        print(f"Linked as '{outcome.pc_name}' (pc_id {outcome.pc_id}). Remote control is enabled locally.")
        if not outcome.enabled:
            print("Note: this PC is not enabled on your plan yet (device limit). Choose it in Devices or upgrade.")
        return 0

    try:
        return asyncio.run(_main())
    finally:
        store.close()


def cmd_unlink(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .identity import Identity

    identity = Identity(settings.state_dir)
    if not identity.link and identity.read_credential() is None:
        print("This PC is not linked.")
        return 0
    identity.unlink()
    print("Unlinked. Local grants were kept for inspection; pair again after re-linking.")
    return 0


# ----- pairing ----------------------------------------------------------------------------------------------------


def _wait_for_request(ctl: Any, pairing_id: str, deadline: float) -> dict[str, Any] | None:
    while time.time() < deadline:
        status = ctl.call("pair_status")
        if not status.get("active"):
            return None
        for pending in status.get("pending", []):
            return dict(pending)
        time.sleep(1.0)
    return None


def _print_pending(pending: dict[str, Any]) -> None:
    from .pairing import INPUT_CAPABILITIES, INPUT_SCOPE_EXPLANATION

    print("\nA phone claimed this code.")
    print(f"  Name shown by the phone (untrusted): {pending['display_name']}")
    print(f"  Requested permissions: {', '.join(pending['requested_capabilities'])}")
    if any(c in INPUT_CAPABILITIES for c in pending["requested_capabilities"]):
        print(f"\n  About touchpad/keyboard: {INPUT_SCOPE_EXPLANATION}")
    print(f"\n  Check that the phone shows this verification code:  {pending['verification_code']}\n")


def _choose_capabilities(
    pending: dict[str, Any], assume_yes: bool, explicit_input: frozenset[str] = frozenset()
) -> list[str]:
    """The requested non-input capabilities are granted with the approval; pointer and keyboard are
    offered one by one so the PC owner decides explicitly (spec §10A-D). ``--yes`` answers only the
    general approval: pointer/keyboard are granted without a prompt ONLY when named explicitly
    (``--pointer`` / ``--keyboard``), never implied by ``--yes``."""
    from .pairing import INPUT_CAPABILITIES

    requested = list(pending["requested_capabilities"])
    granted = [c for c in requested if c not in INPUT_CAPABILITIES]
    labels = {"pointer": "touchpad / mouse (pointer)", "keyboard": "keyboard (typing, keys, shortcuts)"}
    for cap in INPUT_CAPABILITIES:
        if cap not in requested:
            continue
        if cap in explicit_input:
            granted.append(cap)
            print(f"  {labels[cap]}: allowed (--{cap})")
        elif assume_yes:
            print(f"  {labels[cap]}: NOT allowed (--yes does not cover it; pass --{cap} to allow)")
        elif _confirm(f"  Allow {labels[cap]} for this phone? [y/N] ", False):
            granted.append(cap)
    return granted


def _explicit_input(args: argparse.Namespace) -> frozenset[str]:
    return frozenset(c for c in ("pointer", "keyboard") if getattr(args, c, False))


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def cmd_pair(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError
    from .ui import qr_ascii

    ctl = _control(settings)
    try:
        started = ctl.call("pair_start")
    except ControlError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    if args.print_code:
        print(started["code"])
        print(f"pairing_id={started['pairing_id']}")
    else:
        print("On your phone, open DoMe → Pair a PC and scan this code or type it in:\n")
        print(qr_ascii(started["qr_url"]))
        print(f"    {started['code']}\n")
    print(f"Pairing id {started['pairing_id']}, expires at {started['expires_at']}.")
    if args.no_wait:
        return 0
    print("Waiting for the phone…")
    sys.stdout.flush()
    deadline = time.time() + args.timeout
    try:
        pending = _wait_for_request(ctl, started["pairing_id"], deadline)
    except ControlError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    if pending is None:
        print("No phone claimed the code before it expired.")
        return 1
    _print_pending(pending)
    if not _confirm("Approve this phone? [y/N] ", args.yes):
        ctl.call("pair_decline", pairing_id=pending["pairing_id"])
        print("Declined.")
        return 1
    granted = _choose_capabilities(pending, args.yes, _explicit_input(args))
    result = ctl.call("pair_approve", pairing_id=pending["pairing_id"], capabilities=granted)
    print(f"Approved '{result['display_name']}' with: {', '.join(granted)}. The phone can control this PC now.")
    return 0


def cmd_pair_approve(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    ctl = _control(settings)
    try:
        status = ctl.call("pair_status")
        pending = next((p for p in status.get("pending", []) if p["pairing_id"] == args.pairing_id), None)
        if pending is None:
            print("error: no pending pairing request with that id", file=sys.stderr)
            return 1
        _print_pending(pending)
        if not _confirm("Approve this phone? [y/N] ", args.yes):
            print("Not approved.")
            return 1
        granted = (
            args.capabilities
            if args.capabilities is not None
            else _choose_capabilities(pending, args.yes, _explicit_input(args))
        )
        result = ctl.call("pair_approve", pairing_id=args.pairing_id, capabilities=granted)
    except ControlError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print(f"Approved '{result['display_name']}' (kid {result['kid'][:8]}…).")
    return 0


def cmd_pair_decline(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        _control(settings).call("pair_decline", pairing_id=args.pairing_id)
    except ControlError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print("Declined.")
    return 0


# ----- local state --------------------------------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        status = _control(settings).call("status")
        status["agent_running"] = True
    except ControlError:
        from .identity import Identity
        from .store import Store

        store = Store(settings.db_path)
        try:
            status = {
                "agent_running": False,
                "agent_version": __version__,
                "identity": Identity(settings.state_dir).status_summary(),
                "store": store.summary(),
                "grants": [
                    {
                        "controller_id": g.controller_id,
                        "display_name": g.display_name,
                        "capabilities": list(g.capabilities),
                        "revoked_at": g.revoked_at,
                    }
                    for g in store.list_grants(include_revoked=True)
                ],
            }
        finally:
            store.close()
    from .single_instance import inspect

    instance = inspect(settings.state_dir) if not status["agent_running"] else None
    if instance is not None:
        status["instance"] = instance.as_dict()
    if args.json:
        _print(status)
        return 0
    ident = status.get("identity", {})
    print(f"DoMe agent {status.get('agent_version')} — {'running' if status['agent_running'] else 'not running'}")
    if instance is not None and (
        instance.lock_held_by_other
        or instance.permission_problem
        or instance.other_session_conflict
        or instance.control_endpoint_stale
        or instance.pid_file_stale
    ):
        print(f"  INSTANCE: {instance.message}")
    print(f"  linked: {ident.get('linked')}  pc_id: {ident.get('pc_id')}  name: {ident.get('pc_name')}")
    if status["agent_running"]:
        print(
            f"  connection: {status.get('connection')}  snapshot: {status.get('snapshot_received')}  extension: {status.get('extension_connected')}"
        )
        print(f"  entitlement: {status.get('entitlement', {}).get('effective_plan')}")
        if status.get("relink_required"):
            print(f"  RE-LINK REQUIRED ({status.get('relink_reason')}): run `dome-agent link`")
        if status.get("configuration_error"):
            print(f"  CONFIGURATION ERROR: {status['configuration_error']}")
        if status.get("pending_revocations"):
            print(f"  revocations not yet delivered to the service: {len(status['pending_revocations'])}")
        if status.get("pending_grant_updates"):
            print(f"  permission changes not yet delivered to the service: {len(status['pending_grant_updates'])}")
        session = (status.get("input") or {}).get("session")
        if session:
            print(
                f"  manual input: {session['state']} for {session['controller_id']} (pointer={session['pointer']}, "
                f"keyboard={session['keyboard']}, held={session['held_buttons'] + session['held_keys']})"
            )
    print(f"  remote control: {'ENABLED' if status['store'].get('remote_enabled') else 'disabled'}")
    print(f"  paired phones: {len([g for g in status.get('grants', []) if not g.get('revoked_at')])}")
    for g in status.get("grants", []):
        flag = "revoked" if g.get("revoked_at") else "active"
        print(
            f"    - {g.get('display_name')!r} [{flag}] caps={','.join(g.get('capabilities', []))} id={g.get('controller_id')}"
        )
    return 0


def _set_enabled(args: argparse.Namespace, enabled: bool) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        _control(settings).call("enable" if enabled else "disable")
    except ControlError:
        from .store import Store

        store = Store(settings.db_path)
        try:
            store.set_remote_enabled(enabled)
        finally:
            store.close()
    print(f"Remote control {'enabled' if enabled else 'DISABLED'} on this PC.")
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    return _set_enabled(args, True)


def cmd_disable(args: argparse.Namespace) -> int:
    return _set_enabled(args, False)


def cmd_approve_app(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        row = _control(settings).call("approve_app", app_id=args.app_id, exe_path=args.exe_path, display_name=args.name)
    except ControlError as exc:
        if exc.code != "AGENT_NOT_RUNNING":
            print(f"error: {exc.message}", file=sys.stderr)
            return 1
        from .approved_apps import ApprovalError, ApprovedApps
        from .store import Store

        store = Store(settings.db_path)
        try:
            r = ApprovedApps(store).approve(args.app_id, args.exe_path, args.name)
            row = {
                "app_id": r.app_id,
                "display_name": r.display_name,
                "exe_path": r.exe_path,
                "exe_sha256": r.exe_sha256,
            }
        except ApprovalError as exc2:
            print(f"error: {exc2}", file=sys.stderr)
            return 1
        finally:
            store.close()
    print(f"Approved {row['app_id']} → {row['exe_path']} (sha256 {row['exe_sha256'][:12]}…)")
    return 0


def cmd_remove_app(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        removed = _control(settings).call("remove_app", app_id=args.app_id)["removed"]
    except ControlError:
        from .approved_apps import ApprovedApps
        from .store import Store

        store = Store(settings.db_path)
        try:
            removed = ApprovedApps(store).remove(args.app_id)
        finally:
            store.close()
    print("Removed." if removed else "No such approval.")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        ok = _control(settings).call("revoke", controller_id=args.controller_id)["revoked"]
    except ControlError:
        from .store import Store

        store = Store(settings.db_path)
        try:
            ok = store.revoke_grant_locally(args.controller_id, "local_revocation") is not None
        finally:
            store.close()
        if ok:
            print(
                "Revoked locally; this PC refuses the phone from now on. The agent is not running, so the DoMe "
                "service has NOT been told yet: it will send revoke_controller the next time it connects."
            )
            return 0
    print("Revoked." if ok else "No such controller.")
    return 0 if ok else 1


def cmd_grant(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError
    from .pairing import INPUT_SCOPE_EXPLANATION

    add = [c for c, flag in (("pointer", args.pointer), ("keyboard", args.keyboard)) if flag]
    remove = [c for c, flag in (("pointer", args.remove_pointer), ("keyboard", args.remove_keyboard)) if flag]
    if not add and not remove:
        print("error: nothing to change (use --pointer/--keyboard/--remove-pointer/--remove-keyboard)", file=sys.stderr)
        return 2
    if set(add) & set(remove):
        print("error: a capability cannot be added and removed at once", file=sys.stderr)
        return 2
    if add:
        print(f"About touchpad/keyboard access: {INPUT_SCOPE_EXPLANATION}\n")
    try:
        result = _control(settings).call("grant", controller_id=args.controller_id, add=add, remove=remove)
        caps = result["capabilities"]
        pending = bool(result.get("pending_relay_update"))
    except ControlError as exc:
        if exc.code != "AGENT_NOT_RUNNING":
            print(f"error: {exc.message}", file=sys.stderr)
            return 1
        from .store import Store

        store = Store(settings.db_path)
        try:
            grant = store.get_grant(args.controller_id)
            if grant is None or grant.revoked:
                print("error: no active paired phone with that controller id", file=sys.stderr)
                return 1
            caps_list = [c for c in grant.capabilities if c not in remove] + [
                c for c in add if c not in grant.capabilities
            ]
            try:
                row = store.update_grant_capabilities(args.controller_id, caps_list)
            except ValueError as exc2:
                print(f"error: {exc2}", file=sys.stderr)
                return 1
            assert row is not None
            caps = list(row.capabilities)
        finally:
            store.close()
        pending = True
        print(
            "The agent is not running, so the DoMe service has NOT been told yet: it sends grant_update when it connects."
        )
    print(f"Permissions for {args.controller_id}: {', '.join(caps)}" + (" (service update pending)" if pending else ""))
    return 0


def cmd_stop_input(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        result = _control(settings).call("input_stop")
    except ControlError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print(f"Manual input stopped; released {result['released_holds']} held button(s)/key(s).")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .single_instance import repair

    host_path = Path(args.host_path).resolve() if args.host_path else None
    for line in repair(settings.state_dir, host_path=host_path, dev_extension_id=settings.dev_extension_id):
        print(line)
    print("Repair finished. Pairing, grants and approved apps were preserved; nothing was terminated.")
    return 0


def cmd_reconnect(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError

    try:
        _control(settings).call("reconnect")
    except ControlError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return 1
    print("Reconnect requested.")
    return 0


def cmd_diagnostics(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .control import ControlError
    from .diagnostics import write_bundle

    try:
        path = _control(settings).call("diagnostics")["path"]
    except ControlError:
        from .identity import Identity
        from .store import Store

        store = Store(settings.db_path)
        try:
            status = {
                "agent_running": False,
                "identity": Identity(settings.state_dir).status_summary(),
                "store": store.summary(),
                "security_events": store.list_security_events(50),
            }
        finally:
            store.close()
        path = str(write_bundle(settings, status))
    print(f"Diagnostics written to {path}")
    print("Review it before sharing; it is redacted but contains your PC's configuration summary.")
    return 0


def cmd_install_native_host(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .bridge.manifest import HOST_NAME, allowed_origins, write_manifest
    from .platform import build_platform
    from .tray import host_executable_path

    host_path = Path(args.host_path).resolve() if args.host_path else host_executable_path()
    if not host_path.exists():
        print(f"error: native host executable not found at {host_path} (pass --host-path)", file=sys.stderr)
        return 1
    try:
        origins = allowed_origins(settings.dev_extension_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    manifest_path = write_manifest(settings.state_dir / f"{HOST_NAME}.json", host_path, origins)
    platform = build_platform(settings)
    try:
        written = platform.native_host.install(manifest_path)
    except ProtocolError as exc:
        print(f"Manifest written to {manifest_path}; registry registration skipped: {exc.message}")
        return 0
    print(f"Manifest written to {manifest_path}")
    for location in written:
        print(f"Registered {location}")
    return 0


def cmd_uninstall_native_host(args: argparse.Namespace) -> int:
    settings = _settings(args)
    from .platform import build_platform

    try:
        removed = build_platform(settings).native_host.uninstall()
    except ProtocolError as exc:
        print(f"Nothing to remove here: {exc.message}")
        return 0
    for location in removed:
        print(f"Removed {location}")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    from dome_protocol import load_registry

    print(
        f"dome-agent {__version__} (protocol {load_registry().protocol_version}, registry {load_registry().registry_version})"
    )
    return 0


# ----- parser ---------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dome-agent", description="DoMe Windows agent")
    parser.add_argument(
        "--state-dir", help="override the state directory (default: %%LOCALAPPDATA%%\\DoMe or ~/.local/state/dome)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="run the agent")
    p.add_argument("--headless", action="store_true", help="no tray/windows (same as DOME_AGENT_HEADLESS=1)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("link", help="link this PC to a DoMe account")
    p.add_argument("--api-url", help="DoMe API origin (default: DOME_AGENT_API_URL)")
    p.add_argument("--name", help="suggested PC name")
    p.add_argument("--no-browser", action="store_true", help="do not open the system browser; print the code and link")
    p.add_argument("--timeout", type=int, default=None, help="give up after N seconds (default: code lifetime)")
    p.set_defaults(fn=cmd_link)

    sub.add_parser("unlink", help="forget the account link").set_defaults(fn=cmd_unlink)

    p = sub.add_parser("pair", help="pair a phone")
    p.add_argument(
        "--print-code", action="store_true", help="print the code on one line instead of the QR (scripts/tests)"
    )
    p.add_argument("--no-wait", action="store_true", help="exit after showing the code")
    p.add_argument("--yes", action="store_true", help="approve without asking (tests only; not touchpad/keyboard)")
    p.add_argument("--pointer", action="store_true", help="also allow touchpad / mouse input if the phone asked")
    p.add_argument("--keyboard", action="store_true", help="also allow keyboard input if the phone asked")
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(fn=cmd_pair)

    p = sub.add_parser("pair-approve", help="approve a pending pairing request")
    p.add_argument("pairing_id")
    p.add_argument("--yes", action="store_true", help="approve without asking (not touchpad/keyboard)")
    p.add_argument("--pointer", action="store_true", help="also allow touchpad / mouse input if the phone asked")
    p.add_argument("--keyboard", action="store_true", help="also allow keyboard input if the phone asked")
    p.add_argument("--capabilities", nargs="*", default=None, help="subset of the requested capabilities to grant")
    p.set_defaults(fn=cmd_pair_approve)

    p = sub.add_parser("pair-decline", help="decline a pending pairing request")
    p.add_argument("pairing_id")
    p.set_defaults(fn=cmd_pair_decline)

    p = sub.add_parser("status", help="show status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    sub.add_parser("enable", help="enable remote control locally").set_defaults(fn=cmd_enable)
    sub.add_parser("disable", help="DISABLE remote control locally (emergency stop)").set_defaults(fn=cmd_disable)

    p = sub.add_parser("approve-app", help="approve a local application")
    p.add_argument("app_id")
    p.add_argument("exe_path")
    p.add_argument("--name")
    p.set_defaults(fn=cmd_approve_app)

    p = sub.add_parser("remove-app", help="remove an application approval")
    p.add_argument("app_id")
    p.set_defaults(fn=cmd_remove_app)

    p = sub.add_parser("revoke", help="revoke a paired phone locally")
    p.add_argument("controller_id")
    p.set_defaults(fn=cmd_revoke)

    p = sub.add_parser("grant", help="allow or withdraw manual touchpad/keyboard input for a paired phone")
    p.add_argument("controller_id")
    p.add_argument("--pointer", action="store_true", help="allow touchpad / mouse input")
    p.add_argument("--keyboard", action="store_true", help="allow keyboard input")
    p.add_argument("--remove-pointer", action="store_true")
    p.add_argument("--remove-keyboard", action="store_true")
    p.set_defaults(fn=cmd_grant)

    sub.add_parser("stop-input", help="end the live manual-input session and release held input").set_defaults(
        fn=cmd_stop_input
    )

    p = sub.add_parser("repair", help="re-register the native host and clean stale endpoints (keeps pairing)")
    p.add_argument("--host-path", help="path to dome-native-host(.exe)")
    p.set_defaults(fn=cmd_repair)

    p = sub.add_parser("install-native-host", help="register the browser native-messaging host")
    p.add_argument("--host-path", help="path to dome-native-host(.exe)")
    p.set_defaults(fn=cmd_install_native_host)
    sub.add_parser("uninstall-native-host").set_defaults(fn=cmd_uninstall_native_host)

    sub.add_parser("reconnect", help="reconnect to the relay").set_defaults(fn=cmd_reconnect)
    sub.add_parser("diagnostics", help="write a redacted diagnostics bundle").set_defaults(fn=cmd_diagnostics)
    sub.add_parser("version").set_defaults(fn=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fn: Callable[[argparse.Namespace], int] = args.fn
    try:
        return fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

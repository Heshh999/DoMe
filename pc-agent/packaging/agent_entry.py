"""PyInstaller entry point for ``DoMe.exe`` (the tray agent). ``DoMe.exe`` alone means ``run``.

Every exception ends in ``logs/crash.log`` and exit code 70 (dome_agent.crashguard), never in the
bootloader's modal error box: Setup and its uninstaller run ``DoMe.exe quit`` and friends and must
never wait on a box nobody sees. Only the tray started by a person shows a short message instead.
"""

import sys

if __name__ == "__main__":
    argv = sys.argv[1:] or ["run"]

    def _main() -> int:
        from dome_agent.cli import main

        return main(argv)

    from dome_agent.crashguard import run_guarded, wants_message

    sys.exit(run_guarded(_main, program="DoMe", argv=argv, show_message=wants_message(argv)))

"""PyInstaller entry point for ``dome-native-host.exe`` (Chrome/Edge Native Messaging host).

The browser starts it in the background, so an exception must never open the bootloader's modal
error box: dome_agent.crashguard writes it to ``logs/crash.log`` and the process exits with 70.
"""

import sys

if __name__ == "__main__":

    def _main() -> int:
        from dome_agent.bridge.host import main

        return main()

    from dome_agent.crashguard import run_guarded

    sys.exit(run_guarded(_main, program="dome-native-host", argv=sys.argv[1:]))

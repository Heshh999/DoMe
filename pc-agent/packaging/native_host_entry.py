"""PyInstaller entry point for ``dome-native-host.exe`` (Chrome/Edge Native Messaging host)."""

import sys

from dome_agent.bridge.host import main

if __name__ == "__main__":
    sys.exit(main())

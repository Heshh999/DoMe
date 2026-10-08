"""PyInstaller entry point for ``DoMe.exe`` (the tray agent). ``DoMe.exe`` alone means ``run``."""

import sys

from dome_agent.cli import main

if __name__ == "__main__":
    argv = sys.argv[1:] or ["run"]
    sys.exit(main(argv))

"""``python -m dome_agent.screen.worker``: the agent's child process (``DoMe.exe screen-worker`` when frozen)."""

import sys

from dome_agent.screen.worker.main import run_worker_process

if __name__ == "__main__":
    sys.exit(run_worker_process(sys.argv[1:]))

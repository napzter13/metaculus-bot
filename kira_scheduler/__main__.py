"""``python -m kira_scheduler``: run the scheduler in the foreground until SIGTERM."""

import sys

from kira_scheduler.scheduler import main

if __name__ == "__main__":
    sys.exit(main())

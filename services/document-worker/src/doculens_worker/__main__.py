"""Support ``python -m doculens_worker``."""

import sys

from doculens_worker.entrypoint import main

if __name__ == "__main__":
    sys.exit(main())

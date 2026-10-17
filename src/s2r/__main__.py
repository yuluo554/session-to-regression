"""进程入口：`python -m s2r` 与 console script `s2r` 走同一条 main()。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

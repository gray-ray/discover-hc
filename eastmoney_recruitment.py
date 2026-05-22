#!/usr/bin/env python3
"""兼容旧入口，实际实现已拆分到 eastmoney_recruitment_lib。"""

from __future__ import annotations

import sys

from eastmoney_recruitment_lib import main as cli_main
from eastmoney_recruitment_lib.web_server import main as web_main


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "serve":
            sys.argv.pop(1)
            raise SystemExit(web_main())
        raise SystemExit(cli_main())
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

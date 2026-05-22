#!/usr/bin/env python3
"""上市公司招聘洞察 HTML 控制台启动入口。"""

from __future__ import annotations

import sys

from eastmoney_recruitment_lib.web_server import main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

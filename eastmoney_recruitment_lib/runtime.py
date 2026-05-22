"""运行时状态和进度输出。"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable


_STATE = threading.local()


def configure_runtime(
    *,
    show_ssl_warning: bool,
    quiet: bool,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    _STATE.quiet = quiet
    _STATE.show_ssl_warning = show_ssl_warning
    _STATE.progress_callback = progress_callback


def should_show_ssl_warning() -> bool:
    return bool(getattr(_STATE, "show_ssl_warning", False))


def is_quiet() -> bool:
    return bool(getattr(_STATE, "quiet", False))


def progress(message: str) -> None:
    callback = getattr(_STATE, "progress_callback", None)
    if callback is not None:
        callback(message)
    if not is_quiet():
        print(f"[进行中] {message}", file=sys.stderr, flush=True)

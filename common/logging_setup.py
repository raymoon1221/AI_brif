"""표준 로깅 설정 — 모든 모듈이 동일 포맷으로 stderr 에 로그를 남긴다."""
from __future__ import annotations

import logging
import os
import sys

_configured = False


def _force_utf8_streams() -> None:
    """
    Windows 콘솔 기본 인코딩(cp949)에서 이모지/한글 출력 시 UnicodeEncodeError 가
    나는 것을 막는다. stdout/stderr 를 UTF-8 로 재설정(가능한 환경에서만).
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def setup_logging(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    _force_utf8_streams()
    lvl = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, lvl, logging.INFO),
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,   # stdout 은 dry-run JSON 출력용으로 비워둔다.
    )


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)

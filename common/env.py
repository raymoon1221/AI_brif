"""
비밀값 접근 — 오직 환경변수에서만 읽는다. 코드/저장소에는 절대 값을 두지 않는다.

로컬 개발 편의를 위해 프로젝트 루트의 .env 를 (있으면) 한 번 읽어 환경변수로 로드한다.
python-dotenv 의존 없이 최소 파서로 처리한다.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_loaded = False


def _load_dotenv_once() -> None:
    """.env 파일을 가볍게 파싱해 아직 설정되지 않은 키만 os.environ 에 채운다."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            # 이미 실제 환경변수가 있으면(예: CI Secrets) 그것을 우선한다.
            os.environ.setdefault(key, val)
    except OSError:
        pass


def get_secret(name: str, default: str | None = None, required: bool = False) -> str | None:
    """환경변수에서 비밀값을 읽는다. required=True 인데 없으면 명확히 실패."""
    _load_dotenv_once()
    val = os.environ.get(name, default)
    # .env.example 플레이스홀더(연속된 x 6자 이상, 예: 'xxxxxxxx')만 미설정으로 취급한다.
    # 실제 키가 우연히 'xxxx'(4자)를 포함해도 오탐하지 않도록 임계를 높였다.
    if val is not None and (val.strip() == "" or re.search(r"x{6,}", val.lower())):
        val = None
    if required and not val:
        raise RuntimeError(
            f"환경변수 {name} 가 설정되지 않았습니다. .env 또는 GitHub Secrets 를 확인하세요."
        )
    return val


def is_dry_run(cli_override: bool | None = None) -> bool:
    """
    dry-run 여부. 기본값은 안전하게 True.
    우선순위: CLI 인자(--send/--dry-run) > 환경변수 DRY_RUN > 기본 True.
    """
    if cli_override is not None:
        return cli_override
    _load_dotenv_once()
    raw = (os.environ.get("DRY_RUN", "true") or "true").strip().lower()
    return raw not in ("false", "0", "no", "off")

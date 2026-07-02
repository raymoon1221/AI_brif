"""
config.yaml 로더 + 기본값 병합.

설정(비밀값 아님)은 config.yaml 에서, 비밀값(API 키/토큰)은 env 에서 읽는다.
(common/env.py 참고)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as e:  # pragma: no cover
    raise SystemExit("PyYAML 이 필요합니다: pip install -r requirements.txt") from e


# 프로젝트 루트 = 이 파일의 부모의 부모 (common/ 의 상위)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# 누락 키에 대한 최소 기본값(설정 파일이 일부만 있어도 동작하게).
DEFAULTS: dict[str, Any] = {
    "text_sources": {
        "rss": [], "korea_rss": [], "arxiv": {"enabled": False}, "hackernews": {"enabled": False},
        "rss_lookback_hours": 48, "max_items": 50, "korea_max_items": 15,
    },
    "video_sources": {
        "channels": [], "playlists": [],
        "daily_budget_minutes": 480, "per_channel_cap": 3,
        "lookback_hours": 36, "relevance_keywords": [],
    },
    "classifier": {
        "gloss_level": "rare",
        "frontier_keywords": [], "trend_keywords": [],
        "dedup_title_threshold": 0.72,
    },
    "delivery": {
        "mode": "text",              # site_base_url 미설정 시 안전하게 text 로 동작
        "site_base_url": "",
        "publish_dir": "public",
        "title_prefix": "🌏 오늘의 해외 AI 브리핑",
        "section_order": ["TREND", "KOREA", "FRONTIER"],
        "html_max_items": {"TREND": 8, "KOREA": 8, "FRONTIER": 8},
        "max_items_per_track": 5,
        "kakao_text_limit": 200,
        "max_messages": 16,
        "send_interval_sec": 0.5,
    },
    "models": {"text_provider": "gemini", "claude": "claude-sonnet-4-6", "gemini": "gemini-2.5-flash"},
    "paths": {"state_dir": "state", "out_dir": "out"},
}


class Config:
    """점(.) 경로로 접근 가능한 얇은 설정 래퍼."""

    def __init__(self, data: dict[str, Any], path: Path):
        self.data = data
        self.path = path

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    # 자주 쓰는 경로 헬퍼 ----------------------------------------------------
    @property
    def state_dir(self) -> Path:
        return PROJECT_ROOT / self.get("paths.state_dir", "state")

    @property
    def out_dir(self) -> Path:
        return PROJECT_ROOT / self.get("paths.out_dir", "out")


def load_config(path: str | os.PathLike | None = None) -> Config:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(f"config 파일을 찾을 수 없습니다: {p}")
    with open(p, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    merged = _deep_merge(DEFAULTS, raw)
    cfg = Config(merged, p)
    # 상태/출력 디렉터리는 미리 보장한다.
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    return cfg

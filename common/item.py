"""
표준 item 데이터 계약 — 모든 에이전트(수집기/분류기/발송기)가 이 스키마로 주고받는다.

{ "id", "source", "type": "text|video", "title", "url",
  "raw_or_transcript", "summary", "track": "FRONTIER|TREND|null", "collected_at" }

추가(내부) 필드: published_at, duration_sec, channel, meta{}.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# id 생성 시 무시할 트래킹 쿼리 파라미터(재게시·공유로 붙는 노이즈). v= 등 의미 있는 값은 유지.
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "spm"}

# 트랙 상수 — 문자열 오타를 방지하기 위해 한 곳에서 정의한다.
FRONTIER = "FRONTIER"   # 배움: 새 모델·아키텍처·논문·기법·벤치마크 (원리)
TREND = "TREND"         # 활용: 제품·시장·실전 적용·워크플로우 (바로 써먹기)
KOREA = "KOREA"         # 국내 트렌드: 대한민국 국내 AI 소식(소스 지역 기반)
VALID_TRACKS = (FRONTIER, TREND, KOREA)


def now_iso() -> str:
    """현재 시각(UTC) ISO8601 문자열."""
    return datetime.now(timezone.utc).isoformat()


def canon_url(url: str) -> str:
    """중복 제거용 URL 정규화: 스킴/호스트 소문자, 트래킹 파라미터·끝 슬래시 제거.
    (의미 있는 쿼리, 예: youtube 의 v= 는 유지한다.)"""
    try:
        s = urlsplit((url or "").strip())
        if not s.netloc:
            return (url or "").strip().lower()
        scheme = (s.scheme or "https").lower()
        host = s.netloc.lower()
        q = [(k, v) for k, v in parse_qsl(s.query, keep_blank_values=False)
             if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS]
        path = s.path.rstrip("/") or "/"
        return urlunsplit((scheme, host, path, urlencode(q), ""))
    except Exception:  # noqa: BLE001 — 정규화 실패 시 원본 소문자 폴백
        return (url or "").strip().lower()


def make_id(*parts: str) -> str:
    """안정적인 중복 제거 키. URL 부분은 정규화해 제목 변동/트래킹 파라미터에 둔감하게 만든다."""
    keyparts = []
    for p in parts:
        if not p:
            continue
        keyparts.append(canon_url(p) if "://" in p else p.strip().lower())
    raw = "||".join(keyparts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Item:
    source: str
    type: str                       # "text" | "video"
    title: str
    url: str
    raw_or_transcript: str = ""      # 텍스트 본문 / 영상은 빈 문자열(Gemini가 URL 직접 분석)
    summary: str = ""                # 한국어 요약
    track: Optional[str] = None      # FRONTIER | TREND | None
    id: str = ""
    collected_at: str = field(default_factory=now_iso)

    # --- 내부/부가 필드(계약 외, 랭킹·중복 제거 보조) ---
    published_at: Optional[str] = None
    duration_sec: Optional[int] = None
    channel: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            # URL 이 있으면 정규화 URL 만으로 id 생성(제목 변동·재게시에도 동일 id → 중복 차단).
            self.id = make_id(self.url) if self.url else make_id(self.title or "")
        if self.type not in ("text", "video"):
            raise ValueError(f"item.type must be 'text' or 'video', got {self.type!r}")

    # 직렬화 ----------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Item":
        # 알 수 없는 키는 meta 로 흡수해 스키마 진화에 견디게 한다.
        known = {f for f in Item.__dataclass_fields__}  # type: ignore[attr-defined]
        base = {k: v for k, v in d.items() if k in known}
        extra = {k: v for k, v in d.items() if k not in known}
        item = Item(**base)
        if extra:
            item.meta.update(extra)
        return item


def validate_track(track: Optional[str]) -> bool:
    return track in VALID_TRACKS

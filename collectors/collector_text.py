"""
collector-text : 텍스트 소스 수집기 (RSS / arXiv / Hacker News)

- 입력: config.yaml 의 text_sources.
- 출력: 표준 item 리스트(type="text", summary 는 빈 문자열 — 요약은 classifier 가 채움).
- 격리: 소스 하나가 죽어도(네트워크/파싱 오류) 그 소스만 건너뛰고 부분 결과를 반환한다.
- dedup: state/seen_text.json 으로 기존 발송분을 제거한다.

단독 실행:
    python -m collectors.collector_text --dry-run
    python -m collectors.collector_text --config config/config.yaml --no-dedup
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any

# 공통 모듈 (패키지 실행/직접 실행 모두 지원)
try:
    from common.config import Config, load_config
    from common.item import Item
    from common.logging_setup import get_logger
    from common.state import SeenStore
except ModuleNotFoundError:  # 직접 실행 대비 sys.path 보정
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from common.config import Config, load_config
    from common.item import Item
    from common.logging_setup import get_logger
    from common.state import SeenStore

log = get_logger("collector-text")

# 선택적 의존성 — 없으면 명확히 경고하고 해당 수집만 건너뛴다.
try:
    import requests
except ImportError:
    requests = None  # type: ignore
try:
    import feedparser
except ImportError:
    feedparser = None  # type: ignore

_HTTP_TIMEOUT = 20
_UA = {"User-Agent": "AI-brief-collector/1.0 (+https://example.invalid)"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _within(published_struct, lookback_hours: int) -> bool:
    """feedparser 의 published_parsed(struct_time)가 lookback 이내인지."""
    if not published_struct:
        return True  # 시각 불명이면 보수적으로 포함
    try:
        dt = datetime(*published_struct[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return dt >= _now() - timedelta(hours=lookback_hours)


def _struct_to_iso(published_struct) -> str | None:
    if not published_struct:
        return None
    try:
        return datetime(*published_struct[:6], tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _fetch_feed(url: str):
    """requests 로 타임아웃 제어 후 feedparser 로 파싱."""
    if feedparser is None:
        raise RuntimeError("feedparser 미설치 (pip install feedparser)")
    if requests is not None:
        resp = requests.get(url, timeout=_HTTP_TIMEOUT, headers=_UA)
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    return feedparser.parse(url)  # 폴백


# --- 소스별 수집 함수 (각각 예외를 자체 처리하지 않고 호출부에서 격리) ---

def _collect_rss(name: str, url: str, lookback_hours: int) -> list[Item]:
    """RSS 피드 수집. 다수 피드가 전체 이력을 반환하므로 lookback 윈도로 최신만 남긴다.
    (날짜가 없는 항목은 보수적으로 포함한다.)"""
    feed = _fetch_feed(url)
    items: list[Item] = []
    for e in feed.entries:
        title = (getattr(e, "title", "") or "").strip()
        link = (getattr(e, "link", "") or "").strip()
        if not title or not link:
            continue
        pub = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if not _within(pub, lookback_hours):
            continue  # 윈도 밖(오래된 글) → 신규 아님
        raw = getattr(e, "summary", "") or ""
        if hasattr(e, "content") and e.content:
            raw = e.content[0].get("value", raw)
        items.append(Item(
            source=name, type="text", title=title, url=link,
            raw_or_transcript=raw[:8000],
            published_at=_struct_to_iso(pub),
        ))
    return items


def _collect_arxiv(cfg: dict) -> list[Item]:
    categories = cfg.get("categories", ["cs.AI"])
    max_results = int(cfg.get("max_results", 15))
    lookback = int(cfg.get("lookback_hours", 48))
    items: list[Item] = []
    for cat in categories:
        url = (
            "http://export.arxiv.org/api/query?"
            f"search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending"
            f"&max_results={max_results}"
        )
        feed = _fetch_feed(url)
        for e in feed.entries:
            if not _within(getattr(e, "published_parsed", None), lookback):
                continue
            title = (getattr(e, "title", "") or "").strip().replace("\n", " ")
            link = (getattr(e, "link", "") or "").strip()
            abstract = (getattr(e, "summary", "") or "").strip()
            authors = ", ".join(a.get("name", "") for a in getattr(e, "authors", []) or [])
            items.append(Item(
                source=f"arXiv {cat}", type="text", title=title, url=link,
                raw_or_transcript=f"Authors: {authors}\n\nAbstract: {abstract}"[:8000],
                published_at=_struct_to_iso(getattr(e, "published_parsed", None)),
                meta={"category": cat},
            ))
        time.sleep(0.3)  # arXiv 예의상 간격
    return items


def _collect_hackernews(cfg: dict) -> list[Item]:
    if requests is None:
        raise RuntimeError("requests 미설치")
    keywords = cfg.get("keywords", [])
    min_points = int(cfg.get("min_points", 50))
    lookback = int(cfg.get("lookback_hours", 48))
    since_ts = int((_now() - timedelta(hours=lookback)).timestamp())
    seen_obj: set[str] = set()
    items: list[Item] = []
    for kw in keywords:
        url = (
            "http://hn.algolia.com/api/v1/search_by_date?"
            f"query={requests.utils.quote(kw)}&tags=story"
            f"&numericFilters=points>={min_points},created_at_i>{since_ts}"
        )
        resp = requests.get(url, timeout=_HTTP_TIMEOUT, headers=_UA)
        resp.raise_for_status()
        for hit in resp.json().get("hits", []):
            obj = hit.get("objectID")
            if not obj or obj in seen_obj:
                continue
            seen_obj.add(obj)
            title = (hit.get("title") or hit.get("story_title") or "").strip()
            if not title:
                continue
            story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={obj}"
            body = hit.get("story_text") or ""
            items.append(Item(
                source="Hacker News", type="text", title=title, url=story_url,
                raw_or_transcript=(f"{title}\n\n{body}")[:8000],
                published_at=hit.get("created_at"),
                meta={"points": hit.get("points"), "hn_url": f"https://news.ycombinator.com/item?id={obj}"},
            ))
        time.sleep(0.2)
    return items


def collect(cfg: Config, use_dedup: bool = True) -> list[Item]:
    """모든 텍스트 소스를 수집해 표준 item 리스트를 반환(부분 실패 격리)."""
    src = cfg.get("text_sources", {}) or {}
    rss_lookback = int(src.get("rss_lookback_hours", 48))
    max_items = int(src.get("max_items", 50))
    collected: list[Item] = []

    # 1) RSS (lookback 윈도로 최신만)
    for entry in src.get("rss", []) or []:
        name, url = entry.get("name", "RSS"), entry.get("url")
        if not url:
            continue
        try:
            got = _collect_rss(name, url, rss_lookback)
            log.info("RSS '%s' → %d건(최근 %dh)", name, len(got), rss_lookback)
            collected.extend(got)
        except Exception as e:  # noqa: BLE001 — 소스 단위 격리
            log.warning("RSS '%s' 수집 실패(건너뜀): %s", name, e)

    # 2) arXiv
    arxiv_cfg = src.get("arxiv", {}) or {}
    if arxiv_cfg.get("enabled"):
        try:
            got = _collect_arxiv(arxiv_cfg)
            log.info("arXiv → %d건", len(got))
            collected.extend(got)
        except Exception as e:  # noqa: BLE001
            log.warning("arXiv 수집 실패(건너뜀): %s", e)

    # 3) Hacker News
    hn_cfg = src.get("hackernews", {}) or {}
    if hn_cfg.get("enabled"):
        try:
            got = _collect_hackernews(hn_cfg)
            log.info("Hacker News → %d건", len(got))
            collected.extend(got)
        except Exception as e:  # noqa: BLE001
            log.warning("Hacker News 수집 실패(건너뜀): %s", e)

    # 중복 제거: 동일 실행 내 중복 + 과거 발송분(state) 제거
    out: list[Item] = []
    in_run: set[str] = set()
    store = SeenStore(cfg.state_dir / "seen_text.json") if use_dedup else None
    for it in collected:
        if it.id in in_run:
            continue
        in_run.add(it.id)
        if store is not None and it.id in store:
            continue
        out.append(it)

    # 최신순 정렬 후 상한 적용 — 요약(Claude) 비용을 일일 예산 수준으로 묶는다.
    out.sort(key=lambda it: it.published_at or "", reverse=True)
    if len(out) > max_items:
        log.info("텍스트 %d건 → 최신 %d건으로 상한 적용", len(out), max_items)
        out = out[:max_items]

    log.info("텍스트 수집 합계: %d건(중복 제거·상한 후)", len(out))
    return out


def mark_seen(cfg: Config, items: list[Item]) -> None:
    """발송 확정된 item 들을 seen 상태에 기록(다음 날 중복 방지)."""
    store = SeenStore(cfg.state_dir / "seen_text.json")
    for it in items:
        if it.type == "text":
            store.add(it.id)
    store.save()


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="텍스트 소스 수집기(단독 실행)")
    ap.add_argument("--config", default=None, help="config.yaml 경로")
    ap.add_argument("--dry-run", action="store_true", help="결과 JSON 을 stdout 으로 출력")
    ap.add_argument("--no-dedup", action="store_true", help="state 기반 중복 제거 비활성화")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    items = collect(cfg, use_dedup=not args.no_dedup)
    payload: list[dict[str, Any]] = [it.to_dict() for it in items]
    # stdout 은 데이터 채널(로그는 stderr).
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    log.info("출력 %d건", len(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

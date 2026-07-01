"""
collector-video : 유튜브 신규 영상 탐지 → (예산 초과 시) 선별 → Gemini 네이티브 URL 요약

처리 순서(스펙 그대로):
  1) 탐지   : 채널/플레이리스트 RSS 로 마지막 처리 시각 이후 신규 영상만 수집(키 불필요).
  2) 예산판정: 신규 총 분량 <= daily_budget 이면 전량 진행(선별 생략).
  3) 선별   : 초과 시에만 메타데이터(채널 신뢰도·제목/설명 적합도·길이)로 랭킹,
              채널당 상한을 지키며 예산을 채울 때까지 상위만 선택. 탈락 사유 로깅.
  4) 요약   : 선택 영상을 Gemini 네이티브 YouTube URL 로 분석 → 한국어 요약 + 트랙을
              "한 번의 호출"로 동시 출력(별도 번역 단계 없음).

격리: 영상 1건 실패가 전체를 중단시키지 않는다(항목 단위 try/except).

단독 실행:
    python -m collectors.collector_video --dry-run            # 키 있으면 요약까지
    python -m collectors.collector_video --detect-only        # 탐지만(요약 호출 안 함)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any

try:
    from common.config import Config, load_config
    from common.env import get_secret
    from common.item import Item, FRONTIER, TREND
    from common.jsonutil import extract_json
    from common.logging_setup import get_logger
    from common.state import SeenStore, write_json
except ModuleNotFoundError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from common.config import Config, load_config
    from common.env import get_secret
    from common.item import Item, FRONTIER, TREND
    from common.jsonutil import extract_json
    from common.logging_setup import get_logger
    from common.state import SeenStore, write_json

log = get_logger("collector-video")

try:
    import requests
except ImportError:
    requests = None  # type: ignore
try:
    import feedparser
except ImportError:
    feedparser = None  # type: ignore

_HTTP_TIMEOUT = 20
_UA = {"User-Agent": "AI-brief-collector/1.0"}
DEFAULT_UNKNOWN_MIN = 12          # 영상 길이를 알 수 없을 때 예산 회계용 가정값(분)
# ISO8601 기간: 주/일(P#W, P#D)과 시/분/초(T...) 모두 처리. (24시간 초과 영상은 P#DT… 형태)
_DURATION_RE = re.compile(
    r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# 1) 탐지 — 채널/플레이리스트 RSS (API 키 불필요)
# --------------------------------------------------------------------------
def _feed_url(channel_id: str | None = None, playlist_id: str | None = None) -> str:
    if channel_id:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    return f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"


def _parse_feed(url: str):
    if feedparser is None:
        raise RuntimeError("feedparser 미설치")
    if requests is not None:
        r = requests.get(url, timeout=_HTTP_TIMEOUT, headers=_UA)
        r.raise_for_status()
        return feedparser.parse(r.content)
    return feedparser.parse(url)


def _detect_source(name: str, trust: int, url: str, since: datetime) -> list[Item]:
    feed = _parse_feed(url)
    items: list[Item] = []
    for e in feed.entries:
        vid = getattr(e, "yt_videoid", None) or getattr(e, "id", "")
        title = (getattr(e, "title", "") or "").strip()
        link = (getattr(e, "link", "") or "").strip()
        if not title or not link:
            continue
        pub = getattr(e, "published_parsed", None)
        pub_dt = datetime(*pub[:6], tzinfo=timezone.utc) if pub else None
        if pub_dt and pub_dt < since:
            continue  # 마지막 처리 시각 이전 → 신규 아님
        desc = ""
        if hasattr(e, "summary"):
            desc = e.summary or ""
        items.append(Item(
            source=name, type="video", title=title, url=link,
            raw_or_transcript="",  # 영상은 Gemini 가 URL 을 직접 읽으므로 비움
            channel=name,
            published_at=pub_dt.isoformat() if pub_dt else None,
            meta={"trust": trust, "video_id": vid, "description": desc[:1000]},
        ))
    return items


def detect_new(cfg: Config, since: datetime) -> list[Item]:
    """모든 채널/플레이리스트에서 since 이후 신규 영상 탐지(소스 단위 격리)."""
    vs = cfg.get("video_sources", {}) or {}
    found: list[Item] = []
    for ch in vs.get("channels", []) or []:
        cid = ch.get("channel_id")
        if not cid:
            continue
        try:
            got = _detect_source(ch.get("name", cid), int(ch.get("trust", 3)),
                                  _feed_url(channel_id=cid), since)
            log.info("채널 '%s' 신규 %d건", ch.get("name", cid), len(got))
            found.extend(got)
        except Exception as e:  # noqa: BLE001
            log.warning("채널 '%s' 탐지 실패(건너뜀): %s", ch.get("name", cid), e)
    for pl in vs.get("playlists", []) or []:
        pid = pl.get("playlist_id")
        if not pid:
            continue
        try:
            got = _detect_source(pl.get("name", pid), int(pl.get("trust", 3)),
                                 _feed_url(playlist_id=pid), since)
            log.info("플레이리스트 '%s' 신규 %d건", pl.get("name", pid), len(got))
            found.extend(got)
        except Exception as e:  # noqa: BLE001
            log.warning("플레이리스트 '%s' 탐지 실패(건너뜀): %s", pl.get("name", pid), e)
    return found


# --------------------------------------------------------------------------
# 영상 길이 — YouTube Data API(키 있으면 정확) / 없으면 가정값
# --------------------------------------------------------------------------
def _iso_duration_to_sec(iso: str) -> int:
    m = _DURATION_RE.fullmatch((iso or "").strip())
    if not m:
        return 0
    w, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((w * 7 + d) * 24 + h) * 3600 + mi * 60 + s


def enrich_durations(items: list[Item], api_key: str | None) -> None:
    """가능하면 YouTube Data API 로 정확한 영상 길이/설명을 채운다(in-place)."""
    if not api_key or requests is None:
        for it in items:
            if it.duration_sec is None:
                it.duration_sec = DEFAULT_UNKNOWN_MIN * 60
                it.meta["duration_estimated"] = True
        log.info("YOUTUBE_API_KEY 없음 → 영상 길이를 가정값 %d분으로 회계", DEFAULT_UNKNOWN_MIN)
        return
    ids = [it.meta.get("video_id") for it in items if it.meta.get("video_id")]
    durations: dict[str, int] = {}
    descs: dict[str, str] = {}
    for i in range(0, len(ids), 50):  # API 는 50개씩 배치
        batch = [x for x in ids[i:i + 50] if x]
        if not batch:
            continue
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "contentDetails,snippet", "id": ",".join(batch), "key": api_key},
                timeout=_HTTP_TIMEOUT,
            )
            r.raise_for_status()
            for v in r.json().get("items", []):
                vid = v.get("id")
                durations[vid] = _iso_duration_to_sec(v.get("contentDetails", {}).get("duration", ""))
                descs[vid] = (v.get("snippet", {}).get("description", "") or "")[:1000]
        except Exception as e:  # noqa: BLE001
            log.warning("YouTube Data API 호출 실패(가정값 사용): %s", e)
    for it in items:
        vid = it.meta.get("video_id")
        dur = durations.get(vid)
        # None(미조회) 또는 0(파싱 실패/빈 값) 모두 가정값으로 폴백하고 추정 표시.
        if not dur:
            it.duration_sec = DEFAULT_UNKNOWN_MIN * 60
            it.meta["duration_estimated"] = True
        else:
            it.duration_sec = dur
        if descs.get(vid):
            it.meta["description"] = descs[vid]


# --------------------------------------------------------------------------
# 2~3) 예산 판정 및 (초과 시) 선별
# --------------------------------------------------------------------------
def _relevance_score(it: Item, keywords: list[str]) -> int:
    text = f"{it.title} {it.meta.get('description', '')}".lower()
    return sum(1 for kw in keywords if kw.lower() in text)


def select_within_budget(cfg: Config, items: list[Item]) -> list[Item]:
    """예산 이하면 전량, 초과면 랭킹·채널상한으로 선별. 탈락 사유 로깅.

    영상 길이를 모르면(YOUTUBE_API_KEY 미설정 등) 모든 항목이 가정값(12분)으로 회계되어
    '분(minute) 예산'만으로는 실제 분량을 보장할 수 없다. 따라서 추정 길이가 섞이면
    '개수(count) 상한'(= 예산분/가정값분)을 함께 강제해 무제한 처리를 막는다.
    """
    vs = cfg.get("video_sources", {}) or {}
    budget_min = int(vs.get("daily_budget_minutes", 480))
    budget_sec = budget_min * 60
    per_channel_cap = int(vs.get("per_channel_cap", 3))
    keywords = vs.get("relevance_keywords", []) or []

    # 추정 길이가 하나라도 있으면 개수 상한을 적용(없으면 분 예산만으로 충분).
    n_estimated = sum(1 for it in items if it.meta.get("duration_estimated"))
    count_cap = max(1, budget_min // DEFAULT_UNKNOWN_MIN) if n_estimated else None

    total = sum(it.duration_sec or 0 for it in items)
    within_minutes = total <= budget_sec
    within_count = (count_cap is None) or (len(items) <= count_cap)
    if within_minutes and within_count:
        log.info("신규 영상 총 %.0f분 ≤ 예산 %.0f분%s → 전량 진행(선별 생략)",
                 total / 60, budget_min,
                 "" if count_cap is None else f", 개수 {len(items)}≤{count_cap}")
        return items

    why = []
    if not within_minutes:
        why.append(f"분량 {total/60:.0f}분 > 예산 {budget_min}분")
    if not within_count:
        why.append(f"개수 {len(items)} > 상한 {count_cap}(길이 추정 {n_estimated}건)")
    log.info("예산 초과(%s) → 메타데이터 랭킹 선별 시작", "; ".join(why))

    # 점수: 채널 신뢰도(가중 큼) + 제목/설명 적합도. 당일 영상은 인기지표 대신 이걸로 판단.
    def score(it: Item) -> float:
        trust = int(it.meta.get("trust", 3))
        rel = _relevance_score(it, keywords)
        return trust * 2.0 + rel * 1.0

    ranked = sorted(items, key=score, reverse=True)
    selected: list[Item] = []
    used_sec = 0
    per_channel: dict[str, int] = {}
    for it in ranked:
        ch = it.channel or it.source
        dur = it.duration_sec or 0
        reason = None
        if per_channel.get(ch, 0) >= per_channel_cap:
            reason = f"채널 상한({per_channel_cap}) 초과"
        elif count_cap is not None and len(selected) >= count_cap:
            reason = f"개수 상한({count_cap}) 초과"
        elif used_sec + dur > budget_sec:
            reason = f"예산 초과(누적 {used_sec/60:.0f}분 + {dur/60:.0f}분)"
        if reason:
            log.info("선별 탈락: '%s' (%s, 점수 %.1f)", it.title[:50], reason, score(it))
            continue
        selected.append(it)
        used_sec += dur
        per_channel[ch] = per_channel.get(ch, 0) + 1
    log.info("선별 완료: %d/%d건, 누적 %.0f분", len(selected), len(items), used_sec / 60)
    return selected


# --------------------------------------------------------------------------
# 4) Gemini 네이티브 URL 요약 (한국어 요약 + 트랙을 한 번의 호출로)
# --------------------------------------------------------------------------
_GEMINI_PROMPT = """당신은 해외 AI 콘텐츠를 한국어로 브리핑하는 분석가입니다.
아래 유튜브 영상을 시청하고(자막이 없어도 영상/음성으로 직접 이해) 다음을 수행하세요.

1) 한국어로 핵심을 2~3문장으로 요약. (영어 영상이어도 요약은 반드시 한국어)
2) 두 트랙 중 하나로 분류:
   - FRONTIER(배움): 새 모델·아키텍처·논문·기법·벤치마크 등 "원리를 배워야 하는" 기술.
     이 경우 요약에 핵심 기여·작동 원리·한계를 담으세요.
   - TREND(활용): 제품 출시·시장 동향·실전 적용·워크플로우·업계 뉴스 등 "바로 써먹는" 내용.
     이 경우 요약에 무엇을·어디에·어떻게 쓰는지를 담으세요.
   애매하면 더 적합한 한쪽으로 반드시 강제 배정하세요.

반드시 아래 JSON 형식만 출력하세요(코드펜스/설명 금지):
{"summary": "한국어 2~3문장 요약", "track": "FRONTIER 또는 TREND", "reason": "분류 근거 한 문장"}
"""


def summarize_video(item: Item, model: str, api_key: str) -> Item:
    """단일 영상을 Gemini 네이티브 YouTube URL 입력으로 요약·분류(한 번의 호출)."""
    from google import genai           # 지역 import: 미설치 시 이 항목만 실패
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model,
        contents=types.Content(parts=[
            types.Part(file_data=types.FileData(file_uri=item.url)),  # ← 네이티브 YouTube URL
            types.Part(text=_GEMINI_PROMPT),
        ]),
    )
    data = extract_json(resp.text or "")
    track = (data.get("track") or "").upper().strip()
    if track not in (FRONTIER, TREND):
        track = FRONTIER  # 모델이 트랙을 못 정하면 배움으로 강제 배정.
    summary = (data.get("summary") or "").strip()
    item.summary = summary or f"(요약 생성 실패) {item.title}"
    item.track = track
    item.meta["classify_reason"] = data.get("reason", "")
    item.meta["summarized_by"] = "gemini"
    return item


# --------------------------------------------------------------------------
# 오케스트레이션
# --------------------------------------------------------------------------
def _save_last_run(cfg: Config) -> None:
    """마지막 처리 시각을 기록(정보/디버깅용). 탐지 윈도는 seen-dedup + lookback 으로 결정하며
    이 값으로 윈도를 좁히지 않는다 — 좁히면 예산/상한에 밀린 영상이 영구 유실되기 때문."""
    write_json(cfg.state_dir / "video_last.json", {"last_run": _now().isoformat()})


def process(cfg: Config, summarize: bool = True, use_dedup: bool = True,
            advance_state: bool = False) -> list[Item]:
    """탐지 → 예산판정/선별 → (선택)요약. 반환: summary+track 채워진 video item 들."""
    vs = cfg.get("video_sources", {}) or {}
    lookback = int(vs.get("lookback_hours", 36))
    # 탐지 윈도 = 최근 lookback 시간. "이미 처리한 영상"은 scalar last_run 이 아니라
    # 항목별 seen-dedup(seen_video.json)으로 기록한다 → 예산/상한에 밀린 영상도
    # 윈도 안에서는 다음 실행에 재고려되고, 발송된 것만 seen 으로 제외된다.
    since = _now() - timedelta(hours=lookback)

    detected = detect_new(cfg, since)

    # 중복 제거(과거 발송분)
    store = SeenStore(cfg.state_dir / "seen_video.json") if use_dedup else None
    fresh: list[Item] = []
    seen_run: set[str] = set()
    for it in detected:
        if it.id in seen_run:
            continue
        seen_run.add(it.id)
        if store is not None and it.id in store:
            continue
        fresh.append(it)
    log.info("신규 영상 %d건(중복 제거 후)", len(fresh))
    if not fresh:
        if advance_state:
            _save_last_run(cfg)
        return []

    # 영상 길이 보강 후 예산 판정/선별
    enrich_durations(fresh, get_secret("YOUTUBE_API_KEY"))
    selected = select_within_budget(cfg, fresh)

    if not summarize:
        log.info("--detect-only / 요약 생략: 선택 %d건 메타데이터만 반환", len(selected))
        if advance_state:
            _save_last_run(cfg)
        return selected

    api_key = get_secret("GEMINI_API_KEY")
    if not api_key:
        # 요약 없는 영상은 다이제스트 품질을 떨어뜨리므로 발송 경로에는 넣지 않는다.
        # (탐지 결과만 보려면 --detect-only 사용)
        log.warning("GEMINI_API_KEY 없음 → 영상 요약 불가. 영상 %d건을 이번 다이제스트에서 제외.",
                    len(selected))
        if advance_state:
            _save_last_run(cfg)
        return []

    model = cfg.get("models.gemini", "gemini-2.5-flash")
    out: list[Item] = []
    for it in selected:
        try:
            out.append(summarize_video(it, model, api_key))
            log.info("Gemini 요약 OK: '%s' → %s", it.title[:50], out[-1].track)
            time.sleep(0.5)  # 요청당 1개 영상 권장 + 과호출 방지
        except Exception as e:  # noqa: BLE001 — 항목 단위 격리
            log.warning("Gemini 요약 실패(이 영상만 건너뜀): '%s' : %s", it.title[:50], e)
    if advance_state:
        _save_last_run(cfg)
    return out


def mark_seen(cfg: Config, items: list[Item]) -> None:
    store = SeenStore(cfg.state_dir / "seen_video.json")
    for it in items:
        if it.type == "video":
            store.add(it.id)
    store.save()


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="유튜브 영상 수집·요약기(단독 실행)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true", help="결과 JSON 출력(상태 미갱신)")
    ap.add_argument("--detect-only", action="store_true", help="Gemini 호출 없이 탐지만")
    ap.add_argument("--no-dedup", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    items = process(cfg, summarize=not args.detect_only, use_dedup=not args.no_dedup,
                    advance_state=False)  # 단독 실행은 상태를 진전시키지 않는다
    json.dump([it.to_dict() for it in items], sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    log.info("출력 %d건", len(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

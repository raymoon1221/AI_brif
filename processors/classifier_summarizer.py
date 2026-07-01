"""
classifier-summarizer : 배움/활용 분류 + 텍스트 요약 + 교차 중복 제거

- 입력: collector-text / collector-video 의 item 리스트.
- 출력: 모든 item 에 track ∈ {FRONTIER, TREND} 와 한국어 summary 가 채워지고,
        텍스트/영상 교차 중복이 제거된 리스트.
- 텍스트 item: Claude 로 한국어 요약 + 분류(한 번의 호출, JSON).
- 영상 item: 이미 Gemini 가 요약·분류했으므로 그대로 두되, 누락 시 폴백 보정.
- 분류 규칙(키워드/임계값)은 config.classifier 에서 운영자가 수정 가능.

단독 실행(파일/stdin 의 item JSON 을 받아 처리):
    python -m processors.classifier_summarizer --in items.json --dry-run
    cat items.json | python -m processors.classifier_summarizer --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from difflib import SequenceMatcher
from typing import Any

try:
    from common.config import Config, load_config
    from common.env import get_secret
    from common.item import Item, FRONTIER, TREND, KOREA, VALID_TRACKS
    from common.jsonutil import extract_json
    from common.glossary import gloss_rules
    from common.logging_setup import get_logger
except ModuleNotFoundError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from common.config import Config, load_config
    from common.env import get_secret
    from common.item import Item, FRONTIER, TREND, KOREA, VALID_TRACKS
    from common.jsonutil import extract_json
    from common.glossary import gloss_rules
    from common.logging_setup import get_logger

log = get_logger("classifier-summarizer")


# --------------------------------------------------------------------------
# 폴백 분류 — 키워드 기반(강제 배정). Claude 미사용/실패 시에도 track 을 보장.
# --------------------------------------------------------------------------
def keyword_classify(item: Item, cfg: Config) -> str:
    c = cfg.get("classifier", {}) or {}
    f_kw = [k.lower() for k in c.get("frontier_keywords", [])]
    t_kw = [k.lower() for k in c.get("trend_keywords", [])]
    text = f"{item.title} {item.raw_or_transcript} {item.meta.get('description', '')}".lower()
    f_score = sum(1 for k in f_kw if k in text)
    t_score = sum(1 for k in t_kw if k in text)
    if f_score != t_score:
        return FRONTIER if f_score > t_score else TREND
    # 동점: arXiv/논문 성격은 배움으로, 그 외는 활용으로 강제 배정.
    return FRONTIER if "arxiv" in item.source.lower() else TREND


def _coerce_track(track: Any, item: Item, cfg: Config) -> str:
    t = (track or "").upper().strip() if isinstance(track, str) else ""
    return t if t in VALID_TRACKS else keyword_classify(item, cfg)


# --------------------------------------------------------------------------
# Claude 텍스트 요약 + 분류 (한 번의 호출, JSON)
# --------------------------------------------------------------------------
def _build_prompt(item: Item, cfg: Config, is_korea: bool = False) -> str:
    c = cfg.get("classifier", {}) or {}
    rules = gloss_rules(str(c.get("gloss_level", "rare")))   # 용어 풀이 수준(none/rare/all)
    body = (item.raw_or_transcript or item.title)[:6000]
    head = f"[제목] {item.title}\n[출처] {item.source}\n[본문]\n{body}"

    if is_korea:
        return (
            "당신은 대한민국 국내 AI 소식을 '일반인'에게 쉽게 풀어주는 큐레이터입니다.\n"
            "아래 국내 기사를 읽고 비개발자도 이해할 쉬운 한국어로 2~3문장 요약하세요.\n"
            f"{rules}"
            "- 누가·무엇을·왜 했는지, 우리 일상이나 업계에 어떤 의미인지 일상어로 담는다.\n\n"
            f"{head}\n\n"
            "아래 JSON 형식만 출력(코드펜스/설명 금지):\n"
            '{"summary": "쉬운 한국어 2~3문장"}'
        )

    f_kw = ", ".join(c.get("frontier_keywords", [])[:10])
    t_kw = ", ".join(c.get("trend_keywords", [])[:10])
    return (
        "당신은 해외 AI 소식을 '일반인'에게 쉽게 풀어주는 큐레이터입니다.\n"
        "아래 글을 읽고 두 가지를 하세요.\n\n"
        f"{head}\n\n"
        f"1) 쉬운 한국어로 2~3문장 요약. (영어 원문이어도 요약은 반드시 한국어)\n"
        f"{rules}"
        "2) 두 트랙 중 하나로 분류(애매하면 더 적합한 쪽으로 강제):\n"
        f"   - FRONTIER(배움): 새 모델·기술·원리 등 '어떻게 되는지 배우는' 내용. (힌트: {f_kw})\n"
        f"   - TREND(활용): 제품·서비스·시장·업무 등 '바로 써먹거나 흐름을 아는' 내용. (힌트: {t_kw})\n\n"
        "아래 JSON 형식만 출력(코드펜스/설명 금지):\n"
        '{"summary": "쉬운 한국어 2~3문장", "track": "FRONTIER 또는 TREND"}'
    )


def summarize_text_with_claude(item: Item, model: str, api_key: str, cfg: Config,
                               is_korea: bool = False) -> Item:
    import anthropic  # 지역 import: 미설치 시 이 항목만 실패
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=700,
        messages=[{"role": "user", "content": _build_prompt(item, cfg, is_korea)}],
    )
    text = "".join(getattr(b, "text", "") for b in msg.content)
    data = extract_json(text)
    summary = (data.get("summary") or "").strip()
    if not summary:
        snippet = re.sub(r"\s+", " ", item.raw_or_transcript or item.title)[:160]
        summary = f"(요약 생성 실패 — 원문 발췌) {snippet}"
    item.summary = summary
    # 국내 소스는 지역 기반으로 KOREA 트랙 고정. 그 외는 배움/활용 분류.
    item.track = KOREA if is_korea else _coerce_track(data.get("track"), item, cfg)
    item.meta["summarized_by"] = "claude"
    return item


# --------------------------------------------------------------------------
# 교차 중복 제거 — 텍스트/영상에 같은 이슈가 겹치면 신호 강한 것 하나만.
# --------------------------------------------------------------------------
def _norm_title(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z가-힣 ]", " ", (t or "").lower())).strip()


def _signal(item: Item) -> float:
    """신호 강도: 실제 LLM 요약 > 폴백, 영상 가산, HN 점수/채널 신뢰도 반영."""
    s = 0.0
    if item.meta.get("summarized_by") in ("claude", "gemini"):
        s += 3.0
    if item.type == "video":
        s += 1.0
        s += float(item.meta.get("trust", 0)) * 0.3
    pts = item.meta.get("points")
    if isinstance(pts, (int, float)):
        s += min(pts / 100.0, 3.0)
    return s


def dedup_cross(items: list[Item], threshold: float) -> list[Item]:
    kept: list[Item] = []
    for it in items:
        nt = _norm_title(it.title)
        dup_idx = -1
        for i, k in enumerate(kept):
            if SequenceMatcher(None, nt, _norm_title(k.title)).ratio() >= threshold:
                dup_idx = i
                break
        if dup_idx == -1:
            kept.append(it)
            continue
        # 중복: 신호 강한 쪽 유지
        if _signal(it) > _signal(kept[dup_idx]):
            log.info("교차 중복: '%s' 유지 / '%s' 제거", it.title[:40], kept[dup_idx].title[:40])
            kept[dup_idx] = it
        else:
            log.info("교차 중복: '%s' 유지 / '%s' 제거", kept[dup_idx].title[:40], it.title[:40])
    return kept


# --------------------------------------------------------------------------
# 오케스트레이션
# --------------------------------------------------------------------------
def process(items: list[Item], cfg: Config) -> list[Item]:
    api_key = get_secret("ANTHROPIC_API_KEY")
    model = cfg.get("models.claude", "claude-sonnet-4-6")
    out: list[Item] = []

    for it in items:
        try:
            if it.type == "video":
                # 영상은 Gemini 가 요약·분류 완료 상태. 누락분만 폴백 보정.
                if not it.summary:
                    it.summary = f"(영상 요약 미생성) {it.title}"
                it.track = _coerce_track(it.track, it, cfg)
                out.append(it)
                continue

            # 텍스트
            is_korea = it.meta.get("region") == "kr"   # 국내 소스 → KOREA 트랙
            if it.summary and it.track in VALID_TRACKS:
                out.append(it)  # 이미 처리됨
                continue

            if api_key:
                summarize_text_with_claude(it, model, api_key, cfg, is_korea=is_korea)
                time.sleep(0.2)
            else:
                # 키 없음(주로 구조 dry-run): 폴백 요약 + (국내면 KOREA / 아니면 키워드 분류).
                snippet = re.sub(r"\s+", " ", it.raw_or_transcript or it.title)[:180]
                it.summary = f"(요약 생략 — ANTHROPIC_API_KEY 미설정) {snippet}"
                it.track = KOREA if is_korea else keyword_classify(it, cfg)
                it.meta["summarized_by"] = "fallback"
            out.append(it)
            log.info("분류 OK: [%s] '%s'", it.track, it.title[:50])
        except Exception as e:  # noqa: BLE001 — 항목 단위 격리
            # 요약 호출/JSON 파싱이 실패해도 항목을 버리지 않는다(track 보장 누락 0).
            log.warning("요약/분류 실패 → 폴백 보정: '%s' : %s", it.title[:50], e)
            try:
                if not it.summary:
                    snippet = re.sub(r"\s+", " ", it.raw_or_transcript or it.title)[:160]
                    it.summary = f"(요약 실패 — 원문 발췌) {snippet}"
                it.track = KOREA if it.meta.get("region") == "kr" else keyword_classify(it, cfg)
                it.meta["summarized_by"] = "fallback-error"
                out.append(it)
            except Exception as e2:  # noqa: BLE001 — 폴백마저 실패하면 그때만 제외
                log.warning("폴백도 실패(이 항목만 건너뜀): '%s' : %s", it.title[:50], e2)

    # 모든 item 의 track/summary 최종 보증(누락 0건). 국내는 KOREA 유지.
    for it in out:
        if it.track not in VALID_TRACKS:
            it.track = KOREA if it.meta.get("region") == "kr" else keyword_classify(it, cfg)
        if not (it.summary or "").strip():
            it.summary = f"(요약 없음) {it.title}"

    threshold = float((cfg.get("classifier", {}) or {}).get("dedup_title_threshold", 0.72))
    deduped = dedup_cross(out, threshold)
    log.info("처리 완료: 입력 %d → 출력 %d (중복 제거 후)", len(items), len(deduped))
    return deduped


def _read_items(path: str | None) -> list[Item]:
    raw = open(path, "r", encoding="utf-8").read() if path else sys.stdin.read()
    data = json.loads(raw) if raw.strip() else []
    return [Item.from_dict(d) for d in data]


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="분류·요약기(단독 실행)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--in", dest="infile", default=None, help="item JSON 파일(미지정 시 stdin)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    items = _read_items(args.infile)
    result = process(items, cfg)
    json.dump([it.to_dict() for it in result], sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())

"""
다이제스트 조립 — 두 트랙(배움/활용) 한국어 메시지 + 카카오 길이 한도 대응 분할.

카카오 기본 '텍스트' 템플릿의 text 한도(약 200자)에 맞춰 메시지를 자동 분할한다.
- 트랙별 max_items_per_track 로 항목 수 상한(초과분은 "…외 N건" 말줄임).
- max_messages 로 총 메시지 수 상한(초과분도 말줄임) → 과도한 알림 방지.
"""
from __future__ import annotations

import sys
from typing import Any

try:
    from common.config import Config
    from common.item import Item, FRONTIER, TREND, KOREA
    from common.logging_setup import get_logger
except ModuleNotFoundError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from common.config import Config
    from common.item import Item, FRONTIER, TREND, KOREA
    from common.logging_setup import get_logger

log = get_logger("digest")

SECTION_TITLES = {FRONTIER: "📘 배움(FRONTIER)", TREND: "🛠 활용(TREND)", KOREA: "🇰🇷 국내 트렌드"}
# 카카오 링크 메시지에 쓰는 짧은 라벨
SHORT_LABELS = {TREND: "🛠 활용", KOREA: "🇰🇷 국내", FRONTIER: "📘 배움"}
DEFAULT_ORDER = [TREND, KOREA, FRONTIER]


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


# 문장 종결부(우선) → 단어 경계(차선) 순으로 자연스럽게 자를 지점을 찾는다.
_SENTENCE_ENDS = ("다. ", "요. ", "다.", "요.", ". ", "! ", "? ", "…", "\n")


def _smart_trim(text: str, limit: int) -> str:
    """limit 이내로 줄이되 가능하면 문장/단어 경계에서 끊고 '…'를 붙인다(단어 중간 절단 방지)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    cut = text[: limit - 1]
    floor = int(limit * 0.5)  # 너무 짧게 잘리는 것 방지(최소 절반 지점 이후에서만 경계 절단)
    best = -1
    for sep in _SENTENCE_ENDS:
        idx = cut.rfind(sep)
        if idx >= floor:
            best = max(best, idx + len(sep))
    if best == -1:
        sp = cut.rfind(" ")
        best = sp if sp >= floor else len(cut)
    return cut[:best].rstrip(" ").rstrip("".join(".!? ")) + "…"


def _item_block(idx: int, it: Item, char_budget: int) -> str:
    """단일 항목 블록: [번호. 제목 / 요약 / 링크].
    **URL 은 절대 자르지 않는다**(잘린 링크는 무용지물). 남는 공간에 제목·요약을 문장 경계로 맞춘다."""
    title = it.title.strip()
    summary = (it.summary or "").strip()
    url = it.url.strip()

    # URL 을 온전히 보존하기 위한 예산 배분. (제목/요약 사이 줄바꿈 2개 포함)
    avail = char_budget - len(url) - 2
    if avail < 12:
        # URL 이 예산을 거의 다 차지 → 제목만 최소로, URL 은 그대로.
        head = _smart_trim(f"{idx}. {title}", max(0, char_budget - len(url) - 1))
        return f"{head}\n{url}" if head else url

    head = f"{idx}. {title}"
    max_head = max(14, int(avail * 0.5))     # 제목은 가용분의 절반까지
    if len(head) > max_head:
        head = _smart_trim(head, max_head)
    room_summary = avail - len(head) - 1     # 제목-요약 사이 줄바꿈 1개
    if room_summary < 8:
        return f"{head}\n{url}"              # 요약 넣을 공간 없음 → 제목+링크만
    summary = _smart_trim(summary, room_summary)
    return f"{head}\n{summary}\n{url}"


def select_for_digest(items: list[Item], max_per_track: int) -> tuple[dict[str, list[Item]], dict[str, int]]:
    """트랙별로 나누고 상한 적용. 반환: (트랙별 항목, 트랙별 초과건수)."""
    buckets: dict[str, list[Item]] = {TREND: [], KOREA: [], FRONTIER: []}
    for it in items:
        if it.track in buckets:
            buckets[it.track].append(it)
    overflow: dict[str, int] = {}
    for track, lst in buckets.items():
        if len(lst) > max_per_track:
            overflow[track] = len(lst) - max_per_track
            buckets[track] = lst[:max_per_track]
        else:
            overflow[track] = 0
    return buckets, overflow


def selected_items(items: list[Item], cfg: Config) -> list[Item]:
    """다이제스트에 실제로 포함되는 item 들(트랙별 상한 적용 후)만 반환.
    상태(seen) 갱신은 '발송된' 항목에만 적용하기 위해 사용한다."""
    max_per_track = int((cfg.get("delivery", {}) or {}).get("max_items_per_track", 8))
    buckets, _ = select_for_digest(items, max_per_track)
    return buckets.get(FRONTIER, []) + buckets.get(TREND, [])


def build_segments(items: list[Item], cfg: Config, date_str: str) -> list[tuple[str, str | None]]:
    """다이제스트를 '분할되면 안 되는 세그먼트'로 만든다.
    반환: [(세그먼트 텍스트, item_id 또는 None)] — 제목/헤더/오버플로 세그먼트는 None.
    item_id 는 '실제로 발송된 항목'만 seen 처리하기 위해 pack 단계로 전달된다."""
    d = cfg.get("delivery", {}) or {}
    max_per_track = int(d.get("max_items_per_track", 8))
    limit = int(d.get("kakao_text_limit", 200))
    char_budget = max(80, limit - 12)  # 페이지 표시(i/n) 등 여유

    buckets, overflow = select_for_digest(items, max_per_track)
    title_prefix = d.get("title_prefix", "🌏 오늘의 해외 AI 브리핑")
    order = [t for t in (d.get("section_order") or DEFAULT_ORDER) if t in SECTION_TITLES]

    segs: list[tuple[str, str | None]] = [(f"{title_prefix} ({date_str})", None)]
    for track in order:
        lst = buckets.get(track, [])
        header = SECTION_TITLES[track]
        if not lst:
            segs.append((f"{header}\n(오늘 해당 항목 없음)", None))
            continue
        segs.append((header, None))
        for i, it in enumerate(lst, 1):
            segs.append((_item_block(i, it, char_budget), it.id))
        if overflow.get(track):
            segs.append((f"…외 {overflow[track]}건 더 있음", None))
    return segs


def pack_messages(seg_pairs: list[tuple[str, str | None]], limit: int,
                  max_messages: int) -> tuple[list[str], set[str]]:
    """세그먼트를 한도 이하 메시지로 그리디 패킹.
    반환: (메시지 리스트, 실제 발송된 item_id 집합). max_messages 로 잘려나간 메시지의
    항목은 included 에서 제외된다 → 발송 안 된 항목을 seen 처리하지 않도록 보장."""
    eff = max(60, limit - 12)  # 페이지 표시(i/n) + 여유 (tail/prefix 모두 이 버퍼 안에서 처리)
    packed: list[tuple[str, set[str]]] = []
    cur, cur_ids = "", set()
    for text, sid in seg_pairs:
        text = text if len(text) <= eff else _truncate(text, eff)
        if not cur:
            cur, cur_ids = text, ({sid} if sid else set())
        elif len(cur) + 2 + len(text) <= eff:
            cur += "\n\n" + text
            if sid:
                cur_ids.add(sid)
        else:
            packed.append((cur, cur_ids))
            cur, cur_ids = text, ({sid} if sid else set())
    if cur:
        packed.append((cur, cur_ids))

    # 총 메시지 수 상한 — 초과 메시지(그 안의 항목 포함)는 발송에서 제외된다.
    if len(packed) > max_messages:
        dropped = len(packed) - max_messages
        packed = packed[:max_messages]
        tail = f"\n\n(이하 {dropped}개 메시지 생략 — 항목 수/길이 한도)"
        last_text, last_ids = packed[-1]
        # 길이검사/트렁케이트에 eff 사용 → 이후 붙는 페이지 prefix 여유를 미리 확보(200자 초과 방지).
        if len(last_text) + len(tail) <= eff:
            last_text += tail
        else:
            last_text = _truncate(last_text, eff - len(tail)) + tail
        packed[-1] = (last_text, last_ids)

    messages = [t for t, _ in packed]
    included_ids: set[str] = set()
    for _, ids in packed:
        included_ids |= ids

    # 페이지 표시
    total = len(messages)
    if total > 1:
        messages = [f"({i}/{total})\n{m}" for i, m in enumerate(messages, 1)]
    return messages, included_ids


def build_messages(items: list[Item], cfg: Config, date_str: str) -> tuple[list[str], set[str]]:
    """반환: (발송할 메시지 리스트, 실제 메시지에 포함된 item_id 집합)."""
    d = cfg.get("delivery", {}) or {}
    limit = int(d.get("kakao_text_limit", 200))
    max_messages = int(d.get("max_messages", 6))
    seg_pairs = build_segments(items, cfg, date_str)
    messages, included_ids = pack_messages(seg_pairs, limit, max_messages)
    log.info("다이제스트 → %d개 메시지(한도 %d자), 발송 항목 %d건",
             len(messages), limit, len(included_ids))
    return messages, included_ids


def build_full_text(items: list[Item], cfg: Config, date_str: str) -> str:
    """분할 전 전체 다이제스트(미리보기/로그용)."""
    return "\n\n".join(t for t, _ in build_segments(items, cfg, date_str))


def build_link_message(items: list[Item], cfg: Config, date_str: str, url: str) -> str:
    """링크 모드용 짧은 카카오 메시지 1건(제목+건수+HTML 전체보기 링크). 200자 이내."""
    d = cfg.get("delivery", {}) or {}
    limit = int(d.get("kakao_text_limit", 200))
    prefix = d.get("title_prefix", "🌏 오늘의 해외 AI 브리핑")
    order = [t for t in (d.get("section_order") or DEFAULT_ORDER) if t in SHORT_LABELS]
    caps = d.get("html_max_items") or {}
    # HTML 페이지에 실제 표시되는(캡 적용) 건수와 맞춘다.
    parts = []
    for track in order:
        n = sum(1 for it in items if it.track == track)
        cap = int(caps.get(track, 8))
        if 0 <= cap < n:
            n = cap
        parts.append(f"{SHORT_LABELS[track]} {n}")
    text = f"{prefix} ({date_str})\n" + " · ".join(parts) + f"\n👉 전체 보기: {url}"
    return _truncate(text, limit)

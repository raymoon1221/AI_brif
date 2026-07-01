"""
감사(audit) 후속 회귀 테스트 — 각 수정이 실제로 적용됐는지 코드로 증명한다.
네트워크/실키 불필요(모두 오프라인, LLM 호출은 몽키패치).

실행: python tests/test_fixes.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import load_config
from common.item import Item, FRONTIER, TREND, VALID_TRACKS, make_id, canon_url
from common.jsonutil import extract_json
from collectors import collector_video as cv
from delivery import digest
import processors.classifier_summarizer as cs


def test_duration_parser_days_weeks():
    """#7: P#D / P#W / 빈 값 처리."""
    assert cv._iso_duration_to_sec("PT1H2M3S") == 3723
    assert cv._iso_duration_to_sec("P1DT2H") == 26 * 3600          # 1일 2시간
    assert cv._iso_duration_to_sec("P1W") == 7 * 24 * 3600         # 1주
    assert cv._iso_duration_to_sec("") == 0
    assert cv._iso_duration_to_sec("garbage") == 0
    print("[OK] #7 ISO8601 duration: days/weeks/빈값 처리")


def test_budget_count_cap_on_estimates():
    """#1: 길이 추정 시 개수 상한이 강제되어 무제한 처리 방지."""
    cfg = load_config()
    cfg.data["video_sources"] = {
        "daily_budget_minutes": 24, "per_channel_cap": 99, "relevance_keywords": [],
    }
    # 길이 미상 영상 10개 → 각 12분 추정. count_cap = 24//12 = 2.
    items = [Item(source="C", type="video", title=f"v{i}",
                  url=f"https://youtube.com/watch?v=x{i}", channel="C",
                  meta={"trust": 3, "video_id": f"x{i}"}) for i in range(10)]
    cv.enrich_durations(items, None)                  # 키 없음 → 모두 추정값
    assert all(it.meta.get("duration_estimated") for it in items)
    selected = cv.select_within_budget(cfg, items)
    assert len(selected) == 2, f"개수 상한 2 기대, got {len(selected)}"
    print("[OK] #1 추정 길이 예산: 개수 상한(2)으로 무제한 처리 차단")


def test_delivered_ids_excludes_dropped():
    """#5/#9: max_messages 로 잘린 항목은 delivered_ids 에 없어 seen 처리 안 됨."""
    cfg = load_config()
    cfg.data["delivery"] = {
        "title_prefix": "T", "max_items_per_track": 50,
        "kakao_text_limit": 200, "max_messages": 2, "send_interval_sec": 0,
    }
    items = []
    for i in range(20):
        tr = FRONTIER if i % 2 == 0 else TREND
        items.append(Item(source="s", type="text", title=f"제목 항목 번호 {i}",
                          url=f"https://ex.com/{i}", summary="한국어 요약 " * 5, track=tr))
    messages, delivered_ids = digest.build_messages(items, cfg, "2026-07-01")
    assert len(messages) == 2, f"max_messages=2 기대, got {len(messages)}"
    assert len(delivered_ids) < len(items), "잘린 항목이 delivered 에서 제외돼야 함"
    # delivered_ids 의 항목만 실제 메시지 텍스트에 등장하는지 교차 확인
    joined = "\n".join(messages)
    for it in items:
        present = it.title in joined
        assert (it.id in delivered_ids) == present, f"불일치: {it.title}"
    print(f"[OK] #5/#9 발송 항목만 delivered: {len(delivered_ids)}/{len(items)}건 (잘린 항목 seen 제외)")


def test_page_prefix_within_limit():
    """#10: 분할/말줄임 + 페이지표시까지 합쳐도 카카오 한도(200) 초과 금지."""
    cfg = load_config()
    limit = 200
    cfg.data["delivery"] = {
        "title_prefix": "오늘의 브리핑", "max_items_per_track": 50,
        "kakao_text_limit": limit, "max_messages": 3, "send_interval_sec": 0,
    }
    items = [Item(source="s", type="text", title=f"항목 제목 {i} " + "가" * 30,
                  url=f"https://example.com/very/long/path/{i}",
                  summary="아주 긴 한국어 요약입니다. " * 10,
                  track=FRONTIER if i % 2 else TREND) for i in range(30)]
    messages, _ = digest.build_messages(items, cfg, "2026-07-01")
    for m in messages:
        assert len(m) <= limit, f"한도 초과: {len(m)}자 > {limit}\n{m}"
    print(f"[OK] #10 모든 메시지 ≤ {limit}자 (페이지표시/말줄임 포함, {len(messages)}개)")


def test_claude_failure_keeps_item():
    """#3: Claude 요약이 예외를 던져도 항목은 폴백으로 출력에 남는다(누락 0)."""
    cfg = load_config()
    orig_get = cs.get_secret
    orig_sum = cs.summarize_text_with_claude
    cs.get_secret = lambda name, *a, **k: "fake-key" if name == "ANTHROPIC_API_KEY" else orig_get(name, *a, **k)

    def boom(*a, **k):
        raise RuntimeError("simulated API/JSON failure")
    cs.summarize_text_with_claude = boom
    try:
        it = Item(source="arXiv", type="text", title="A paper on transformer architecture",
                  url="https://arxiv.org/abs/2606.1", raw_or_transcript="benchmark and model details")
        result = cs.process([it], cfg)
    finally:
        cs.get_secret = orig_get
        cs.summarize_text_with_claude = orig_sum
    assert len(result) == 1, "실패해도 항목이 남아야 함"
    assert result[0].track in VALID_TRACKS, "track 강제 배정"
    assert result[0].summary.strip(), "폴백 summary 존재"
    assert result[0].meta.get("summarized_by") == "fallback-error"
    print("[OK] #3 Claude 실패 시 폴백으로 항목 보존(track+summary)")


def test_canonical_url_id():
    """#8: 제목 변동/트래킹 파라미터 무시, youtube v= 유지."""
    a = Item(source="s", type="text", title="원제목", url="https://blog.com/post?utm_source=x")
    b = Item(source="s", type="text", title="수정된 제목", url="https://blog.com/post/")
    assert a.id == b.id, "제목 변동/utm/슬래시 차이에도 동일 id 여야 함"
    # youtube 의 v= 는 보존되어 서로 다른 영상은 다른 id
    v1 = Item(source="s", type="video", title="t", url="https://www.youtube.com/watch?v=AAA")
    v2 = Item(source="s", type="video", title="t", url="https://www.youtube.com/watch?v=BBB")
    assert v1.id != v2.id, "youtube v= 가 유지되어 영상별 id 분리"
    assert "v=AAA" in canon_url("https://www.youtube.com/watch?v=AAA&utm_medium=x")
    print("[OK] #8 canonical-URL id: 제목/utm 무시, youtube v= 유지")


def test_extract_json_robust():
    """#12: 본문 중괄호/후행 산문/멀티객체에도 첫 유효 객체 추출."""
    assert extract_json('{"a":1}')["a"] == 1
    assert extract_json('설명 {잡음} 뒤 {"track":"FRONTIER"} 추가설명')["track"] == "FRONTIER"
    assert extract_json('```json\n{"track":"TREND"}\n```')["track"] == "TREND"
    assert extract_json('{"summary":"끝"} 그리고 다른 {"x":2}')["summary"] == "끝"
    print("[OK] #12 _extract_json: 잡음/후행/멀티객체 견고 처리")


if __name__ == "__main__":
    print("===== 감사 후속 회귀 테스트 =====")
    test_duration_parser_days_weeks()
    test_budget_count_cap_on_estimates()
    test_delivered_ids_excludes_dropped()
    test_page_prefix_within_limit()
    test_claude_failure_keeps_item()
    test_canonical_url_id()
    test_extract_json_robust()
    print("\n===== 모든 회귀 테스트 통과 =====")

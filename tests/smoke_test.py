"""
오프라인 스모크 테스트 — 네트워크/API 키 없이 파이프라인 핵심 로직을 검증한다.

검증 항목:
  1) 모든 item 이 배움/활용 중 하나로 강제 분류되는가(track ∈ {FRONTIER, TREND}).
  2) 텍스트/영상 교차 중복이 제거되는가.
  3) 두 트랙 다이제스트가 한국어로 조립되고 dry-run 으로 미리보기되는가(실제 전송 X).
  4) 깨진 소스가 있어도 collector 가 예외 없이 부분 결과를 반환하는가(격리).

실행:  python tests/smoke_test.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import load_config
from common.item import Item, FRONTIER, TREND, VALID_TRACKS
from processors import classifier_summarizer
from delivery import digest, kakao
from collectors import collector_text


def _fake_items() -> list[Item]:
    return [
        # 배움 성격(논문/아키텍처) — 키워드 폴백으로 FRONTIER 예상
        Item(source="arXiv cs.CL", type="text",
             title="A New Transformer Architecture for Efficient Attention",
             url="https://arxiv.org/abs/2406.00001",
             raw_or_transcript="We propose a new attention mechanism and benchmark it. "
                               "Ablation shows lower loss during pretraining."),
        # 활용 성격(제품 출시) — TREND 예상
        Item(source="OpenAI Blog", type="text",
             title="Introducing our new API pricing and product launch",
             url="https://openai.com/blog/new-product",
             raw_or_transcript="Today we release a new product with updated API pricing "
                               "and integration features for your workflow."),
        # 영상(이미 Gemini 가 요약·분류했다고 가정)
        Item(source="Two Minute Papers", type="video",
             title="This New AI Model Is Insane!",
             url="https://www.youtube.com/watch?v=abc123",
             summary="새 모델의 핵심 기여와 작동 원리를 소개한다. 한계도 언급한다.",
             track=FRONTIER, meta={"summarized_by": "gemini", "trust": 5}),
        # 교차 중복: 위 영상과 거의 같은 제목의 텍스트(신호 약함) → 제거되어야
        Item(source="Some Blog", type="text",
             title="This New AI Model Is Insane",
             url="https://blog.example.com/insane-model",
             raw_or_transcript="A blog post about the same new AI model."),
    ]


def test_classification_and_dedup() -> list[Item]:
    cfg = load_config()
    items = _fake_items()
    result = classifier_summarizer.process(items, cfg)

    # (1) 모든 item track 강제 분류
    for it in result:
        assert it.track in VALID_TRACKS, f"track 누락: {it.title}"
        assert it.summary, f"summary 누락: {it.title}"
    print(f"[OK] 모든 item track 분류 + 한국어 summary 존재 ({len(result)}건)")

    # (2) 교차 중복 제거: 동일 이슈 4→3
    titles = [it.title for it in result]
    assert len(result) == 3, f"중복 제거 실패: {titles}"
    assert any(it.type == "video" for it in result), "신호 강한 영상이 유지되어야 함"
    print(f"[OK] 교차 중복 제거: 4 → {len(result)}건 (신호 강한 영상 유지)")

    return result


def test_digest_and_dry_run(items: list[Item]) -> None:
    cfg = load_config()
    messages, delivered_ids = digest.build_messages(items, cfg, "2026-06-30")
    assert messages, "메시지가 비어 있음"
    assert delivered_ids, "발송 항목 id 집합이 비어 있음"
    full = digest.build_full_text(items, cfg, "2026-06-30")
    assert "배움(FRONTIER)" in full and "활용(TREND)" in full, "두 트랙 섹션 누락"
    print(f"[OK] 두 트랙 다이제스트 조립 → {len(messages)}개 메시지")

    # (3) dry-run: 실제 전송 없이 미리보기 + 파일 출력
    result = kakao.deliver(messages, cfg, dry_run=True, date_str="2026-06-30")
    assert result["dry_run"] is True and result["failed"] == 0
    assert "preview_file" in result
    print(f"[OK] dry-run 미리보기 파일: {result['preview_file']}")


def test_failure_isolation() -> None:
    """깨진 RSS 소스가 있어도 collect() 가 예외 없이 빈/부분 결과를 반환해야 한다."""
    cfg = load_config()
    # config 를 메모리에서 깨진 소스만 남기도록 덮어쓴다.
    cfg.data["text_sources"] = {
        "rss": [{"name": "BROKEN", "url": "http://127.0.0.1:9/definitely-not-a-feed.xml"}],
        "arxiv": {"enabled": False},
        "hackernews": {"enabled": False},
    }
    items = collector_text.collect(cfg, use_dedup=False)
    assert isinstance(items, list), "리스트가 반환되어야 함"
    print(f"[OK] 격리: 깨진 소스에도 예외 없이 부분 결과 반환 ({len(items)}건)")


if __name__ == "__main__":
    print("===== 오프라인 스모크 테스트 =====")
    processed = test_classification_and_dedup()
    test_digest_and_dry_run(processed)
    test_failure_isolation()
    print("\n===== 모든 스모크 테스트 통과 =====")

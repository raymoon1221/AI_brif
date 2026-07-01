"""
HTML 렌더러 보안/정합 테스트 — 공개 페이지이므로 XSS·링크 스킴을 엄격히 검증한다.

실행: python tests/test_html.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.config import load_config
from common.item import Item, FRONTIER, TREND
from delivery import digest
from render import html as html_render


def test_escaping_and_link_safety():
    cfg = load_config()
    items = [
        # 악성 입력: 스크립트/이벤트 핸들러/위험 스킴
        Item(source="<b>evil</b>", type="text",
             title="<script>alert('xss')</script> New Model",
             url="javascript:alert(document.cookie)",
             summary="정상 요약 <img src=x onerror=alert(1)> 끝.", track=FRONTIER),
        # 정상 항목
        Item(source="OpenAI", type="text", title="Safe Title & Co",
             url="https://openai.com/blog/x?a=1&b=2",
             summary="정상적인 한국어 요약입니다.", track=TREND),
    ]
    out = html_render.render_digest(items, cfg, "2026-07-01")

    # (1) 콘텐츠에서 온 실행 가능한 raw 태그가 그대로 들어가면 안 됨(이스케이프되어야 함)
    assert "<script>alert" not in out, "스크립트가 이스케이프되지 않음"
    assert "<img" not in out, "raw <img> 태그가 살아있음(이벤트 핸들러 실행 위험)"
    assert "javascript:alert" not in out, "위험 스킴 링크가 그대로 노출됨"
    # (2) 악성 문자열은 이스케이프된 형태로 존재해야 함
    assert "&lt;script&gt;" in out, "이스케이프 처리 흔적이 없음"
    # (3) 위험 스킴 URL 은 링크로 렌더되지 않아야(제목만 텍스트)
    assert 'href="javascript' not in out
    # (4) 정상 URL 은 살아있어야
    assert "https://openai.com/blog/x?a=1&amp;b=2" in out or "https://openai.com/blog/x" in out
    print("[OK] XSS 이스케이프 + 위험 스킴 링크 차단")


def test_no_secret_patterns_in_html():
    """렌더 결과에 키/토큰 형태 문자열이 섞이지 않는지(입력 스키마에 비밀값 없음 보증)."""
    import re
    cfg = load_config()
    items = [Item(source="s", type="text", title="t", url="https://e.com/a",
                  summary="요약", track=FRONTIER)]
    out = html_render.render_digest(items, cfg, "2026-07-01")
    for pat in (r"sk-ant-api", r"AIzaSy[A-Za-z0-9_\-]{33}", r"Bearer [A-Za-z0-9]"):
        assert not re.search(pat, out), f"HTML 에 비밀값 형태 발견: {pat}"
    print("[OK] HTML 에 비밀값 형태 문자열 없음")


def test_link_message_short():
    cfg = load_config()
    items = [Item(source="s", type="text", title="t", url="https://e.com/a", summary="x", track=FRONTIER),
             Item(source="s", type="video", title="v", url="https://youtube.com/watch?v=a", summary="y", track=TREND)]
    url = "https://user.github.io/AI_brif/archive/2026-07-01.html"
    msg = digest.build_link_message(items, cfg, "2026-07-01", url)
    assert url in msg and len(msg) <= 200
    assert "활용 1" in msg and "배움 1" in msg and "국내" in msg
    print(f"[OK] 링크 메시지 {len(msg)}자, 3트랙 건수+URL 포함")


if __name__ == "__main__":
    print("===== HTML 렌더러 보안/정합 테스트 =====")
    test_escaping_and_link_safety()
    test_no_secret_patterns_in_html()
    test_link_message_short()
    print("\n===== 통과 =====")

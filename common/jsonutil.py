"""
LLM 출력에서 JSON 객체를 견고하게 추출한다.

기존 greedy(첫 '{' ~ 마지막 '}') 방식은 본문에 중괄호가 섞이거나(예: 요약에
'{...}' 표현), 멀티 객체/후행 산문이 있으면 json.loads 가 실패했다. 이 버전은
각 '{' 위치에서 JSONDecoder.raw_decode 를 시도해 첫 번째로 파싱되는 dict 를 반환한다.
"""
from __future__ import annotations

import json
import re
from typing import Any

_DECODER = json.JSONDecoder()


def extract_json(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    # 코드펜스 제거
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    # 각 '{' 에서 완결 객체 파싱 시도 → 첫 dict 반환
    for i, ch in enumerate(t):
        if ch != "{":
            continue
        try:
            obj, _ = _DECODER.raw_decode(t[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError(f"JSON 추출 실패: {t[:200]}")

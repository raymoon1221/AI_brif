"""
요약 용어 풀이(괄호 설명) 규칙 — Claude/Gemini 프롬프트가 공유한다.

gloss_level(운영자 설정):
  - none : 어떤 용어에도 괄호 풀이를 붙이지 않는다(문장만 쉽게).
  - rare : 널리 알려진 약자는 그대로 두고, '낯선/전문적' 약자만 짧게 풀이(기본).
  - all  : 모든 영어 약자에 짧게 풀이(초보자 공유용).
공통: 벤치마크·파인튜닝처럼 한글로 굳어진 용어, 일반 비즈니스 용어에는 풀이를 붙이지 않는다.
"""
from __future__ import annotations

# 이 약자들은 rare 모드에서 풀이를 붙이지 않는다(널리 알려진 것).
COMMON_ACRONYMS = "AI, LLM, API, GPU, CPU, GPT, ML, UI, UX, SDK, IT, OS, PC, URL, HTTP, SaaS, IoT"

_BASE = "- 'AI를 잘 모르는 일반인'도 이해할 만큼 쉬운 한국어로 쓴다. 어려운 문장·번역투 금지.\n"
_TAIL = "- '무엇이 새로운지 / 그래서 뭐가 좋아지는지'를 일상어로 담는다.\n"


def gloss_rules(level: str = "rare") -> str:
    level = (level or "rare").strip().lower()
    if level == "none":
        mid = "- 어떤 용어에도 괄호 풀이를 붙이지 않는다. 어려운 말은 쉬운 우리말로 바꿔 문장 자체를 쉽게 쓴다.\n"
    elif level == "all":
        mid = (
            "- 영어 약자가 처음 나올 때만 괄호로 아주 짧은 뜻풀이를 한 번 붙인다.\n"
            "  예) LLM(사람 말을 알아듣고 답하는 큰 AI), API(프로그램끼리 잇는 통로).\n"
            "- 벤치마크·파인튜닝처럼 한글로 굳어진 용어, 일반 비즈니스 용어에는 풀이를 붙이지 않는다.\n"
        )
    else:  # rare (기본)
        mid = (
            f"- 널리 알려진 약자({COMMON_ACRONYMS})에는 풀이를 붙이지 않고 그대로 쓴다.\n"
            "- '낯설거나 전문적인' 약자가 처음 나올 때만 괄호로 아주 짧은 뜻풀이를 한 번 붙인다.\n"
            "  예) RAG(검색 결과로 답을 보강하는 방식), RLHF(사람 피드백으로 AI를 다듬는 학습), MoE(전문가 여러 개를 나눠 쓰는 구조).\n"
            "- 벤치마크·파인튜닝처럼 한글로 굳어진 용어, 일반 비즈니스 용어에는 풀이를 붙이지 않는다.\n"
        )
    return "규칙:\n" + _BASE + mid + _TAIL

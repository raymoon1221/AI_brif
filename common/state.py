"""
상태 파일 입출력 — dedup(중복 제거) 집합, 발송 이력, 카카오 토큰 등.

원자적 쓰기(임시 파일 → rename)로 중단 시 손상 방지. 모든 파일은 state/ 아래 두며
.gitignore 처리된다(비밀값/이력은 커밋하지 않는다).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

log = get_logger("state")


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("상태 파일 읽기 실패(%s) → 기본값 사용: %s", path.name, e)
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 원자적 교체


class SeenStore:
    """이미 처리/발송한 id 집합을 관리(중복 제거용). 크기 상한으로 무한 증가 방지."""

    def __init__(self, path: Path, max_size: int = 5000):
        self.path = path
        self.max_size = max_size
        raw = read_json(path, [])
        self.order: list[str] = list(raw) if isinstance(raw, list) else []
        self.seen: set[str] = set(self.order)

    def __contains__(self, item_id: str) -> bool:
        return item_id in self.seen

    def add(self, item_id: str) -> None:
        if item_id in self.seen:
            return
        self.seen.add(item_id)
        self.order.append(item_id)

    def save(self) -> None:
        # 오래된 항목부터 잘라 상한 유지(FIFO).
        if len(self.order) > self.max_size:
            drop = len(self.order) - self.max_size
            for old in self.order[:drop]:
                self.seen.discard(old)
            self.order = self.order[drop:]
        write_json(self.path, self.order)

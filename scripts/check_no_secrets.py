"""
비밀값 유출 방지 스캐너 — 저장소에 커밋될 파일에 실제 키/토큰이 섞였는지 검사한다.
(공개 GitHub 저장소 + Pages 배포이므로 방어선 이중화)

- git 이 있으면 '추적 중인 파일'만 검사(=실제 커밋 대상). 없으면 트리를 걷되 무시 디렉터리 제외.
- 고신뢰 패턴만 탐지해 오탐을 줄인다. 매치는 마스킹해 출력(값 자체를 노출하지 않음).
- 하나라도 발견되면 종료코드 1(=CI 실패).

사용:  python scripts/check_no_secrets.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = [
    # 고정 접두 패턴
    (re.compile(r"sk-ant-api\d{2}-[A-Za-z0-9_\-]{20,}"), "Anthropic API key"),
    (re.compile(r"AIzaSy[A-Za-z0-9_\-]{33}"), "Google/Gemini API key"),
    (re.compile(r"ghp_[A-Za-z0-9]{36,}"), "GitHub PAT"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"), "Slack token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "Private key block"),
    # 접두가 없는 카카오/OAuth 비밀값은 '변수명 = 긴 값' 형태로 탐지(placeholder 는 x{6,} 로 예외).
    (re.compile(r'(?i)kakao[_.\-]?(?:rest_api_key|refresh_token|access_token|client_secret)'
                r'["\']?\s*[:=]\s*["\']?[A-Za-z0-9_\-]{20,}'), "Kakao credential"),
    (re.compile(r'(?i)["\']?(?:access_token|refresh_token)["\']?\s*[:=]\s*'
                r'["\']?[A-Za-z0-9_\-]{20,}'), "OAuth token"),
    (re.compile(r'(?i)\w*(?:client_secret|api[_-]?key)\w*\s*[:=]\s*'
                r'["\']?[A-Za-z0-9_\-]{24,}'), "Secret-named assignment"),
]

# 검사 제외 디렉터리(생성물/의존성/가상환경). .env* 는 애초에 gitignore 라 추적 안 됨.
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "out", "public", "node_modules", ".pytest_cache"}


def _is_gitignored_secret_file(name: str) -> bool:
    """gitignore 되는 실제 비밀값 파일(.env, .env.local 등)은 스캔 대상에서 제외. .env.example 은 검사."""
    if name == ".env.example":
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")
# 플레이스홀더 파일은 예외(연속 x 6자 이상은 예시값으로 간주).
PLACEHOLDER = re.compile(r"x{6,}", re.I)
TEXT_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".txt", ".json", ".cfg", ".ini", ".toml", ".html", ".env", ""}


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return [ROOT / line for line in out.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    # git 없음 → 트리 순회
    files = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if _is_gitignored_secret_file(p.name):   # .env 등 gitignore 대상은 제외
            continue
        files.append(p)
    return files


def main() -> int:
    # Windows(cp949)에서도 이모지/한글 출력이 깨지지 않도록 UTF-8 강제.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    findings = []
    for f in _tracked_files():
        if f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat, name in PATTERNS:
                m = pat.search(line)
                if m and not PLACEHOLDER.search(m.group(0)):
                    masked = m.group(0)[:8] + "…(마스킹)"
                    findings.append((f.relative_to(ROOT), i, name, masked))

    if findings:
        print("❌ 비밀값으로 의심되는 값이 저장소 파일에서 발견됨:", file=sys.stderr)
        for rel, ln, name, masked in findings:
            print(f"  - {rel}:{ln}  [{name}]  {masked}", file=sys.stderr)
        print("\n실제 키는 .env(gitignore) / GitHub Secrets 로만 관리하세요.", file=sys.stderr)
        return 1
    print("✅ 비밀값 스캔 통과 — 저장소 파일에 노출된 키 없음.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

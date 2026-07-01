"""
카카오 '나에게 보내기'(memo/default/send) + OAuth 토큰 갱신.

- 친구 전송 API 는 사용하지 않는다(나에게 보내기 전용 — 검수/쿼터 불필요).
- access token(약 6시간) 만료 시 refresh token(약 2개월)으로 자동 재발급.
- 모든 비밀값은 환경변수에서만 읽고, 갱신된 토큰은 state/kakao_token.json 에 저장(gitignore).
- dry-run 이 기본값: 실제 전송 없이 out/ 파일과 콘솔로 최종 메시지를 출력한다.

토큰 최초 발급/갱신 절차는 README "카카오 토큰 발급/갱신" 참고.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from common.config import Config
    from common.env import get_secret
    from common.logging_setup import get_logger
    from common.state import read_json, write_json
except ModuleNotFoundError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from common.config import Config
    from common.env import get_secret
    from common.logging_setup import get_logger
    from common.state import read_json, write_json

log = get_logger("kakao")

try:
    import requests
except ImportError:
    requests = None  # type: ignore

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
_HTTP_TIMEOUT = 15


def _token_path(cfg: Config) -> Path:
    return cfg.state_dir / "kakao_token.json"


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def refresh_access_token(rest_api_key: str, refresh_token: str,
                         client_secret: str | None = None) -> dict[str, Any]:
    """refresh token 으로 새 access token 발급. (client_id = REST API 키)
    앱에서 '클라이언트 시크릿'이 활성화된 경우 client_secret 이 필요하다."""
    if requests is None:
        raise RuntimeError("requests 미설치")
    data = {
        "grant_type": "refresh_token",
        "client_id": rest_api_key,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(TOKEN_URL, data=data, timeout=_HTTP_TIMEOUT)
    if resp.status_code != 200:
        # 응답 본문에 토큰/민감정보가 섞일 수 있어 앞부분만 노출.
        raise RuntimeError(f"토큰 갱신 실패({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def get_access_token(cfg: Config) -> str:
    """
    유효한 access token 을 반환. 없거나 만료 임박이면 refresh token 으로 갱신 후 저장.
    부트스트랩 우선순위: state 파일 > 환경변수(KAKAO_ACCESS_TOKEN/KAKAO_REFRESH_TOKEN).
    """
    rest_api_key = get_secret("KAKAO_REST_API_KEY", required=True)
    state = read_json(_token_path(cfg), {})

    access = state.get("access_token") or get_secret("KAKAO_ACCESS_TOKEN")
    refresh = state.get("refresh_token") or get_secret("KAKAO_REFRESH_TOKEN")
    refresh_from_state = bool(state.get("refresh_token"))
    expires_at = float(state.get("expires_at", 0) or 0)

    # 만료 60초 전이면 갱신. access 가 아예 없어도 갱신 시도.
    needs_refresh = (not access) or (_now_ts() >= expires_at - 60)
    if needs_refresh:
        if not refresh:
            raise RuntimeError(
                "KAKAO_REFRESH_TOKEN 이 없어 access token 을 발급할 수 없습니다. "
                "README '카카오 토큰 발급' 을 참고해 최초 refresh token 을 발급하세요."
            )
        if not refresh_from_state:
            # 상태 파일이 유실되어 정적 Secret 으로 폴백 — 카카오가 토큰을 회전했다면 무효일 수 있음.
            log.warning("state 에 저장된 refresh token 이 없어 정적 Secret 으로 폴백합니다. "
                        "갱신이 실패하면 README 절차로 KAKAO_REFRESH_TOKEN 을 재발급하세요.")
        log.info("access token 갱신 중…")
        try:
            data = refresh_access_token(rest_api_key, refresh, get_secret("KAKAO_CLIENT_SECRET"))
        except Exception:  # noqa: BLE001
            log.error("카카오 access token 갱신 실패 — refresh token 이 만료/무효일 수 있습니다. "
                      "README '카카오 토큰 발급/갱신'으로 재발급 필요.")
            raise
        access = data["access_token"]
        new_state = {
            "access_token": access,
            "refresh_token": data.get("refresh_token", refresh),  # 회전되면 갱신
            "expires_at": _now_ts() + int(data.get("expires_in", 21600)),
        }
        write_json(_token_path(cfg), new_state)
        log.info("access token 갱신 완료(다음 만료까지 약 %d분)", int(data.get("expires_in", 21600)) // 60)
    return access


def send_to_me(text: str, access_token: str, link_url: str | None = None) -> dict[str, Any]:
    """기본 '텍스트' 템플릿으로 나에게 보내기 1건 전송."""
    if requests is None:
        raise RuntimeError("requests 미설치")
    template: dict[str, Any] = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": link_url or "https://developers.kakao.com",
                 "mobile_web_url": link_url or "https://developers.kakao.com"},
    }
    resp = requests.post(
        SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"카톡 전송 실패({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def deliver(messages: list[str], cfg: Config, dry_run: bool = True,
            date_str: str | None = None, link_url: str | None = None) -> dict[str, Any]:
    """
    메시지 리스트를 발송. dry_run=True 면 실제 전송 없이 콘솔+파일로 출력(기본).
    link_url 이 주어지면(링크 모드) 각 메시지의 버튼/링크를 그 URL 로 설정한다.
    한 메시지 전송 실패는 격리하고 나머지를 계속 보낸다.
    """
    result = {"dry_run": dry_run, "total": len(messages), "sent": 0, "failed": 0, "errors": []}

    if dry_run:
        preview = "\n\n――――――――――\n\n".join(messages)
        out_dir = cfg.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = out_dir / f"digest_{(date_str or 'preview')}.txt"
        fname.write_text(preview, encoding="utf-8")
        log.info("[DRY-RUN] 실제 전송 안 함. 미리보기 %d개 메시지 → %s", len(messages), fname)
        print("\n===== [DRY-RUN] 카카오 전송 미리보기 =====\n")
        print(preview)
        print("\n===== 미리보기 끝 =====\n")
        result["sent"] = len(messages)
        result["preview_file"] = str(fname)
        return result

    # 실제 전송
    interval = float((cfg.get("delivery", {}) or {}).get("send_interval_sec", 0.5))
    access = get_access_token(cfg)
    for i, msg in enumerate(messages, 1):
        try:
            send_to_me(msg, access, link_url=link_url)
            result["sent"] += 1
            log.info("전송 %d/%d 완료", i, len(messages))
            if interval:
                time.sleep(interval)
        except Exception as e:  # noqa: BLE001 — 메시지 단위 격리
            result["failed"] += 1
            result["errors"].append(str(e))
            log.warning("전송 %d/%d 실패(건너뜀): %s", i, len(messages), e)
    return result

"""
카카오 최초 refresh token 발급 헬퍼 (1회만 실행).

사전 준비(README "카카오 토큰 발급/갱신" 참고):
  1) developers.kakao.com 앱 생성 → REST API 키 확보.
  2) 카카오 로그인 활성화 + Redirect URI 등록(예: https://localhost).
  3) 동의항목에서 '카카오톡 메시지 전송(talk_message)' 활성화.
  4) 아래 URL 을 브라우저에서 열고 로그인 → 리다이렉트된 주소의 code 값을 복사.
     https://kauth.kakao.com/oauth/authorize?client_id=REST_API_KEY&redirect_uri=REDIRECT_URI&response_type=code&scope=talk_message

사용:
  python scripts/kakao_get_token.py --rest-api-key XXXX --redirect-uri https://localhost --code AUTH_CODE

출력된 refresh_token 을 .env(KAKAO_REFRESH_TOKEN) 또는 GitHub Secrets 에 저장한다.
이후로는 파이프라인이 access token 을 자동 갱신하므로 다시 실행할 필요가 없다.
"""
from __future__ import annotations

import argparse
import sys

import requests

TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="카카오 refresh token 최초 발급")
    ap.add_argument("--rest-api-key", required=True, help="앱 REST API 키(client_id)")
    ap.add_argument("--redirect-uri", required=True, help="등록한 Redirect URI")
    ap.add_argument("--code", required=True, help="authorize 후 받은 인가 코드")
    ap.add_argument("--client-secret", default=None,
                    help="앱에서 '클라이언트 시크릿'을 활성화한 경우 필요")
    args = ap.parse_args(argv)

    data = {
        "grant_type": "authorization_code",
        "client_id": args.rest_api_key,
        "redirect_uri": args.redirect_uri,
        "code": args.code,
    }
    if args.client_secret:
        data["client_secret"] = args.client_secret
    resp = requests.post(TOKEN_URL, data=data, timeout=15)

    if resp.status_code != 200:
        print(f"[실패] {resp.status_code}: {resp.text}", file=sys.stderr)
        return 1

    data = resp.json()
    print("=== 발급 성공 — 아래 값을 .env / GitHub Secrets 에 저장하세요 ===")
    print(f"KAKAO_REST_API_KEY={args.rest_api_key}")
    print(f"KAKAO_REFRESH_TOKEN={data.get('refresh_token')}")
    print()
    print(f"(참고) access_token(약 6시간): {data.get('access_token')}")
    print(f"(참고) refresh_token 만료(초): {data.get('refresh_token_expires_in')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

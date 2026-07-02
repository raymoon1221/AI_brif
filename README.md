# AI_brief — 매일 해외 AI 콘텐츠 → 두 트랙 한국어 다이제스트 → 카카오 "나에게 보내기"

해외 AI 콘텐츠(블로그·뉴스레터·arXiv·Hacker News·유튜브)를 매일 자동으로
**수집 → 요약 → 두 트랙 분리 → 카카오톡 발송**하는 무인 파이프라인입니다.

- **배움(FRONTIER)** — 새 모델·아키텍처·논문·기법·벤치마크 등 *원리를 배우는* 기술 콘텐츠
- **활용(TREND)** — 제품 출시·시장 동향·실전 적용·워크플로우 등 *바로 써먹는* 콘텐츠

> 텍스트 요약과 유튜브 영상 분석을 모두 **Gemini** 로 처리합니다(영상은 네이티브 YouTube URL
> 입력이라 자막이 없어도 동작). 텍스트 요약은 `text_provider=claude` 로 Claude 전환도 가능합니다.
> 최종 다이제스트는 항상 **한국어**입니다.

---

## 1. 동작 개요

```
                ┌─────────────────┐   ┌─────────────────┐
  config.yaml → │ collector-text  │   │ collector-video │ ← config.yaml
  (소스/키워드) │ RSS·arXiv·HN    │   │ YouTube 탐지    │   (채널/예산)
                │  (Claude 요약은 │   │ →예산판정/선별  │
                │   다음 단계)    │   │ →Gemini 요약    │
                └────────┬────────┘   └────────┬────────┘
                         │   표준 item 리스트   │
                         └──────────┬──────────┘
                          ┌─────────▼──────────┐
                          │ classifier-summarizer │  Claude 텍스트 요약 +
                          │ 배움/활용 강제 분류    │  교차 중복 제거
                          └─────────┬──────────┘
                          ┌─────────▼──────────┐
                          │ delivery            │  두 트랙 다이제스트 조립 +
                          │ 카카오 나에게 보내기  │  토큰 자동 갱신 + dry-run
                          └────────────────────┘
            스케줄: GitHub Actions cron  또는  OS 스케줄러 (LLM 아님)
```

각 단계는 **격리**되어 있어, 소스 하나가 죽거나 항목 하나의 요약이 실패해도
나머지는 정상적으로 발송됩니다.

---

## 2. 설치

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

`.env.example` 를 복사해 `.env` 를 만들고 비밀값을 채웁니다.

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

---

## 3. 비밀값(.env / 환경변수 / GitHub Secrets)

| 변수 | 용도 | 필수 |
|------|------|------|
| `GEMINI_API_KEY` | 텍스트 요약·분류 + 유튜브 영상 분석(Gemini) | **필요**(텍스트·영상 공용) |
| `ANTHROPIC_API_KEY` | 텍스트 요약·분류(Claude) | 선택(`text_provider=claude` 일 때만) |
| `YOUTUBE_API_KEY` | 영상 길이/메타데이터 정확도 | 선택(없으면 탐지만) |
| `KAKAO_REST_API_KEY` | 카카오 앱 REST API 키(=client_id) | 발송 시 필요 |
| `KAKAO_REFRESH_TOKEN` | access token 자동 재발급용 | 발송 시 필요 |
| `KAKAO_ACCESS_TOKEN` | (선택) 초기 access token | 선택 |
| `KAKAO_CLIENT_SECRET` | 클라이언트 시크릿(앱에서 ON 인 경우) | 조건부 |
| `DRY_RUN` | `true`면 전송 안 함(기본 안전값) | 선택 |

> 코드와 저장소에는 **플레이스홀더만** 둡니다. 실제 값은 `.env`(gitignore) 또는
> GitHub Secrets 에만 저장하세요.

### Gemini 키 (기본 · 텍스트+영상 공용)
[Google AI Studio](https://aistudio.google.com/apikey) 에서 API 키 발급 → `GEMINI_API_KEY`.
이 **키 하나로 텍스트 요약·분류와 유튜브 영상 분석을 모두** 처리합니다(무료 티어면 사실상 0원).
유튜브 영상 네이티브 URL 입력은 프리뷰/무료이며 **하루 8시간 분량** 한도가 있습니다
(파이프라인의 `daily_budget_minutes` 가 이 한도를 지키도록 선별합니다).

### Claude 키 (선택)
텍스트 요약을 Claude 로 돌리고 싶을 때만 필요합니다. `config.yaml` 의
`models.text_provider` 를 `claude` 로 바꾸고 [Anthropic Console](https://console.anthropic.com/)
에서 발급한 키를 `ANTHROPIC_API_KEY` 로 저장하세요. 기본값(`gemini`)이면 이 키는 없어도 됩니다.

---

## 4. 카카오 토큰 발급/갱신

카카오 "나에게 보내기"는 **친구 전송이 아니라 본인 메모 전송**이라 검수·쿼터가 없습니다.
다만 access token(약 6시간)이 만료되므로, **refresh token(약 2개월)** 을 한 번 발급해 저장하면
파이프라인이 이후 access token 을 자동 재발급합니다.

### (1) 앱/권한 준비 (카카오 개발자 콘솔)
1. [콘솔](https://developers.kakao.com/) → 앱 선택 → **앱 설정 > 플랫폼 키**에서 **REST API 키** 확인.
2. **제품 설정 > 카카오 로그인 > 일반**: 상태를 **ON**(활성화).
3. **앱 설정 > 플랫폼 키 > REST API 키 [수정]**: **"카카오 로그인 리다이렉트 URI"** 에
   `https://localhost` 를 추가하고 저장. (이 콘솔 버전은 Redirect URI 를 키 수정 페이지에서 등록)
4. 같은 [수정] 페이지의 **"클라이언트 시크릿"** 이 ON 이면(REST 키 발급 시 기본 ON) 그 **코드**를
   `KAKAO_CLIENT_SECRET` 로 저장한다. OFF 로 두면 비워도 된다.
5. **제품 설정 > 카카오 로그인 > 동의항목**: **카카오톡 메시지 전송(`talk_message`)** 을
   **선택 동의**로 설정(동의 목적 입력 필요).

### (2) 인가 코드 받기
아래 URL 을 브라우저에서 열고 로그인하면, Redirect URI 로 `?code=...` 가 붙어 돌아옵니다. 그 `code` 를 복사하세요.

```
https://kauth.kakao.com/oauth/authorize?client_id=REST_API_KEY&redirect_uri=https://localhost&response_type=code&scope=talk_message
```

> ⚠️ 동의 화면에서 **"카카오톡 메시지 전송"(talk_message) 동의를 반드시 체크**하세요(선택 동의).
> 버튼이 "전체 선택하기"면 눌러서 항목을 체크한 뒤 "동의하고 계속하기"로 넘어갑니다. 체크하지
> 않으면 토큰에 scope 가 없어 전송 시 `insufficient scopes(403)` 가 납니다.
> `localhost` 는 서버가 없어 "연결할 수 없음" 페이지가 뜨지만 정상이며, 주소창의 `code` 만 쓰면 됩니다.

### (3) refresh token 발급(헬퍼 사용)
```bash
python scripts/kakao_get_token.py \
  --rest-api-key  REST_API_KEY \
  --redirect-uri  https://localhost \
  --code          위에서_복사한_코드 \
  --client-secret CLIENT_SECRET   # 클라이언트 시크릿 OFF 면 생략
```
출력된 `KAKAO_REST_API_KEY`, `KAKAO_REFRESH_TOKEN` 을 `.env`(또는 Secrets)에 저장합니다.

> 이후 토큰 갱신은 자동입니다. 파이프라인은 만료 임박 시
> `state/kakao_token.json` 에 새 access token(및 회전된 refresh token)을 저장합니다.
> 이 파일은 gitignore 되어 커밋되지 않습니다.

---

## 5. 설정 바꾸기 (`config/config.yaml`)

운영자가 손으로 고치는 "설정 표면"입니다. 코드를 건드릴 필요가 없습니다.

- **텍스트 소스 추가**: `text_sources.rss` 에 `{name, url}` 추가.
- **arXiv 분류/개수**: `text_sources.arxiv.categories`, `max_results`, `lookback_hours`.
- **Hacker News 키워드/점수**: `text_sources.hackernews.keywords`, `min_points`.
- **요약 비용 상한**: `text_sources.max_items`(최신순 N건만 Claude 요약).
- **유튜브 채널 추가**: `video_sources.channels` 에 `{name, channel_id, trust}` 추가.
  - `channel_id` 는 `UC...` 형식. (채널 페이지 소스/도구로 확인)
- **영상 일일 예산**: `video_sources.daily_budget_minutes`(기본 480=8시간, 더 낮춰도 됨).
  - `YOUTUBE_API_KEY` 가 없어 영상 길이를 모를 때는 가정값(12분)으로 회계하되, **개수 상한
    (= 예산분 ÷ 12분)** 을 함께 적용해 무제한 처리를 막습니다(예: 예산 480분 → 최대 40편).
- **분류 키워드/임계값**: `classifier.frontier_keywords`, `trend_keywords`, `dedup_title_threshold`.
- **발송 형식**: `delivery.max_items_per_track`, `kakao_text_limit`(기본 200=카카오 한도),
  `max_messages`(총 메시지 상한), `title_prefix`.

> **메시지 분할 참고:** 카카오 기본 텍스트 템플릿의 `text` 는 **정확히 200자** 한도입니다.
> 따라서 200자에선 항목 1개가 대략 메시지 1개가 됩니다. 알림 수를 줄이려면
> `max_items_per_track` 를 낮추세요. (커스텀 비기본 템플릿을 직접 구현하면 `kakao_text_limit`
> 를 늘려 한 메시지에 더 담을 수 있습니다.)

### HTML 링크 모드 + GitHub Pages (권장, 200자 한계 해소)
다이제스트를 **HTML 페이지**로 만들어 GitHub Pages 에 올리고, 카톡엔 **짧은 요약 + 전체보기
링크 1건**만 보냅니다. 요약이 잘리지 않고 전문이 보입니다.

설정:
1. `config.yaml` → `delivery.mode: link`, `delivery.site_base_url` 를 본인 Pages 주소로:
   `https://<GitHub사용자명>.github.io/<저장소명>` (예: `https://doctordog.github.io/AI_brif`)
2. 저장소 **Settings → Pages → Source: Deploy from a branch → `gh-pages` / `(root)`**.
   (워크플로가 첫 실행 때 `gh-pages` 브랜치를 만들어 배포합니다. `keep_files` 로 과거 archive 보존.)
3. 카톡 링크는 `…/archive/<날짜>.html`(그날 다이제스트 영구 링크)로 갑니다. `index.html` 은 최신본.

- 로컬 미리보기: `python main.py`(dry-run) → `out/index.html` 생성 → 브라우저로 열어 확인.
- `site_base_url` 이 비어 있으면 자동으로 **text(200자 분할)** 모드로 폴백합니다.
- **공개 주의:** GitHub Pages 는 URL 을 아는 사람이 열람할 수 있습니다(공개 AI 뉴스라 대개 무방).
  HTML 은 외부 피드 내용을 **모두 이스케이프**하고 **http(s) 링크만** 허용해 XSS 를 차단합니다.

---

## 6. 실행 — dry-run(기본) vs 운영 발송

**dry-run 이 기본값**입니다. 실제 전송 없이 최종 메시지를 콘솔과 `out/` 파일로 보여줍니다.

```bash
# 전체 파이프라인 dry-run (전송 안 함)
python main.py                 # 또는  python main.py --dry-run

# 실제 카카오 전송 (상태 갱신)
python main.py --send          # 또는  DRY_RUN=false python main.py
```

각 모듈 단독 실행/검증:
```bash
python -m collectors.collector_text  --dry-run     # 텍스트 수집 결과(JSON)
python -m collectors.collector_video --detect-only # 영상 탐지만(Gemini 호출 X)
python -m processors.classifier_summarizer --in items.json --dry-run
python tests/smoke_test.py                         # 오프라인 핵심 로직 검증
```

> **권장 절차:** 먼저 `python main.py` (dry-run)로 `out/digest_*.txt` 를 확인하고,
> 의도대로 두 트랙이 조립되면 `--send` 로 전환하세요.

---

## 7. 스케줄링 — 둘 중 하나 선택

스케줄링은 LLM 이 아니라 cron/스케줄러가 담당합니다.

### A. GitHub Actions (클라우드, 무인)
1. 저장소 **Settings → Secrets and variables → Actions** 에 위 비밀값들을 등록.
   (저장소엔 절대 넣지 않습니다 — 코드에는 플레이스홀더만.)
2. **Settings → Pages → Source: Deploy from a branch → `gh-pages`** (링크 모드 사용 시).
3. `.github/workflows/daily.yml` 의 `cron` 시각을 조정(UTC 기준).
   - 예: `0 22 * * *` = **07:00 KST**(한국시간). KST = UTC+9.
4. 워크플로는 매 실행 첫 단계로 **시크릿 스캔**(`scripts/check_no_secrets.py`)을 돌려,
   저장소 파일에 키가 섞였으면 **배포를 중단**합니다.
5. 스케줄 실행은 자동으로 실제 발송(`DRY_RUN=false`)이며, **수동 실행**(Run workflow)
   시에는 dry-run 토글을 켤 수 있습니다.

> **상태 보존 주의:** GitHub Actions 는 일회성 환경이라 `state/`(중복 제거·회전된 토큰)가
> 기본적으로 유지되지 않습니다. 워크플로는 `actions/cache` 로 베스트에포트 보존하지만,
> refresh token 이 회전되는 드문 경우엔 `KAKAO_REFRESH_TOKEN` Secret 을 갱신해야 할 수
> 있습니다. **토큰 회전까지 확실히 보존하려면 아래 OS 스케줄러(영구 디스크) 방식을 권장합니다.**

### B. OS 스케줄러 (로컬/서버, 상태 영구 보존)

**Windows 작업 스케줄러** (매일 07:00):
```powershell
schtasks /Create /TN "AI_brief" /SC DAILY /ST 07:00 ^
  /TR "\"C:\path\to\AI_brif\.venv\Scripts\python.exe\" \"C:\path\to\AI_brif\main.py\" --send"
```

**Linux/macOS cron** (`crontab -e`, 매일 07:00):
```cron
0 7 * * * cd /path/to/AI_brif && /path/to/AI_brif/.venv/bin/python main.py --send >> out/cron.log 2>&1
```
(`.env` 가 프로젝트 루트에 있으면 자동 로드됩니다.)

---

## 8. 🔐 보안 — 비밀값 관리 (공개 저장소 기준)

GitHub Pages 를 쓰면 저장소가 공개되므로, 비밀값 관리가 가장 중요합니다.

- **비밀값은 GitHub Secrets / 로컬 `.env`(gitignore) 에만.** 코드·config 에는 플레이스홀더만.
- `.gitignore` 가 `.env*`, `state/*.json`(카카오 토큰), `public/`, `*.pem/*.key` 등을 제외.
- **커밋 전 시크릿 스캐너**: `python scripts/check_no_secrets.py` (CI 첫 단계에서 자동 실행,
  키 패턴 발견 시 배포 중단). 로컬에서도 커밋 전 실행 권장.
- **공개되는 HTML 엔 비밀값이 없습니다** — item 스키마에 비밀값이 없고, 외부 피드 내용은
  모두 이스케이프됩니다.
- 키가 어딘가에 노출됐다면 즉시 재발급: [Anthropic](https://console.anthropic.com/settings/keys),
  [Gemini](https://aistudio.google.com/apikey), 카카오는 [REST 키 수정 > 클라이언트 시크릿 재발급].

---

## 9. 메시지 형식

**링크 모드(권장):** 카톡엔 짧은 안내 + HTML 전체보기 링크 1건.
```
🌏 오늘의 해외 AI 브리핑 (2026-07-01)
📘 배움 5건 · 🛠 활용 5건
👉 전체 보기: https://<user>.github.io/AI_brif/archive/2026-07-01.html
```
링크를 열면 두 트랙 카드(제목·요약 전문·원문 링크)가 담긴 HTML 페이지가 나옵니다.

**text 모드(폴백):** 200자 한도로 항목별 분할 발송(`(1/n)` 표시), 요약은 문장 경계로
줄이고 **URL 은 절대 자르지 않습니다**. 트랙별 항목 수 초과 시 `…외 N건`.

---

## 9. 디렉터리 구조

```
AI_brif/
├─ config/config.yaml          # 운영 설정(소스·키워드·예산·발송)
├─ common/                      # 데이터 계약(item)·설정·비밀값·로깅·상태
├─ collectors/
│  ├─ collector_text.py         # RSS·arXiv·HN 수집
│  └─ collector_video.py        # YouTube 탐지→예산선별→Gemini 요약
├─ processors/
│  └─ classifier_summarizer.py  # Claude 요약 + 배움/활용 분류 + 교차 중복 제거
├─ delivery/
│  ├─ digest.py                 # 두 트랙 메시지 조립·분할
│  └─ kakao.py                  # 나에게 보내기 + 토큰 자동 갱신 + dry-run
├─ scripts/kakao_get_token.py   # 카카오 refresh token 최초 발급 헬퍼
├─ tests/smoke_test.py          # 오프라인 핵심 로직 검증
├─ state/                       # (gitignore) 토큰·dedup·발송 이력
├─ main.py                      # 오케스트레이터(엔드투엔드)
├─ .github/workflows/daily.yml  # 스케줄러(클라우드)
├─ .env.example                 # 비밀값 템플릿
└─ requirements.txt
```

---

## 10. 자주 묻는 문제

- **`KAKAO_REFRESH_TOKEN 이 없어 …`** → 4장 절차로 refresh token 을 발급해 `.env` 에 저장.
- **영상이 안 보임** → `GEMINI_API_KEY` 미설정 시, 요약 없는 영상은 품질 보호를 위해 이번
  다이제스트에서 제외됩니다. 탐지 결과만 보려면 `python -m collectors.collector_video --detect-only`.
- **텍스트 요약이 "(요약 생략 …)"** → 텍스트 제공자 키 미설정 시의 폴백 표시입니다
  (기본 `gemini` → `GEMINI_API_KEY`, `text_provider=claude` → `ANTHROPIC_API_KEY`).
- **소스 하나가 404** → 자동으로 건너뛰고 나머지는 정상 발송됩니다(로그에 경고).
- **알림이 너무 많음** → `delivery.max_items_per_track` 를 낮추세요(200자 한도 때문).

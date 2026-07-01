# PROGRESS

각 모듈은 완료 시 한 줄씩 기록한다: `[모듈명 / 상태(done|blocked|wip) / 산출 파일 / 미해결 이슈]`

| 모듈 | 상태 | 산출 파일 | 미해결 이슈 |
|------|------|-----------|-------------|
| orchestrator(설정·계약·구조) | done | config/config.yaml, common/*, main.py | - |
| collector-text | done | collectors/collector_text.py | arXiv/HN 네트워크 의존(런타임 키 불필요) |
| collector-video | done | collectors/collector_video.py | Gemini/YouTube 키 없으면 탐지만 동작(요약 skip) |
| classifier-summarizer | done | processors/classifier_summarizer.py | ANTHROPIC_API_KEY 없으면 키워드 폴백 분류 |
| delivery | done | delivery/digest.py, delivery/kakao.py | KAKAO 토큰 미설정 시 dry-run만 가능 |
| scheduler | done | .github/workflows/daily.yml, README(OS cron) | - |
| docs | done | README.md, .env.example | - |

## 데이터 계약 (item 스키마) — 모든 에이전트 공통

```json
{
  "id": "안정적 해시(중복 제거 키)",
  "source": "소스 이름",
  "type": "text | video",
  "title": "제목",
  "url": "원문 링크",
  "raw_or_transcript": "텍스트 본문(영상은 빈 문자열 — Gemini가 URL을 직접 읽음)",
  "summary": "한국어 요약",
  "track": "FRONTIER | TREND | null",
  "collected_at": "ISO8601 수집 시각"
}
```

추가(내부) 필드: `published_at`, `duration_sec`, `channel`, `meta{}`.

## 빌드 순서 / 의존성
- collector-text 와 collector-video 는 서로 의존하지 않음 → 병렬 빌드.
- classifier-summarizer 는 두 수집기 완료 후.
- delivery 는 분류 완료 후.

## 빌드 중 발견·수정한 이슈
- **RSS 전체 이력 폭주**: 다수 RSS 가 전체 이력(1000+건)을 반환 → `rss_lookback_hours`(48h)
  윈도 + `max_items`(50) 상한 추가. Claude 요약 비용을 일일 수준으로 묶음.
- **seen 과다 마킹**: 발송은 트랙별 상위 N건뿐인데 처리한 전 항목을 seen 처리하던 문제 →
  `digest.selected_items()` 로 실제 발송 항목만 seen 갱신.
- **Windows cp949 인코딩**: 이모지/한글 출력 시 UnicodeEncodeError → stdout/stderr UTF-8 재설정.
- **카카오 200자 한도 정합**: 공식 문서상 기본 텍스트 템플릿 text=정확히 200자 확인 →
  `max_items_per_track`(5)·`max_messages`(16) 를 한도와 정합되게 조정(내용 무손실).

## SELF_VERIFICATION (코드 추적 결과)
- (a) 유튜브 = Gemini 네이티브 URL → `collectors/collector_video.py:281`
  `types.Part(file_data=types.FileData(file_uri=item.url))` (한 호출로 한국어요약+트랙).
- (b) 모든 item 강제 분류 → `processors/classifier_summarizer.py:197-198`
  최종 보증 루프 `if track not in VALID_TRACKS: keyword_classify(...)`.
- (c) 운영 전 dry-run 검증 → `delivery/kakao.py:deliver(dry_run=True)` 기본값, 전송 없이
  `out/digest_*.txt` + 콘솔 출력. (`common/env.is_dry_run` 기본 True)

## 런타임 검증
- `tests/smoke_test.py` 통과: 강제 분류·교차 중복 제거·두 트랙 조립·dry-run·소스 격리.
- `python main.py --dry-run` 라이브 소스로 통과: 텍스트 50건 수집→두 트랙 13개 메시지 조립,
  404 소스 자동 격리, 실제 전송 없음.

## 라이브 검증(실키)
- 유튜브→Gemini 네이티브 URL: 실제 영상을 자막 없이 분석, 한국어 요약+FRONTIER 분류(한 호출). OK
- 텍스트→Claude: 실제 RSS 항목을 한국어로 요약·정확 분류(예: DiScoFormer→FRONTIER). OK
- 통합 dry-run: Claude 텍스트 + Gemini 영상이 한 다이제스트의 두 트랙으로 조립, 전송 0건. OK
- **카카오 나에게 보내기: 실제 전송 성공(result_code 0)**. 브라우저로 콘솔 설정(로그인 ON·
  REST키 Redirect URI·talk_message 선택동의·클라이언트 시크릿)→OAuth 동의→토큰 교환(scope=talk_message)
  →실 파이프라인 --send 로 5개 메시지 전송(sent=5). 토큰 갱신도 client_secret 포함해 성공.
- 코드 보강: kakao.refresh_access_token/ kakao_get_token 에 client_secret 지원, .env.example·daily.yml·
  README 에 KAKAO_CLIENT_SECRET 및 실제 콘솔 등록 위치(플랫폼 키>REST키 수정) 반영.

## 적대적 코드 감사(5차원 병렬 + 검증) 후 수정
27건 확정(high 4 / medium 9 / low 14). 수정 완료:
- [HIGH] 예산 회계가 추정 길이 의존 → 길이 추정 시 **개수 상한**(예산분/12) 강제. (collector_video)
- [HIGH] 예산/상한 탈락 영상 영구 유실 → 탐지 윈도를 last_run 으로 좁히지 않고 **lookback+seen-dedup** 사용.
- [HIGH] Claude 요약 실패 시 항목 침묵 누락 → except 에서 **폴백 요약+키워드 분류로 보존**. (classifier)
- [HIGH] actions/cache run_id 관용 패턴/토큰 보존 한계 → 주석·README 로 명확화(OS 스케줄러 권장).
- [MED] 발송 안 된 항목 seen 처리(#5/#9) → build_messages 가 **실제 발송 item_id 집합** 반환, 그것만 seen.
- [MED] 페이지표시로 200자 초과(#10) → tail/prefix 모두 eff 버퍼 내에서 처리.
- [MED] duration days/weeks 미파싱(#7) → 정규식 확장 + 0초 폴백.
- [MED] 제목 변동/utm 으로 dedup 우회(#8) → id 를 **정규화 URL** 기준으로(youtube v= 유지).
- [MED] _extract_json greedy 취약(#12) → 공통 `extract_json`(raw_decode 스캔)으로 교체.
- [MED] 빈 summary 발송(#11) → 텍스트도 폴백 스니펫 보정.
- [LOW] env 'xxxx' 오탐(#25)→x{6,}만, 토큰 응답 노출(#26)→[:300], tz-naive(#27), 키 없을 때 영상 제외(#15).
- 회귀 테스트 `tests/test_fixes.py` 7건으로 위 수정 증명(전부 통과).
- 잔여(문서화된 트레이드오프): CI 캐시 토큰 평문/회전 보존(#6/#13) — 확실한 보존은 OS 스케줄러 권장.

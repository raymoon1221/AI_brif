"""
오케스트레이터 — 수집 → 요약/분류 → 두 트랙 다이제스트 조립 → 카카오 '나에게 보내기'.

스케줄러(GitHub Actions cron / OS 스케줄러)가 이 스크립트를 호출한다.

사용:
    python main.py                 # dry-run(기본): 실제 전송 없이 미리보기
    python main.py --send          # 실제 카카오 전송(상태 갱신)
    DRY_RUN=false python main.py   # 환경변수로도 전환 가능

단계 분리(GitHub Pages 링크 모드에서 '배포 성공 후에만 상태 갱신'을 보장):
    python main.py --stage build   # 수집·조립·HTML 렌더 → state/pending.json 저장(발송/상태 X)
    #  ↑ 이후 워크플로가 Pages 배포
    python main.py --stage notify  # pending 을 읽어 카카오 발송 + (성공 시) 상태 갱신
기본 --stage all 은 조립~발송을 한 프로세스에서 수행(로컬/텍스트 모드/미리보기용).

각 단계는 격리되어 한 소스/항목 실패가 전체를 중단시키지 않는다.
실제 전송이 일어난 경우에만 상태(중복 제거·마지막 처리 시각·발송 이력)를 갱신한다.
"""
from __future__ import annotations

import argparse
from datetime import datetime

from common.config import PROJECT_ROOT, load_config
from common.env import is_dry_run
from common.item import Item, FRONTIER, TREND
from common.logging_setup import get_logger
from common.state import read_json, write_json

from collectors import collector_text, collector_video
from processors import classifier_summarizer
from delivery import digest, kakao
from render import html as html_render

log = get_logger("orchestrator")


def _today_str() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _pending_path(cfg):
    return cfg.state_dir / "pending.json"


def _append_sent_log(cfg, date_str: str, dry_run: bool, items: list[Item], result: dict) -> None:
    path = cfg.state_dir / "sent_log.json"
    history = read_json(path, [])
    if not isinstance(history, list):
        history = []
    history.append({
        "date": date_str,
        "dry_run": dry_run,
        "delivered": result.get("sent", 0),
        "failed": result.get("failed", 0),
        "counts": {
            "FRONTIER": sum(1 for it in items if it.track == FRONTIER),
            "TREND": sum(1 for it in items if it.track == TREND),
        },
        "ids": [it.id for it in items],
    })
    write_json(path, history[-60:])  # 최근 60회만 보관


def _collect_and_process(cfg, date_str: str) -> list[Item]:
    """수집(텍스트/영상, 서로 독립·격리) → 요약/분류/중복제거. 처리된 item 리스트 반환."""
    text_items: list[Item] = []
    try:
        text_items = collector_text.collect(cfg, use_dedup=True)
    except Exception as e:  # noqa: BLE001
        log.error("텍스트 수집 단계 실패(빈 결과로 계속): %s", e)

    video_items: list[Item] = []
    try:
        video_items = collector_video.process(cfg, summarize=True, use_dedup=True, advance_state=False)
    except Exception as e:  # noqa: BLE001
        log.error("영상 수집 단계 실패(빈 결과로 계속): %s", e)

    collected = text_items + video_items
    log.info("수집 합계: 텍스트 %d + 영상 %d = %d건", len(text_items), len(video_items), len(collected))
    if not collected:
        return []
    try:
        return classifier_summarizer.process(collected, cfg)
    except Exception as e:  # noqa: BLE001
        log.error("분류/요약 단계 실패: %s", e)
        return [it for it in collected if it.track in (FRONTIER, TREND) and it.summary]


def _assemble(cfg, processed: list[Item], date_str: str, dry_run: bool) -> dict:
    """다이제스트 조립. 링크 모드면 HTML 렌더까지 수행. 반환: pending 딕셔너리."""
    d = cfg.get("delivery", {}) or {}
    mode = d.get("mode", "text")
    site = (d.get("site_base_url") or "").strip()

    if mode == "link" and site:
        html_str = html_render.render_digest(processed, cfg, date_str)
        # 운영은 배포 디렉터리(public/), dry-run 은 미리보기용 out/ 에 쓴다.
        pub_dir = (cfg.out_dir if dry_run else PROJECT_ROOT / d.get("publish_dir", "public"))
        paths = html_render.write_digest(html_str, pub_dir, date_str)
        url = f"{site.rstrip('/')}/archive/{date_str}.html"
        msg = digest.build_link_message(processed, cfg, date_str, url)
        log.info("링크 모드: HTML=%s, 링크=%s", paths["index"], url)
        # HTML 에 전량 실리므로 처리된 모든 항목이 '발송 대상'.
        return {"date_str": date_str, "mode": "link", "link_url": url,
                "messages": [msg], "items": [it.to_dict() for it in processed]}

    if mode == "link" and not site:
        log.warning("mode=link 이지만 site_base_url 이 비어 있어 text(200자 분할) 모드로 폴백합니다. "
                    "GitHub Pages 배포를 쓰려면 config 의 delivery.site_base_url 을 채우세요.")
    messages, delivered_ids = digest.build_messages(processed, cfg, date_str)
    return {"date_str": date_str, "mode": "text", "link_url": None, "messages": messages,
            "items": [it.to_dict() for it in processed if it.id in delivered_ids]}


def _deliver_and_update(cfg, pending: dict, dry_run: bool) -> dict:
    """pending 을 발송하고, 실제 전송 성공 시에만 상태(seen/이력)를 갱신한다."""
    items = [Item.from_dict(x) for x in pending.get("items", [])]
    result = kakao.deliver(pending["messages"], cfg, dry_run=dry_run,
                           date_str=pending["date_str"], link_url=pending.get("link_url"))
    log.info("발송 결과: %s", result)

    if not dry_run and result.get("sent", 0) > 0:
        collector_text.mark_seen(cfg, items)
        collector_video.mark_seen(cfg, items)
        try:
            collector_video._save_last_run(cfg)
        except Exception:  # noqa: BLE001
            pass
        _append_sent_log(cfg, pending["date_str"], dry_run, items, result)
        log.info("상태 갱신 완료(발송 %d건 seen 처리/마지막 처리 시각/발송 이력).", len(items))
    else:
        _append_sent_log(cfg, pending["date_str"], dry_run, items, result)
        log.info("dry-run/미전송: 중복 제거·마지막 처리 시각 상태는 변경하지 않음.")
    return result


def run(config_path: str | None, dry_run: bool, stage: str = "all") -> int:
    cfg = load_config(config_path)
    date_str = _today_str()
    log.info("=== AI_brief 시작 (%s, dry_run=%s, stage=%s) ===", date_str, dry_run, stage)

    if stage in ("all", "build"):
        processed = _collect_and_process(cfg, date_str)
        if not processed:
            log.warning("수집/처리 후 남은 항목이 없습니다. 종료.")
            return 0
        pending = _assemble(cfg, processed, date_str, dry_run)
        if stage == "build":
            # 발송/상태갱신을 하지 않고 pending 만 저장 → 배포 성공 후 notify 단계에서 처리.
            write_json(_pending_path(cfg), pending)
            log.info("build 완료: pending 저장(메시지 %d, 발송대상 %d건). 발송은 notify 단계.",
                     len(pending["messages"]), len(pending["items"]))
            return 0
    else:  # stage == "notify"
        pending = read_json(_pending_path(cfg), None)
        if not pending or not pending.get("messages"):
            log.warning("notify: pending 이 없거나 비어 있습니다. 종료.")
            return 0

    _deliver_and_update(cfg, pending, dry_run)

    if stage == "notify":
        # pending 은 소비 처리(다음 실행 재사용 방지). 발송 실패 시 항목은 seen 안 되어 다음날 재시도됨.
        try:
            _pending_path(cfg).unlink()
        except OSError:
            pass

    log.info("=== 완료 ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI_brief 파이프라인 오케스트레이터")
    ap.add_argument("--config", default=None, help="config.yaml 경로")
    ap.add_argument("--stage", choices=["all", "build", "notify"], default="all",
                    help="all(기본)=조립~발송 / build=조립·HTML·pending 저장만 / notify=pending 발송+상태갱신")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--send", action="store_true", help="실제 카카오 전송(상태 갱신)")
    g.add_argument("--dry-run", action="store_true", help="강제 dry-run(기본값)")
    args = ap.parse_args(argv)

    cli_override = False if args.send else (True if args.dry_run else None)
    dry = is_dry_run(cli_override)
    return run(args.config, dry, args.stage)


if __name__ == "__main__":
    raise SystemExit(main())

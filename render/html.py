"""
두 트랙(배움/활용) 다이제스트를 정적 HTML 로 렌더링한다(GitHub Pages 공개용).

[보안]
- 외부 피드에서 온 모든 텍스트(제목·요약·출처)는 html.escape 로 이스케이프한다(XSS 방지).
- 링크는 http/https 스킴만 허용한다(javascript: 등 위험 스킴 차단).
- 외부 리소스/JS/트래커를 넣지 않는다(CSS 인라인, 완전 자립형).
- 비밀값은 어떤 경우에도 렌더 입력에 포함되지 않는다(item 스키마에 비밀값 없음).
"""
from __future__ import annotations

import html
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    from common.config import Config
    from common.item import Item, FRONTIER, TREND
    from common.logging_setup import get_logger
except ModuleNotFoundError:
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from common.config import Config
    from common.item import Item, FRONTIER, TREND
    from common.logging_setup import get_logger

log = get_logger("render-html")

SECTIONS = [
    (FRONTIER, "📘 배움", "FRONTIER", "새 모델·아키텍처·논문·기법 — 원리를 배우는 콘텐츠"),
    (TREND, "🛠 활용", "TREND", "제품·시장·워크플로우 — 바로 써먹는 콘텐츠"),
]


def _safe_url(url: str) -> str | None:
    """http/https 링크만 허용. 그 외(javascript:, data: 등)는 None."""
    try:
        s = urlsplit((url or "").strip())
    except ValueError:
        return None
    if s.scheme.lower() in ("http", "https") and s.netloc:
        return url.strip()
    return None


def _esc(text: str) -> str:
    return html.escape((text or "").strip(), quote=True)


def _card(idx: int, it: Item) -> str:
    title = _esc(it.title) or "(제목 없음)"
    summary = _esc(it.summary)
    source = _esc(it.source)
    url = _safe_url(it.url)
    badge = "🎬 영상" if it.type == "video" else "📄 글"

    # 제목: 안전한 링크가 있을 때만 <a>, href 는 escape 된 값.
    if url:
        title_html = f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer nofollow">{title}</a>'
        link_row = f'<div class="link"><a href="{_esc(url)}" target="_blank" rel="noopener noreferrer nofollow">{_esc(url)}</a></div>'
    else:
        title_html = title
        link_row = ""

    return f"""      <article class="card">
        <div class="meta"><span class="num">{idx}</span><span class="src">{source}</span><span class="type">{badge}</span></div>
        <h3 class="title">{title_html}</h3>
        <p class="summary">{summary}</p>
{link_row}
      </article>"""


def render_digest(items: list[Item], cfg: Config, date_str: str) -> str:
    d = cfg.get("delivery", {}) or {}
    title_prefix = _esc(d.get("title_prefix", "🌏 오늘의 해외 AI 브리핑"))

    buckets: dict[str, list[Item]] = {FRONTIER: [], TREND: []}
    for it in items:
        if it.track in buckets:
            buckets[it.track].append(it)

    sections_html = []
    for track, emoji_ko, en, desc in SECTIONS:
        lst = buckets.get(track, [])
        cards = "\n".join(_card(i, it) for i, it in enumerate(lst, 1)) if lst \
            else '      <p class="empty">오늘 해당 항목이 없습니다.</p>'
        sections_html.append(f"""    <section class="track track-{en.lower()}">
      <h2>{emoji_ko}<span class="en">{en}</span></h2>
      <p class="track-desc">{_esc(desc)} · {len(lst)}건</p>
{cards}
    </section>""")

    gen = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    total = sum(len(v) for v in buckets.values())

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title_prefix} ({_esc(date_str)})</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
    "Malgun Gothic", "Segoe UI", Roboto, sans-serif; line-height:1.6;
    background:#f6f7f9; color:#1a1a1a; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#15171a; color:#e6e6e6; }}
    .card {{ background:#1e2126 !important; border-color:#2c313a !important; }}
    header {{ background:#1e2126 !important; border-color:#2c313a !important; }}
    a {{ color:#6bb3ff !important; }} }}
  .wrap {{ max-width: 760px; margin: 0 auto; padding: 16px; }}
  header {{ background:#fff; border:1px solid #e6e8eb; border-radius:14px;
    padding:20px; margin-bottom:16px; }}
  header h1 {{ margin:0 0 4px; font-size:1.35rem; }}
  header .date {{ color:#888; font-size:.92rem; }}
  header .count {{ margin-top:8px; font-size:.92rem; color:#555; }}
  .track {{ margin: 22px 0 8px; }}
  .track h2 {{ font-size:1.15rem; margin:0 0 2px; display:flex; align-items:baseline; gap:8px; }}
  .track h2 .en {{ font-size:.72rem; color:#999; letter-spacing:.08em; }}
  .track-desc {{ color:#888; font-size:.85rem; margin:0 0 12px; }}
  .card {{ background:#fff; border:1px solid #e6e8eb; border-radius:12px;
    padding:14px 16px; margin-bottom:10px; }}
  .card .meta {{ display:flex; gap:8px; align-items:center; font-size:.75rem; color:#999; margin-bottom:4px; }}
  .card .num {{ font-weight:700; color:#4a90d9; }}
  .card .title {{ margin:2px 0 6px; font-size:1.02rem; line-height:1.4; }}
  .card .title a {{ color:inherit; text-decoration:none; }}
  .card .title a:hover {{ text-decoration:underline; }}
  .card .summary {{ margin:0 0 8px; font-size:.95rem; }}
  .card .link a {{ font-size:.8rem; color:#4a90d9; word-break:break-all; }}
  .empty {{ color:#aaa; font-style:italic; }}
  footer {{ text-align:center; color:#aaa; font-size:.8rem; margin:24px 0 8px; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>{title_prefix}</h1>
      <div class="date">{_esc(date_str)}</div>
      <div class="count">총 {total}건 · 📘 배움 {len(buckets[FRONTIER])} · 🛠 활용 {len(buckets[TREND])}</div>
    </header>
{chr(10).join(sections_html)}
    <footer>자동 생성 · {gen} · 원문 링크는 각 항목의 출처를 따릅니다.</footer>
  </div>
</body>
</html>"""


def write_digest(html_str: str, out_dir: Path, date_str: str) -> dict[str, Path]:
    """index.html(최신) + archive/<date>.html(보관) 로 저장. 반환 경로 딕셔너리."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "archive").mkdir(parents=True, exist_ok=True)
    index_p = out_dir / "index.html"
    archive_p = out_dir / "archive" / f"{date_str}.html"
    index_p.write_text(html_str, encoding="utf-8")
    archive_p.write_text(html_str, encoding="utf-8")
    log.info("HTML 저장: %s (+ archive/%s.html)", index_p, date_str)
    return {"index": index_p, "archive": archive_p}

"""报告渲染：主题模板 + 内联 SVG 图表，生成不依赖外部资源的单文件 HTML。persona 与后续 summary 共用。"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from ..core.errors import EngineError
from ..core.state import atomic_write_text
from .templating import Loader

THEMES_DIR = Path(__file__).resolve().parent / "themes"
GENERATOR = "dsh-wechat-one"


def themes() -> list[str]:
    return sorted(p.name for p in THEMES_DIR.iterdir() if p.is_dir() and (p / "style.css").is_file())


def theme_info(theme: str) -> dict[str, Any]:
    if theme not in themes():
        raise EngineError("THEME_NOT_FOUND")
    try:
        return json.loads((THEMES_DIR / theme / "theme.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"name": theme, "palette": {}}


def render_html(template: str, context: dict[str, Any], theme: str) -> str:
    info = theme_info(theme)
    css = (THEMES_DIR / "_shared" / "base.css").read_text(encoding="utf-8") + "\n" + \
        (THEMES_DIR / theme / "style.css").read_text(encoding="utf-8")
    loader = Loader([THEMES_DIR / theme, THEMES_DIR / "_shared"])
    return loader.load(template).render({**context, "css": css, "theme": info, "generator": GENERATOR})


def bar_chart(values: list[float], labels: list[str], palette: dict[str, str], *, width: int = 640,
              height: int = 150, highlight: int | None = None, label_every: int = 1) -> str:
    """简单柱状图（内联 SVG）。"""
    n = max(1, len(values))
    top = max(values) if values and max(values) > 0 else 1
    pad_l, pad_b, pad_t = 6, 20, 8
    inner_w, inner_h = width - pad_l * 2, height - pad_b - pad_t
    bw = inner_w / n
    bar, alt = palette.get("bar", "#c96442"), palette.get("bar_alt", "#e6c3ae")
    axis, text = palette.get("axis", "#8a8578"), palette.get("text", "#3d3929")
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" class="chart">',
             f'<line x1="{pad_l}" y1="{pad_t + inner_h}" x2="{width - pad_l}" y2="{pad_t + inner_h}" stroke="{axis}" stroke-width="1"/>']
    for i, v in enumerate(values):
        h = inner_h * (v / top)
        x = pad_l + i * bw + bw * 0.15
        y = pad_t + inner_h - h
        color = bar if highlight is None or i == highlight else alt
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.7:.1f}" height="{h:.1f}" fill="{color}" rx="1">'
                     f'<title>{html.escape(labels[i] if i < len(labels) else "")}: {v:g}</title></rect>')
        if i % label_every == 0 and i < len(labels):
            parts.append(f'<text x="{pad_l + i * bw + bw / 2:.1f}" y="{height - 5}" font-size="10" fill="{text}" '
                         f'text-anchor="middle">{html.escape(labels[i])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def safe_slug(text: str, limit: int = 24) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text or "").strip("_")
    return (slug or "report")[:limit]


def write_report(directory: Path, markdown: str, html_text: str, theme: str) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    md_path = directory / "report.md"
    html_path = directory / f"report.{theme}.html"
    atomic_write_text(md_path, markdown)
    atomic_write_text(html_path, html_text)
    return {"report_md": str(md_path), "report_html": str(html_path)}

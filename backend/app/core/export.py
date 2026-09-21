"""概化图导出（SVG）。"""
from __future__ import annotations

import math
from html import escape


def _fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def schematic_to_svg(sch: dict, title: str | None = None, width: int = 1600) -> str:
    """把概化图渲染成独立 SVG（可用于汇报插图和打印）。"""
    bbox = sch.get("bbox") or [0, 0, 100, 100]
    pad = 70.0
    x0, y0, x1, y1 = bbox
    w = max(1.0, x1 - x0) + pad * 2
    h = max(1.0, y1 - y0) + pad * 2
    scale = min(1.0, width / w)
    vw, vh = w * scale, h * scale

    edges = sch.get("edges", [])
    nodes = sch.get("nodes", [])
    stations = sch.get("stations", [])
    lakes = sch.get("lakes", [])
    orders = [int(e.get("order", 1)) for e in edges] or [1]
    max_order = max(orders)

    def X(v: float) -> float:
        return (v - x0 + pad) * scale

    def Y(v: float) -> float:
        return (v - y0 + pad) * scale

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt(vw)}" height="{_fmt(vh)}" '
        f'viewBox="0 0 {_fmt(vw)} {_fmt(vh)}" font-family="Microsoft YaHei, Source Han Sans SC, sans-serif">'
    )
    parts.append(f'<rect width="100%" height="100%" fill="#ffffff"/>')

    if title:
        parts.append(
            f'<text x="{_fmt(pad * scale)}" y="{_fmt(26)}" font-size="21" font-weight="700" '
            f'fill="#12324f">{escape(title)}</text>'
        )

    # 湖泊
    for lk in lakes:
        pts = lk.get("points") or []
        if len(pts) < 3:
            continue
        d = " ".join(f"{_fmt(X(p[0]))},{_fmt(Y(p[1]))}" for p in pts)
        parts.append(
            f'<polygon points="{d}" fill="#cfe8f7" stroke="#4a90c4" stroke-width="1.4" stroke-linejoin="round"/>'
        )
    # 水体
    for e in edges:
        pts = e.get("points") or []
        if len(pts) < 2:
            continue
        order = int(e.get("order", 1))
        wd = 1.3 + order * 1.0
        if order == max_order:
            color, wd = "#1e6fa8", wd + 1.2
        elif order >= max_order - 1:
            color = "#2f8ac4"
        else:
            color = "#57a7d6"
        d = " ".join(f"{_fmt(X(p[0]))},{_fmt(Y(p[1]))}" for p in pts)
        parts.append(
            f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{_fmt(wd)}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="{0.95 if e.get("kind") != "extra" else 0.6}"/>'
        )
        name = e.get("name") or ""
        if name and len(pts) >= 2:
            mi = len(pts) // 2
            mx, my = (X(pts[mi][0]) + X(pts[mi - 1][0])) / 2, (Y(pts[mi][1]) + Y(pts[mi - 1][1])) / 2
            parts.append(
                f'<text x="{_fmt(mx + 4)}" y="{_fmt(my - 4)}" font-size="11" fill="#3d6b8c">{escape(name)}</text>'
            )
    # 节点
    for n in nodes:
        kind = n.get("kind")
        cx, cy = X(n["x"]), Y(n["y"])
        if kind == "outlet":
            r = 7
            parts.append(
                f'<polygon points="{_fmt(cx)},{_fmt(cy + r)} {_fmt(cx - r)},{_fmt(cy - r)} '
                f'{_fmt(cx + r)},{_fmt(cy - r)}" fill="#f6f9fc" stroke="#1e6fa8" stroke-width="1.6"/>'
            )
            parts.append(
                f'<text x="{_fmt(cx)}" y="{_fmt(cy + 24)}" font-size="12" fill="#1e6fa8" '
                f'text-anchor="middle">流域出口</text>'
            )
        elif kind == "junction":
            parts.append(f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="3.2" fill="#2f8ac4"/>')
        elif kind == "source":
            parts.append(
                f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="2.6" fill="none" stroke="#4a90c4" stroke-width="1.2"/>'
            )

    # 站点
    for s in stations:
        cx, cy = X(s["x"]), Y(s["y"])
        hydro = s.get("type") == "hydro"
        color = "#c0392b" if hydro else "#1f7a4d"
        if s.get("edge_id"):
            ex, ey = s.get("on_edge") or [s["x"], s["y"]]
            parts.append(
                f'<line x1="{_fmt(cx)}" y1="{_fmt(cy)}" x2="{_fmt(X(ex))}" y2="{_fmt(Y(ey))}" '
                f'stroke="{color}" stroke-width="0.8" stroke-dasharray="3 3" opacity="0.6"/>'
            )
        if hydro:
            parts.append(f'<rect x="{_fmt(cx - 5)}" y="{_fmt(cy - 5)}" width="10" height="10" fill="{color}"/>')
        else:
            parts.append(f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="4.6" fill="#ffffff" stroke="{color}" stroke-width="1.8"/>')
        parts.append(
            f'<text x="{_fmt(cx + 8)}" y="{_fmt(cy - 7)}" font-size="12" font-weight="600" fill="{color}">'
            f"{escape(s.get('name') or '')}</text>"
        )

    # 图例
    lx, ly = pad * scale + 8, vh - 84
    parts.append(f'<rect x="{_fmt(lx - 8)}" y="{_fmt(ly - 18)}" width="212" height="80" fill="#ffffff" '
                 f'stroke="#dfe6ec" rx="5"/>')
    parts.append(f'<text x="{_fmt(lx)}" y="{_fmt(ly)}" font-size="12" font-weight="700" fill="#33556e">图例</text>')
    parts.append(f'<rect x="{_fmt(lx)}" y="{_fmt(ly + 10)}" width="10" height="10" fill="#c0392b"/>')
    parts.append(f'<text x="{_fmt(lx + 16)}" y="{_fmt(ly + 19)}" font-size="12" fill="#33556e">水文站</text>')
    parts.append(f'<circle cx="{_fmt(lx + 5)}" cy="{_fmt(ly + 43)}" r="4.6" fill="#fff" stroke="#1f7a4d" stroke-width="1.8"/>')
    parts.append(f'<text x="{_fmt(lx + 16)}" y="{_fmt(ly + 47)}" font-size="12" fill="#33556e">雨量站</text>')
    parts.append(f'<rect x="{_fmt(lx + 96)}" y="{_fmt(ly + 10)}" width="16" height="8" fill="#1e6fa8"/>')
    parts.append(f'<text x="{_fmt(lx + 118)}" y="{_fmt(ly + 19)}" font-size="12" fill="#33556e">干流</text>')
    parts.append(f'<polygon points="{_fmt(lx + 96)},{_fmt(ly + 47)} {_fmt(lx + 92)},{_fmt(ly + 37)} '
                 f'{_fmt(lx + 100)},{_fmt(ly + 37)}" fill="#f6f9fc" stroke="#1e6fa8" stroke-width="1.6"/>')
    parts.append(f'<text x="{_fmt(lx + 118)}" y="{_fmt(ly + 47)}" font-size="12" fill="#33556e">流域出口</text>')

    parts.append("</svg>")
    return "\n".join(parts)

from __future__ import annotations

import math


COLORS = ["#2563eb", "#dc2626", "#059669", "#7c3aed", "#ea580c", "#0891b2"]


def radar_svg(series: list[dict], categories: list[str], *, size: int = 280) -> str:
    if not series or not categories:
        return "<svg width='280' height='120' viewBox='0 0 280 120'><text x='10' y='60'>No runs yet</text></svg>"
    center = size / 2
    radius = size * 0.38
    axes = []
    for idx, category in enumerate(categories):
        angle = -math.pi / 2 + (2 * math.pi * idx / len(categories))
        x = center + math.cos(angle) * radius
        y = center + math.sin(angle) * radius
        axes.append((category, x, y, angle))
    parts = [f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}' role='img'>"]
    for ring in range(1, 6):
        r = radius * ring / 5
        parts.append(f"<circle cx='{center:.1f}' cy='{center:.1f}' r='{r:.1f}' fill='none' stroke='#e5e7eb'/>")
    for category, x, y, _ in axes:
        parts.append(f"<line x1='{center:.1f}' y1='{center:.1f}' x2='{x:.1f}' y2='{y:.1f}' stroke='#d1d5db'/>")
        label_x = center + (x - center) * 1.13
        label_y = center + (y - center) * 1.13
        parts.append(f"<text x='{label_x:.1f}' y='{label_y:.1f}' font-size='10' text-anchor='middle'>{_escape(category)}</text>")
    for idx, row in enumerate(series):
        points = []
        for category, _, _, angle in axes:
            value = float(row.get("per_category", {}).get(category, 0)) / 10.0
            x = center + math.cos(angle) * radius * value
            y = center + math.sin(angle) * radius * value
            points.append(f"{x:.1f},{y:.1f}")
        color = COLORS[idx % len(COLORS)]
        parts.append(f"<polygon points='{' '.join(points)}' fill='{color}' fill-opacity='0.18' stroke='{color}' stroke-width='2'/>")
    parts.append("</svg>")
    return "".join(parts)


def bar_svg(values: dict[str, float], *, width: int = 640) -> str:
    if not values:
        return "<svg width='640' height='80' viewBox='0 0 640 80'><text x='10' y='45'>No category scores</text></svg>"
    row_h = 28
    height = 24 + len(values) * row_h
    parts = [f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' role='img'>"]
    for idx, (category, value) in enumerate(sorted(values.items())):
        y = 18 + idx * row_h
        bar_w = max(1, (width - 180) * float(value) / 10.0)
        parts.append(f"<text x='0' y='{y + 14}' font-size='12'>{_escape(category)}</text>")
        parts.append(f"<rect x='150' y='{y}' width='{bar_w:.1f}' height='16' fill='#2563eb' rx='2'/>")
        parts.append(f"<text x='{155 + bar_w:.1f}' y='{y + 13}' font-size='12'>{value:.2f}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


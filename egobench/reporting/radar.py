from __future__ import annotations

import math


# Seline-inspired palette: Chartwell Blue lead + low-saturation supporting hues.
COLORS = ["#3ba6f1", "#0c0a09", "#7c3aed", "#059669", "#ea580c", "#0891b2"]


def radar_svg(series: list[dict], categories: list[str], *, size: int = 320) -> str:
    if not series or not categories:
        return _empty_svg("No category scores yet", width=size, height=size)
    center = size / 2
    radius = size * 0.36
    axes = []
    for idx, category in enumerate(categories):
        angle = -math.pi / 2 + (2 * math.pi * idx / len(categories))
        x = center + math.cos(angle) * radius
        y = center + math.sin(angle) * radius
        axes.append((category, x, y, angle))
    parts = [
        f"<svg width='100%' height='{size}' viewBox='0 0 {size} {size}' "
        f"role='img' aria-label='Per-category radar chart' class='ebx-radar'>"
    ]
    for ring in range(1, 6):
        r = radius * ring / 5
        parts.append(
            f"<circle cx='{center:.1f}' cy='{center:.1f}' r='{r:.1f}' "
            "fill='none' stroke='#e5e7eb' stroke-width='1'/>"
        )
    for category, x, y, _ in axes:
        parts.append(
            f"<line x1='{center:.1f}' y1='{center:.1f}' x2='{x:.1f}' y2='{y:.1f}' "
            "stroke='#e5e7eb' stroke-width='1'/>"
        )
        label_x = center + (x - center) * 1.16
        label_y = center + (y - center) * 1.16
        parts.append(
            f"<text x='{label_x:.1f}' y='{label_y:.1f}' font-size='11' "
            "fill='#78716c' text-anchor='middle' dominant-baseline='middle' "
            f"font-family='Inter, system-ui, sans-serif'>{_escape(category)}</text>"
        )
    for idx, row in enumerate(series):
        points = []
        for category, _, _, angle in axes:
            value = float(row.get("per_category", {}).get(category, 0)) / 10.0
            x = center + math.cos(angle) * radius * value
            y = center + math.sin(angle) * radius * value
            points.append(f"{x:.1f},{y:.1f}")
        color = COLORS[idx % len(COLORS)]
        parts.append(
            f"<polygon points='{' '.join(points)}' fill='{color}' "
            f"fill-opacity='0.14' stroke='{color}' stroke-width='1.75' "
            "stroke-linejoin='round'/>"
        )
    parts.append("</svg>")
    return "".join(parts)


def bar_svg(values: dict[str, float], *, width: int = 640) -> str:
    if not values:
        return _empty_svg("No category scores yet", width=width, height=120)
    items = sorted(values.items())
    row_h = 30
    pad_top = 16
    pad_bottom = 12
    label_col = 150
    value_col = 56
    height = pad_top + pad_bottom + len(items) * row_h
    track_x = label_col
    track_w = width - label_col - value_col
    parts = [
        f"<svg width='100%' height='{height}' viewBox='0 0 {width} {height}' "
        "role='img' aria-label='Per-category bar chart' class='ebx-bars' "
        "preserveAspectRatio='xMinYMid meet'>"
    ]
    for idx, (category, value) in enumerate(items):
        y = pad_top + idx * row_h
        bar_w = max(2.0, track_w * float(value) / 10.0)
        parts.append(
            f"<text x='0' y='{y + 14}' font-size='12' fill='#0c0a09' "
            f"font-family='Inter, system-ui, sans-serif'>{_escape(category)}</text>"
        )
        parts.append(
            f"<rect x='{track_x}' y='{y + 4}' width='{track_w}' height='14' "
            "fill='#f5f5f4' rx='7'/>"
        )
        parts.append(
            f"<rect x='{track_x}' y='{y + 4}' width='{bar_w:.1f}' height='14' "
            "fill='#3ba6f1' rx='7'/>"
        )
        parts.append(
            f"<text x='{width - value_col + 8}' y='{y + 14}' font-size='12' "
            f"fill='#0c0a09' font-family='Inter, system-ui, sans-serif'>"
            f"{value:.2f}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _empty_svg(message: str, *, width: int, height: int) -> str:
    return (
        f"<svg width='100%' height='{height}' viewBox='0 0 {width} {height}' "
        f"role='img' aria-label='{_escape(message)}'>"
        f"<text x='{width / 2:.1f}' y='{height / 2:.1f}' font-size='13' "
        "fill='#a8a29e' text-anchor='middle' dominant-baseline='middle' "
        f"font-family='Inter, system-ui, sans-serif'>{_escape(message)}</text>"
        "</svg>"
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

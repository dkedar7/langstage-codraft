"""Page geometry shared by the server (word budgets, export) and documented for the UI.

Sizes are CSS px at 96 dpi. The frontend has the same table in ``layout/units.ts``.
"""
from __future__ import annotations

MM = 96 / 25.4

PAGE_SIZES_MM: dict[str, tuple[float, float]] = {
    "letter": (215.9, 279.4),
    "a4": (210.0, 297.0),
    "legal": (215.9, 355.6),
    "a5": (148.0, 210.0),
    "slide": (338.67, 190.5),  # 16:9 (13.333in x 7.5in)
}


def page_size_mm(settings: dict) -> tuple[float, float]:
    """Unrotated (width, height) in mm; ``custom`` uses page_w_mm / page_h_mm."""
    if settings.get("page_size") == "custom":
        return float(settings.get("page_w_mm") or 215.9), float(settings.get("page_h_mm") or 279.4)
    return PAGE_SIZES_MM.get(settings.get("page_size", "letter"), PAGE_SIZES_MM["letter"])


def page_px(settings: dict) -> tuple[float, float]:
    w, h = page_size_mm(settings)
    if settings.get("orientation") == "landscape":
        w, h = h, w
    return w * MM, h * MM


def page_mm(settings: dict) -> tuple[float, float]:
    w, h = page_px(settings)
    return w / MM, h / MM


def content_box(settings: dict) -> tuple[float, float]:
    """(width, height) of the page's content area in px."""
    w, h = page_px(settings)
    m = settings.get("margins", {})
    return (
        w - (m.get("left", 20) + m.get("right", 20)) * MM,
        h - (m.get("top", 20) + m.get("bottom", 20)) * MM,
    )


def column_width(settings: dict) -> float:
    cw, _ = content_box(settings)
    if settings.get("mode") == "pageless":
        return min(cw, 720.0)
    n = int(settings.get("columns", 1))
    gap = settings.get("column_gap", 8) * MM
    return (cw - gap * (n - 1)) / n

"""Checks on a chart spec that a renderer can't fix but an author or agent can.

:func:`chart_warnings` reads a Vega-Lite spec and returns short, plain warnings: one axis
that mixes units, and a colour scale whose neighbours are hard to tell apart. A tool that
writes a chart can append them to its result so the agent sees them before the author does.
"""
from __future__ import annotations

import re
from typing import Any

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _units_in(text: str) -> str | None:
    """The unit a value label carries: "%", "$", "countries", "" for a bare number, or None
    when the text has no number."""
    found = list(_NUMBER.finditer(text))
    if not found:
        return None
    m = found[-1]
    before = text[: m.start()].strip()
    after = text[m.end():].strip().lower()
    if after.startswith("%"):
        return "%"
    if before.endswith(("$", "€", "£")):
        return before[-1]
    word = re.match(r"[a-zµ°/]+", after)
    return word.group(0) if word else ""


def _rgb(color: Any) -> tuple[int, int, int] | None:
    m = _HEX.match(str(color or "").strip())
    if not m:
        return None
    h = m.group(1)
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """A cheap perceptual distance between two colours ("redmean"), about 0 to 765."""
    rm = (a[0] + b[0]) / 2
    dr, dg, db = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return (((512 + rm) * dr * dr) / 256 + 4 * dg * dg + ((767 - rm) * db * db) / 256) ** 0.5


def _units(spec: dict) -> list[dict]:
    layers = spec.get("layer")
    return [u for u in layers if isinstance(u, dict)] if isinstance(layers, list) else [spec]


def chart_warnings(spec: Any) -> list[str]:
    """Problems in a Vega-Lite spec worth telling its author about. Empty when there are none
    or when the spec isn't one this can read."""
    if not isinstance(spec, dict):
        return []
    out: list[str] = []
    values = (spec.get("data") or {}).get("values") if isinstance(spec.get("data"), dict) else None
    rows = [r for r in values if isinstance(r, dict)] if isinstance(values, list) else []

    # One axis, several units: the labels the marks print disagree about what the numbers are.
    for unit in _units(spec):
        enc = {**(spec.get("encoding") or {}), **(unit.get("encoding") or {})}
        text = enc.get("text") if isinstance(enc.get("text"), dict) else None
        if not text or not text.get("field"):
            continue
        found = [u for u in (_units_in(str(r.get(text["field"], ""))) for r in rows) if u is not None]
        kinds = list(dict.fromkeys(found))
        if len(kinds) > 1:
            names = ", ".join(k or "plain numbers" for k in kinds)
            out.append(f"The chart puts different units on one axis ({names}): bars of different units can't be "
                       "compared by length. Use one unit per chart, or show the others as number tiles (a stat block).")
            break

    # Neighbouring colours that read as the same colour.
    for unit in _units(spec):
        enc = {**(spec.get("encoding") or {}), **(unit.get("encoding") or {})}
        color = enc.get("color") if isinstance(enc.get("color"), dict) else None
        rng = ((color or {}).get("scale") or {}).get("range") if color else None
        if not isinstance(rng, list):
            continue
        rgb = [_rgb(c) for c in rng]
        close = [(rng[i], rng[j]) for i in range(len(rgb)) for j in range(i + 1, len(rgb))
                 if rgb[i] and rgb[j] and _distance(rgb[i], rgb[j]) < 90]
        if close:
            pairs = "; ".join(f"{a} and {b}" for a, b in close[:3])
            out.append(f"Some of the chart's colours are hard to tell apart ({pairs}). Pick colours that differ "
                       "in hue as well as lightness, or leave the range out for the default palette.")
            break
    return out

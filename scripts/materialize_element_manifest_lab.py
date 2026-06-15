#!/usr/bin/env python3
"""Materialize an element-manifest driven rebuilt layer for layout/asset QA.

This is a diagnostic component scaffold, not final project code. It renders every
manifest element at measured coordinates, consumes declared element assets as `<img>`
nodes, and paints non-asset primitives with sampled reference colors so layout drift is
easy to separate from semantic text labeling work.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


LIGHT_TEXT_RESAMPLE_REGIONS = {
    "ai-assistant",
    "bottom-nav",
    "header",
    "insights-row",
    "itinerary",
    "packing-checklist",
    "recommendations",
}

DENSE_DARK_TEXT_REGIONS = {
    "ai-remediation",
    "audit-log",
    "deployment-queue",
    "incident-timeline",
    "regional-health",
    "sidebar",
}


def load_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        if fallback is not None:
            return fallback
        raise SystemExit(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def parse_hex(value: str | None, fallback: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    if not value:
        return fallback
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return fallback
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return fallback


def hex_color(rgb: tuple[int, int, int] | list[int]) -> str:
    r, g, b = [max(0, min(255, int(round(v)))) for v in rgb[:3]]
    return f"#{r:02x}{g:02x}{b:02x}"


def luminance(rgb: tuple[int, int, int] | list[int] | np.ndarray) -> float:
    r, g, b = [float(v) for v in rgb[:3]]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def bounds_area(bounds: dict[str, Any]) -> float:
    return max(0.0, float(bounds.get("width") or 0)) * max(0.0, float(bounds.get("height") or 0))


def bounds_intersection(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax, ay = float(a.get("x") or 0), float(a.get("y") or 0)
    bx, by = float(b.get("x") or 0), float(b.get("y") or 0)
    aw, ah = float(a.get("width") or 0), float(a.get("height") or 0)
    bw, bh = float(b.get("width") or 0), float(b.get("height") or 0)
    x0 = max(ax, bx)
    y0 = max(ay, by)
    x1 = min(ax + aw, bx + bw)
    y1 = min(ay + ah, by + bh)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def bounds_center(bounds: dict[str, Any]) -> tuple[float, float]:
    return (
        float(bounds.get("x") or 0) + float(bounds.get("width") or 0) / 2,
        float(bounds.get("y") or 0) + float(bounds.get("height") or 0) / 2,
    )


def bounds_contains(bounds: dict[str, Any], point: tuple[float, float]) -> bool:
    x, y = float(bounds.get("x") or 0), float(bounds.get("y") or 0)
    width, height = float(bounds.get("width") or 0), float(bounds.get("height") or 0)
    return x <= point[0] <= x + width and y <= point[1] <= y + height


def css_ident(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    value = value.strip("-") or "item"
    if value[0].isdigit():
        return f"i-{value}"
    return value


def css_string(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def clamp_bounds(bounds: dict[str, Any], image: Image.Image) -> dict[str, int] | None:
    try:
        x = int(round(float(bounds["x"])))
        y = int(round(float(bounds["y"])))
        width = int(round(float(bounds["width"])))
        height = int(round(float(bounds["height"])))
    except (KeyError, TypeError, ValueError):
        return None
    x = max(0, min(x, image.width))
    y = max(0, min(y, image.height))
    width = max(0, min(width, image.width - x))
    height = max(0, min(height, image.height - y))
    if width <= 0 or height <= 0:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def estimate_border_bg(crop: Image.Image) -> tuple[int, int, int]:
    arr = np.asarray(crop.convert("RGB")).astype(np.int16)
    if arr.size == 0:
        return (255, 255, 255)
    top = arr[: min(3, arr.shape[0]), :, :]
    bottom = arr[max(0, arr.shape[0] - 3) :, :, :]
    left = arr[:, : min(3, arr.shape[1]), :]
    right = arr[:, max(0, arr.shape[1] - 3) :, :]
    border = np.concatenate([top.reshape(-1, 3), bottom.reshape(-1, 3), left.reshape(-1, 3), right.reshape(-1, 3)], axis=0)
    return tuple(int(v) for v in np.median(border, axis=0))


def region_bg(region: dict[str, Any], reference: Image.Image, page_bg: tuple[int, int, int]) -> tuple[int, int, int]:
    raw = region.get("background_rgb")
    if isinstance(raw, list) and len(raw) >= 3:
        return (int(raw[0]), int(raw[1]), int(raw[2]))
    bounds = clamp_bounds(region.get("bounds") or {}, reference)
    if not bounds:
        return page_bg
    crop = reference.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]))
    return estimate_border_bg(crop)


def element_color(
    element: dict[str, Any],
    reference: Image.Image,
    bg: tuple[int, int, int],
    threshold: int,
) -> tuple[int, int, int]:
    bounds = clamp_bounds(element.get("bounds") or {}, reference)
    if not bounds:
        return bg
    crop = reference.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]))
    arr = np.asarray(crop.convert("RGB")).astype(np.int16)
    if arr.size == 0:
        return bg
    bg_arr = np.asarray(bg, dtype=np.int16)
    delta = np.max(np.abs(arr - bg_arr), axis=2)
    mask = delta > threshold
    pixels = arr[mask] if np.count_nonzero(mask) else arr.reshape(-1, 3)
    if len(pixels) == 0:
        return bg
    return tuple(int(v) for v in np.median(pixels, axis=0))


def text_color(
    element: dict[str, Any],
    reference: Image.Image,
    bg: tuple[int, int, int],
    threshold: int,
) -> tuple[int, int, int]:
    bounds = clamp_bounds(element.get("bounds") or {}, reference)
    bg_lum = luminance(bg)
    fallback = (226, 232, 240) if bg_lum < 96 else (17, 24, 39)
    if not bounds:
        return fallback
    crop = reference.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]))
    arr = np.asarray(crop.convert("RGB")).astype(np.int16)
    if arr.size == 0:
        return fallback
    bg_arr = np.asarray(bg, dtype=np.int16)
    delta = np.max(np.abs(arr - bg_arr), axis=2)
    mask = delta > max(8, threshold - 2)
    pixels = arr[mask] if np.count_nonzero(mask) else arr.reshape(-1, 3)
    if len(pixels) == 0:
        return fallback
    lum = np.asarray([luminance(pixel) for pixel in pixels], dtype=np.float32)
    if bg_lum < 96:
        cutoff = np.percentile(lum, 62)
        selected = pixels[lum >= cutoff]
        color = tuple(int(v) for v in np.median(selected if len(selected) else pixels, axis=0))
        if luminance(color) < bg_lum + 42 and max(color) - min(color) < 42:
            return fallback
        return color
    cutoff = np.percentile(lum, 28)
    selected = pixels[lum <= cutoff]
    color = tuple(int(v) for v in np.median(selected if len(selected) else pixels, axis=0))
    if luminance(color) > bg_lum - 32 and max(color) - min(color) < 32:
        return fallback
    return color


def foreground_fraction(
    element: dict[str, Any],
    reference: Image.Image,
    bg: tuple[int, int, int],
    threshold: int,
) -> float:
    bounds = clamp_bounds(element.get("bounds") or {}, reference)
    if not bounds:
        return 0.0
    crop = reference.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]))
    arr = np.asarray(crop.convert("RGB")).astype(np.int16)
    if arr.size == 0:
        return 0.0
    bg_arr = np.asarray(bg, dtype=np.int16)
    delta = np.max(np.abs(arr - bg_arr), axis=2)
    return float(np.count_nonzero(delta > threshold)) / float(delta.size)


def crop_colorfulness(element: dict[str, Any], reference: Image.Image) -> float:
    bounds = clamp_bounds(element.get("bounds") or {}, reference)
    if not bounds:
        return 0.0
    crop = reference.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]))
    arr = np.asarray(crop.convert("RGB")).astype(np.float32)
    if arr.size == 0:
        return 0.0
    rg = np.abs(arr[:, :, 0] - arr[:, :, 1])
    yb = np.abs(0.5 * (arr[:, :, 0] + arr[:, :, 1]) - arr[:, :, 2])
    return float((float(rg.std()) ** 2 + float(yb.std()) ** 2) ** 0.5)


def median_crop_color(element: dict[str, Any], reference: Image.Image, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    bounds = clamp_bounds(element.get("bounds") or {}, reference)
    if not bounds:
        return fallback
    crop = reference.crop((bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]))
    arr = np.asarray(crop.convert("RGB")).reshape(-1, 3)
    if len(arr) == 0:
        return fallback
    return tuple(int(v) for v in np.median(arr, axis=0))


def mean_bounds_color(bounds: dict[str, Any], reference: Image.Image, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    clamped = clamp_bounds(bounds, reference)
    if not clamped:
        return fallback
    crop = reference.crop((clamped["x"], clamped["y"], clamped["x"] + clamped["width"], clamped["y"] + clamped["height"]))
    arr = np.asarray(crop.convert("RGB")).reshape(-1, 3)
    if len(arr) == 0:
        return fallback
    return tuple(int(round(v)) for v in np.mean(arr, axis=0))


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(int(a[index]) - int(b[index])) for index in range(3))


def blend_rgb(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(int(round(a[index] * (1 - amount) + b[index] * amount)) for index in range(3))


def region_surface_style(region_name: str, bg: tuple[int, int, int], page_bg: tuple[int, int, int]) -> dict[str, str]:
    """Infer the region container surface from semantic region names.

    The element scaffold is a diagnostic component renderer, but container chrome is
    still part of component layout. Region-level surface styling keeps lists, tabs,
    cards, and dark dashboard panels from looking like loose absolute fragments.
    """
    name = region_name.lower()
    bg_hex = hex_color(bg)
    style: dict[str, str] = {
        "background": bg_hex,
        "border": "0",
        "border-radius": "0",
        "box-shadow": "none",
    }
    dark = luminance(bg) < 96
    if dark:
        if name == "sidebar":
            style.update(
                {
                    "background": f"linear-gradient(180deg, {hex_color(blend_rgb(bg, (255, 255, 255), 0.025))}, {bg_hex})",
                    "box-shadow": "inset -1px 0 0 rgba(148, 163, 184, 0.15)",
                }
            )
            return style
        if name == "topbar":
            style["background"] = "transparent"
            return style
        style.update(
            {
                "background": (
                    "linear-gradient(135deg, rgba(255, 255, 255, 0.030), rgba(0, 0, 0, 0.035)), "
                    f"{bg_hex}"
                ),
                "border": "0",
                "border-radius": "6px",
                "box-shadow": "inset 0 0 0 1px rgba(148, 163, 184, 0.17), inset 0 1px 0 rgba(255, 255, 255, 0.025)",
            }
        )
        return style

    if name in {"tabs", "itinerary", "packing-checklist", "ai-assistant"}:
        style.update(
            {
                "background": bg_hex,
                "border": "0",
                "border-radius": "14px" if name != "tabs" else "12px",
                "box-shadow": "inset 0 0 0 1px rgba(15, 23, 42, 0.08), 0 10px 24px rgba(15, 23, 42, 0.035)",
            }
        )
    elif name == "bottom-nav":
        style.update(
            {
                "background": hex_color(blend_rgb(bg, page_bg, 0.45)),
                "box-shadow": "0 -10px 22px rgba(15, 23, 42, 0.06)",
            }
        )
    elif name in {"header", "intro-actions", "recommendations", "insights-row"}:
        style["background"] = "transparent"
    return style


def background_patches_from_config(
    config: dict[str, Any],
    reference: Image.Image,
    page_bg: tuple[int, int, int],
    min_delta: int = 2,
    allow_slice_assets: bool = True,
) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    for item in config.get("slices") or []:
        if not isinstance(item, dict) or not str(item.get("name") or "").startswith("gap-"):
            continue
        bounds = {key: int(item.get(key) or 0) for key in ("x", "y", "width", "height")}
        if bounds["width"] <= 0 or bounds["height"] <= 0:
            continue
        clamped = clamp_bounds(bounds, reference)
        if not clamped:
            continue
        crop = reference.crop((clamped["x"], clamped["y"], clamped["x"] + clamped["width"], clamped["y"] + clamped["height"]))
        arr = np.asarray(crop.convert("RGB")).reshape(-1, 3).astype(np.float32)
        if len(arr) == 0:
            continue
        mean = np.mean(arr, axis=0)
        color = tuple(int(round(v)) for v in mean)
        max_delta = np.max(np.abs(arr - mean), axis=1)
        p90_delta = float(np.percentile(max_delta, 90))
        aspect = bounds["width"] / max(1, bounds["height"])
        small_gap_asset = (
            allow_slice_assets
            and
            bool(item.get("src"))
            and (bounds["width"] <= 42 or bounds["height"] <= 24)
            and bounds["width"] * bounds["height"] <= 25000
        )
        use_slice_asset = (
            small_gap_asset
            or (
                allow_slice_assets
                and
                bool(item.get("src"))
                and p90_delta > 6
                and (bounds["width"] <= 42 or bounds["height"] <= 24)
                and bounds["width"] * bounds["height"] <= 20000
            )
        )
        if color_distance(color, page_bg) <= min_delta and not use_slice_asset:
            continue
        if aspect >= 20 and p90_delta > 12 and not use_slice_asset:
            continue
        patch_id = css_ident(str(item.get("name") or f"gap-{len(patches) + 1:02d}"))
        patch = {"id": patch_id, "bounds": bounds, "color": color}
        if use_slice_asset:
            patch["src"] = str(item.get("src"))
        patches.append(patch)
    return patches


def asset_src(path_value: str) -> str:
    if path_value.startswith("./") or path_value.startswith("/") or "://" in path_value:
        return path_value
    return f"./{path_value}"


def is_chart_host(element: dict[str, Any]) -> bool:
    return str(element.get("type") or "") == "chart-host"


def is_text_element(element: dict[str, Any]) -> bool:
    return str(element.get("type") or "") == "text" and bool(str(element.get("content") or "").strip())


def normalize_text_content(region_name: str, value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"\s*(?:©|@\)|@)\s*$", "", text).rstrip()
    text = text.replace("AllSystems", "All Systems")
    region_key = region_name.lower()
    if text == "Lumatrip":
        return "LumaTrip"
    if "intro-actions" in region_key and text == "Good morning,":
        return "Good morning, Sophia"
    if region_key == "insights-row":
        if text == "days":
            return "12 days"
    if region_key == "kpi-incidents" and text == "2vs last 24h":
        return "2 vs last 24h"
    if region_key == "kpi-latency" and text == "8ms":
        return "8 ms"
    if region_key == "deployment-queue":
        if text == "In Queve":
            return "In Queue"
    if "assistant" in region_key:
        text = text.replace("Luma Al Assistant", "Luma AI Assistant")
        text = text.replace("Luma Al", "Luma AI")
    if text.startswith("AlRemediation"):
        text = "AI Remediation" + text[len("AlRemediation") :]
    if text == "Al Remediation":
        text = "AI Remediation"
    if "remediation" in region_key:
        text = text.replace("Al ", "AI ")
    if "itinerary" in region_key and text == "Day May 26, Monday":
        text = "Day 3 · May 26, Monday"
    if region_key == "sidebar" and text == "Corp":
        text = "Acme Corp"
    return text


def display_text(region: dict[str, Any], element: dict[str, Any]) -> str:
    text = str(element.get("content") or "")
    region_name = str(region.get("name") or "").lower()
    if ("checklist" in region_name or "packing" in region_name) and text.strip().lower() == "all items":
        return "View all items"
    return normalize_text_content(region_name, text)


def render_text_markup(region: dict[str, Any], element: dict[str, Any]) -> str:
    text = display_text(region, element)
    region_name = str(region.get("name") or "").lower()
    escaped = html.escape(text)
    wave = "\U0001f44b"
    if wave in text:
        return escaped.replace(wave, f'<span class="pt-wave">{wave}</span>')
    if region_name == "intro-actions" and text == "awaits in Rome":
        return escaped.replace("Rome", '<span class="pt-accent-teal">Rome</span>')
    return escaped


def text_visual_affix(region: dict[str, Any], element: dict[str, Any]) -> dict[str, Any]:
    region_name = str(region.get("name") or "").lower()
    text = display_text(region, element)
    if region_name == "kpi-uptime" and text == "0.03% vs prev 30d":
        return {"before": {"content": "\\2191", "color": "#2eea83", "left": "-13px", "top": "0", "font_size": 1.0}}
    if region_name == "kpi-incidents" and text == "2 vs last 24h":
        return {"before": {"content": "\\2193", "color": "#2eea83", "left": "-12px", "top": "0", "font_size": 1.0}}
    if region_name == "kpi-latency" and text == "8 ms":
        return {"before": {"content": "\\2191", "color": "#ff5a63", "left": "-13px", "top": "0", "font_size": 1.0}}
    if region_name == "kpi-spend" and text == "6.7% vs last month":
        return {"before": {"content": "\\2193", "color": "#2eea83", "left": "-13px", "top": "0", "font_size": 1.0}}
    return {}


def semantic_fallback_text(region: dict[str, Any], element: dict[str, Any]) -> str:
    if is_text_element(element) or element.get("asset_path"):
        return ""
    region_name = str(region.get("name") or "").lower()
    bounds = element.get("bounds") or {}
    region_bounds = region.get("bounds") or {}
    try:
        rel_x = int(bounds.get("x") or 0) - int(region_bounds.get("x") or 0)
        rel_y = int(bounds.get("y") or 0) - int(region_bounds.get("y") or 0)
        width = int(bounds.get("width") or 0)
        height = int(bounds.get("height") or 0)
    except (TypeError, ValueError):
        return ""
    kind = str(element.get("kind_hint") or "")
    if (
        region_name == "kpi-uptime"
        and kind == "control-or-card-fragment"
        and 46 <= rel_x <= 60
        and 16 <= rel_y <= 24
        and 58 <= width <= 92
        and 14 <= height <= 24
    ):
        return "Uptime"
    return ""


def text_weight(element: dict[str, Any], bg: tuple[int, int, int] | None = None, region_name: str = "") -> int:
    bounds = element.get("bounds") or {}
    height = int(bounds.get("height") or 0)
    text = str(element.get("content") or "")
    region_key = region_name.lower()
    if region_key == "tabs":
        return 600 if normalize_text_content(region_key, text) == "Itinerary" else 400
    if region_key == "header":
        return 700 if normalize_text_content(region_key, text) == "LumaTrip" else 500
    if region_key == "itinerary":
        normalized = normalize_text_content(region_key, text)
        title_prefixes = (
            "Day ",
            "Train ",
            "Uffizi ",
            "Lunch ",
            "Ponte ",
            "Check-in ",
        )
        if normalized.startswith(title_prefixes):
            return 600
        return 500
    if region_key == "bottom-nav":
        return 600 if normalize_text_content(region_key, text) == "Home" else 500
    if region_key == "ai-assistant":
        normalized = normalize_text_content(region_key, text)
        if normalized == "Luma AI Assistant":
            return 600
        if normalized in {
            "Ask me anything about your trip",
            "Best day trips from Rome?",
            "Is this restaurant good for kids?",
            "Ask anything...",
        }:
            return 400
    if height >= 22 or any(char.isdigit() for char in text) and height >= 18:
        return 700
    if str(element.get("source") or "") == "ocr":
        if bg is not None and luminance(bg) < 96:
            return 600 if height >= 17 else 500
        return 600 if height >= 14 else 500
    return 500


def font_size_for(element: dict[str, Any], region: dict[str, Any] | None = None) -> int:
    bounds = element.get("bounds") or {}
    width = max(1.0, float(bounds.get("width") or 1))
    height = max(1.0, float(bounds.get("height") or 1))
    text = str(element.get("content") or "")
    compact_len = max(1, len(re.sub(r"\s+", "", text)))
    if str(element.get("source") or "") == "semantic-fallback":
        height_size = height * 0.62
        width_size = width / max(1.0, compact_len * 0.58)
        return max(6, int(round(min(height_size, width_size))))
    source = str(element.get("source") or "")
    region_name = str((region or {}).get("name") or "").lower()
    rel_x = float(bounds.get("x") or 0) - float(((region or {}).get("bounds") or {}).get("x") or 0)

    # OCR boxes describe the painted glyph bounds, while CSS font-size describes
    # the em box. Small UI labels were previously rendered too small because the
    # glyph height was treated as the font size. Use a conservative glyph->em
    # conversion, then tighten only the high-risk hero/stat cases by region.
    dense_dark_text = region_name in DENSE_DARK_TEXT_REGIONS
    if source == "ocr":
        if height <= 10:
            scale = 1.20
        elif height <= 14:
            scale = 1.16
        elif height <= 18:
            scale = 1.10
        elif height <= 24:
            scale = 1.08
        else:
            scale = 1.02
        if dense_dark_text and height <= 13:
            scale = 1.00
        if region_name == "intro-actions" and rel_x < 320 and height >= 20:
            scale = 1.34
        elif region_name == "tabs":
            scale = max(scale, 1.08)
        elif region_name in {"itinerary", "recommendations", "packing-checklist", "ai-assistant", "insights-row"}:
            if height <= 14:
                scale = max(scale, 1.18)
            elif height >= 20:
                scale = max(scale, 1.16)
        elif region_name.startswith("kpi-") and any(char.isdigit() for char in text):
            scale = max(scale, 1.16)
        height_size = height * scale
    else:
        height_size = height * 0.86
    width_factor = 0.62 if dense_dark_text and height <= 13 else 0.52
    width_size = width / max(1.0, compact_len * width_factor)
    if source == "ocr":
        size = min(height_size, width_size)
    else:
        size = height_size
    return max(6, int(round(size)))


def should_resample_light_text_color(region_name: str, bg: tuple[int, int, int], element: dict[str, Any]) -> bool:
    if region_name not in LIGHT_TEXT_RESAMPLE_REGIONS:
        return False
    if luminance(bg) < 180:
        return False
    if str(element.get("source") or "") not in {"ocr", "semantic-fallback"}:
        return False
    text = normalize_text_content(region_name, str(element.get("content") or ""))
    if not text:
        return False
    if region_name == "ai-assistant" and text == "BETA":
        return False
    return True


def needs_dark_text_baseline_shift(region_name: str, bg: tuple[int, int, int], element: dict[str, Any]) -> bool:
    if luminance(bg) >= 96 or str(element.get("source") or "") != "ocr":
        return False
    if region_name.startswith("kpi-"):
        return True
    return region_name in {
        "ai-remediation",
        "audit-log",
        "deployment-queue",
        "incident-timeline",
        "regional-health",
        "sidebar",
    }


def is_surface_placeholder(element: dict[str, Any]) -> bool:
    if element.get("asset_path") or is_text_element(element):
        return False
    kind = str(element.get("kind_hint") or "")
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    area = width * height
    if kind == "container-or-chart":
        return True
    if kind == "control-or-card-fragment" and area >= 500 and width >= 24 and height >= 10:
        return True
    if kind == "shape-or-sparkline" and area >= 1800 and width >= 80 and height >= 14:
        return True
    return False


def is_light_card_shell(region: dict[str, Any], element: dict[str, Any]) -> bool:
    if element.get("asset_path") or is_text_element(element):
        return False
    region_name = str(region.get("name") or "").lower()
    if region_name != "recommendations":
        return False
    kind = str(element.get("kind_hint") or "")
    if kind not in {"shape-or-sparkline", "control-or-card-fragment"}:
        return False
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    return width >= 130 and height >= 150


def is_nested_container_placeholder(region: dict[str, Any], element: dict[str, Any]) -> bool:
    if element.get("asset_path") or is_text_element(element):
        return False
    if str(element.get("kind_hint") or "") != "container-or-chart":
        return False
    region_bounds = region.get("bounds") or {}
    bounds = element.get("bounds") or {}
    region_height = float(region_bounds.get("height") or 0)
    region_area = bounds_area(region_bounds)
    if region_height <= 0 or region_area <= 0:
        return False
    top_offset = float(bounds.get("y") or 0) - float(region_bounds.get("y") or 0)
    height = float(bounds.get("height") or 0)
    if top_offset < region_height * 0.25 or height < region_height * 0.38:
        return False
    child_count = 0
    for other in region.get("elements") or []:
        if not isinstance(other, dict) or other is element:
            continue
        if not (is_text_element(other) or other.get("asset_path")):
            continue
        other_bounds = other.get("bounds") or {}
        other_area = bounds_area(other_bounds)
        if other_area <= 0:
            continue
        if bounds_intersection(bounds, other_bounds) / other_area >= 0.45 or bounds_contains(bounds, bounds_center(other_bounds)):
            child_count += 1
    return child_count >= 3


def is_low_signal_light_fragment(
    element: dict[str, Any],
    reference: Image.Image,
    bg: tuple[int, int, int],
    threshold: int,
) -> bool:
    if element.get("asset_path") or is_text_element(element):
        return False
    if sum(bg) / 3.0 < 180:
        return False
    kind = str(element.get("kind_hint") or "")
    if kind not in {"icon-or-badge", "primitive"}:
        return False
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    area = width * height
    if area <= 0 or area > 1700 or width < 3 or height < 3:
        return False
    return crop_colorfulness(element, reference) < 5.5 and foreground_fraction(element, reference, bg, threshold) < 0.62


def is_text_like_placeholder(
    element: dict[str, Any],
    reference: Image.Image,
    bg: tuple[int, int, int],
    threshold: int,
) -> bool:
    if element.get("asset_path") or is_text_element(element):
        return False
    kind = str(element.get("kind_hint") or "")
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    if width <= 0 or height <= 0 or height > 30:
        return False
    if kind == "text-line":
        return True
    if kind != "control-or-card-fragment":
        return False
    aspect = width / max(1.0, height)
    if width < 24 or aspect < 2.4:
        return False
    return foreground_fraction(element, reference, bg, threshold) <= 0.48


def is_timeline_rail_placeholder(region: dict[str, Any], element: dict[str, Any]) -> bool:
    if element.get("asset_path") or is_text_element(element):
        return False
    region_name = str(region.get("name") or "").lower()
    if "timeline" not in region_name and "itinerary" not in region_name:
        return False
    if str(element.get("kind_hint") or "") != "primitive":
        return False
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    return 6 <= width <= 24 and height >= 90 and width / max(1.0, height) <= 0.16


def text_control_shell(region: dict[str, Any], element: dict[str, Any]) -> dict[str, Any] | None:
    if not is_text_element(element) or str(element.get("source") or "") != "ocr":
        return None
    region_name = str(region.get("name") or "").lower()
    text = str(element.get("content") or "").strip()
    lowered = text.lower()
    bounds = element.get("bounds") or {}
    region_bounds = region.get("bounds") or {}
    try:
        x = int(bounds.get("x") or 0)
        y = int(bounds.get("y") or 0)
        width = int(bounds.get("width") or 0)
        height = int(bounds.get("height") or 0)
        region_x = int(region_bounds.get("x") or 0)
        region_y = int(region_bounds.get("y") or 0)
        region_width = int(region_bounds.get("width") or 0)
        region_height = int(region_bounds.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or region_width <= 0 or region_height <= 0:
        return None

    if "assistant" in region_name and lowered.startswith("ask anything"):
        pad_left, pad_right, pad_top, pad_bottom = 22, 254, 16, 16
    elif "assistant" in region_name and text.endswith("?") and width >= 70:
        pad_left, pad_right, pad_top, pad_bottom = 22, 24, 12, 10
    elif ("checklist" in region_name or "packing" in region_name) and "item" in lowered:
        rel_y = y - region_y
        if rel_y < region_height * 0.62:
            return None
        pad_left, pad_right, pad_top, pad_bottom = 52, 166, 15, 17
    else:
        return None

    shell_x = max(region_x, x - pad_left)
    shell_y = max(region_y, y - pad_top)
    shell_right = min(region_x + region_width, x + width + pad_right)
    shell_bottom = min(region_y + region_height, y + height + pad_bottom)
    if shell_right <= shell_x or shell_bottom <= shell_y:
        return None
    return {
        "x": shell_x,
        "y": shell_y,
        "width": shell_right - shell_x,
        "height": shell_bottom - shell_y,
    }


def synthetic_text_control_shells(region: dict[str, Any]) -> list[dict[str, Any]]:
    shells: list[dict[str, Any]] = []
    for element in region.get("elements") or []:
        if not isinstance(element, dict) or not element.get("id"):
            continue
        bounds = text_control_shell(region, element)
        if not bounds:
            continue
        shells.append(
            make_shell(
                region,
                f"text-control-{css_ident(str(element['id']))}",
                bounds,
                max(12, min(18, int(bounds.get("height") or 0) // 2)),
                "rgba(255, 255, 255, 0.92)",
                "rgba(132, 148, 164, 0.22)",
                "none",
                "synthetic-text-control-shell",
            )
        )
    return shells


def make_shell(
    region: dict[str, Any],
    shell_id: str,
    bounds: dict[str, int],
    radius: int,
    fill: str,
    border: str,
    shadow: str,
    kind: str = "synthetic-control-shell",
) -> dict[str, Any]:
    return {
        "id": f"{region.get('name')}-shell-{shell_id}",
        "bounds": bounds,
        "radius": radius,
        "kind": kind,
        "fill": fill,
        "border": border,
        "shadow": shadow,
    }


def grouped_text_rows(
    region: dict[str, Any],
    min_y: int,
    min_count: int,
    min_span: int,
    cluster_gap: int = 7,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text_elements = [
        element
        for element in region.get("elements") or []
        if isinstance(element, dict) and is_text_element(element) and element.get("source") == "ocr"
    ]
    for element in sorted(text_elements, key=lambda item: bounds_center(item.get("bounds") or {})[1]):
        bounds = element.get("bounds") or {}
        center_y = bounds_center(bounds)[1]
        if center_y < min_y:
            continue
        target = None
        for row in rows:
            if abs(row["center_y"] - center_y) <= cluster_gap:
                target = row
                break
        if target is None:
            target = {"center_y": center_y, "bounds": [], "texts": []}
            rows.append(target)
        target["bounds"].append(bounds)
        target["texts"].append(str(element.get("content") or ""))
        centers = [bounds_center(item)[1] for item in target["bounds"]]
        target["center_y"] = sum(centers) / max(1, len(centers))

    filtered: list[dict[str, Any]] = []
    for row in rows:
        bounds_list = row["bounds"]
        if len(bounds_list) < min_count:
            continue
        min_x = min(int(bounds.get("x") or 0) for bounds in bounds_list)
        max_x = max(int(bounds.get("x") or 0) + int(bounds.get("width") or 0) for bounds in bounds_list)
        if max_x - min_x < min_span:
            continue
        filtered.append({**row, "min_x": min_x, "max_x": max_x})
    return sorted(filtered, key=lambda row: row["center_y"])


def collection_region_kind(region_name: str) -> str | None:
    if region_name.startswith("kpi-"):
        return None
    if region_name in {"bottom-nav", "sidebar"}:
        return "navigation"
    if region_name in {"recommendations"}:
        return "card-grid"
    if region_name in {"itinerary", "packing-checklist", "incident-timeline", "deployment-queue", "audit-log", "regional-health"}:
        return "list"
    if any(token in region_name for token in ("queue", "timeline", "table", "checklist")):
        return "list"
    return None


def text_rows_any(
    region: dict[str, Any],
    min_y: int,
    cluster_gap: int,
    *,
    min_chars: int = 1,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text_elements = [
        element
        for element in region.get("elements") or []
        if isinstance(element, dict) and is_text_element(element) and element.get("source") == "ocr"
    ]
    for element in sorted(text_elements, key=lambda item: bounds_center(item.get("bounds") or {})[1]):
        bounds = element.get("bounds") or {}
        text = str(element.get("content") or "").strip()
        if len(re.sub(r"\s+", "", text)) < min_chars:
            continue
        center_y = bounds_center(bounds)[1]
        if center_y < min_y:
            continue
        target = None
        for row in rows:
            if abs(row["center_y"] - center_y) <= cluster_gap:
                target = row
                break
        if target is None:
            target = {"center_y": center_y, "bounds": [], "texts": []}
            rows.append(target)
        target["bounds"].append(bounds)
        target["texts"].append(text)
        centers = [bounds_center(item)[1] for item in target["bounds"]]
        target["center_y"] = sum(centers) / max(1, len(centers))
    return sorted(rows, key=lambda row: row["center_y"])


def row_bounds_from_centers(
    rows: list[dict[str, Any]],
    region_x: int,
    region_y: int,
    region_width: int,
    region_height: int,
    *,
    left_pad: int,
    right_pad: int,
    min_height: int,
    top_guard: int = 0,
    bottom_guard: int = 0,
) -> list[dict[str, int]]:
    if not rows:
        return []
    centers = [float(row["center_y"]) for row in rows]
    top = region_y + top_guard
    bottom = region_y + region_height - bottom_guard
    items: list[dict[str, int]] = []
    for index, row in enumerate(rows):
        center_y = centers[index]
        prev_center = centers[index - 1] if index > 0 else None
        next_center = centers[index + 1] if index + 1 < len(centers) else None
        if prev_center is None:
            y0 = max(top, center_y - (next_center - center_y) / 2 if next_center is not None else center_y - min_height / 2)
        else:
            y0 = (prev_center + center_y) / 2
        if next_center is None:
            y1 = min(bottom, center_y + (center_y - prev_center) / 2 if prev_center is not None else center_y + min_height / 2)
        else:
            y1 = (center_y + next_center) / 2
        y = int(round(y0))
        height = max(min_height, int(round(y1 - y0)))
        if y + height > bottom:
            height = max(min_height, bottom - y)
        if height <= 0:
            continue
        items.append(
            {
                "x": region_x + left_pad,
                "y": y,
                "width": max(1, region_width - left_pad - right_pad),
                "height": height,
            }
        )
    return items


def card_grid_items(region: dict[str, Any], region_x: int, region_y: int, region_width: int, region_height: int) -> list[dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    for element in region.get("elements") or []:
        if not isinstance(element, dict):
            continue
        bounds = element.get("bounds") or {}
        width = float(bounds.get("width") or 0)
        height = float(bounds.get("height") or 0)
        kind = str(element.get("kind_hint") or "")
        asset_type = str(element.get("asset_type") or "")
        if width < 90 or height < 90:
            continue
        if kind == "shape-or-sparkline" or asset_type == "image":
            candidates.append(bounds)
    if not candidates and str(region.get("name") or "").lower() == "recommendations":
        gap = 12
        top = region_y + min(52, max(42, int(round(region_height * 0.18))))
        card_height = max(1, region_y + region_height - top - 2)
        card_width = max(1, int(round((region_width - gap * 3) / 4)))
        fallback_items: list[dict[str, int]] = []
        for index in range(4):
            x = region_x + index * (card_width + gap)
            if index == 3:
                width = max(1, region_x + region_width - x)
            else:
                width = card_width
            fallback_items.append({"x": x, "y": top, "width": width, "height": card_height})
        return fallback_items
    if not candidates:
        return []
    candidates.sort(key=lambda item: (int(item.get("x") or 0), int(item.get("y") or 0)))
    items: list[dict[str, int]] = []
    seen: set[tuple[int, int]] = set()
    for bounds in candidates:
        x = int(bounds.get("x") or 0)
        y = int(bounds.get("y") or 0)
        width = int(bounds.get("width") or 0)
        key = (round(x / 12), round(width / 12))
        if key in seen:
            continue
        seen.add(key)
        card_bottom = min(region_y + region_height, y + max(int(bounds.get("height") or 0), 190))
        items.append(
            {
                "x": x,
                "y": y,
                "width": width,
                "height": max(1, card_bottom - y),
            }
        )
    if items:
        return sorted(items[:6], key=lambda item: item["x"])
    return []


def bottom_nav_items(region_x: int, region_y: int, region_width: int, region_height: int) -> list[dict[str, int]]:
    count = 5
    item_width = max(1, region_width // count)
    items: list[dict[str, int]] = []
    for index in range(count):
        x = region_x + index * item_width
        width = item_width if index < count - 1 else region_x + region_width - x
        items.append({"x": x, "y": region_y, "width": width, "height": region_height})
    return items


def asset_anchor_rows(
    region: dict[str, Any],
    *,
    min_y: int,
    max_y: int,
    min_rel_x: int,
    max_rel_x: int,
    min_size: int,
    cluster_gap: int = 8,
) -> list[dict[str, Any]]:
    """Infer list rows from repeated visual anchors such as leading item icons."""
    region_bounds = region.get("bounds") or {}
    try:
        region_x = int(region_bounds.get("x") or 0)
    except (TypeError, ValueError):
        return []
    rows: list[dict[str, Any]] = []
    for element in region.get("elements") or []:
        if not isinstance(element, dict) or not element.get("asset_path"):
            continue
        asset_type = str(element.get("asset_type") or "")
        if asset_type not in {"avatar", "control", "icon", "image"}:
            continue
        bounds = element.get("bounds") or {}
        try:
            x = int(bounds.get("x") or 0)
            y = int(bounds.get("y") or 0)
            width = int(bounds.get("width") or 0)
            height = int(bounds.get("height") or 0)
        except (TypeError, ValueError):
            continue
        rel_x = x - region_x
        if not (min_rel_x <= rel_x <= max_rel_x):
            continue
        if max(width, height) < min_size:
            continue
        center_y = y + height / 2
        if center_y < min_y or center_y > max_y:
            continue
        target = None
        for row in rows:
            if abs(row["center_y"] - center_y) <= cluster_gap:
                target = row
                break
        if target is None:
            target = {"center_y": center_y, "bounds": [], "texts": []}
            rows.append(target)
        target["bounds"].append(bounds)
        centers = [bounds_center(item)[1] for item in target["bounds"]]
        target["center_y"] = sum(centers) / max(1, len(centers))
    return sorted(rows, key=lambda row: row["center_y"])


def collection_items_for_region(region: dict[str, Any]) -> list[dict[str, int]]:
    region_name = str(region.get("name") or "").lower()
    bounds = region.get("bounds") or {}
    try:
        region_x = int(bounds.get("x") or 0)
        region_y = int(bounds.get("y") or 0)
        region_width = int(bounds.get("width") or 0)
        region_height = int(bounds.get("height") or 0)
    except (TypeError, ValueError):
        return []
    if region_width <= 0 or region_height <= 0:
        return []

    if region_name == "bottom-nav":
        return bottom_nav_items(region_x, region_y, region_width, region_height)
    if region_name == "recommendations":
        return card_grid_items(region, region_x, region_y, region_width, region_height)
    if region_name == "sidebar":
        rows = text_rows_any(region, region_y + 120, 10, min_chars=3)
        return row_bounds_from_centers(
            rows,
            region_x,
            region_y,
            region_width,
            region_height,
            left_pad=8,
            right_pad=8,
            min_height=28,
            top_guard=120,
            bottom_guard=44,
        )
    if region_name == "itinerary":
        rows = grouped_text_rows(region, region_y + 40, min_count=3, min_span=250, cluster_gap=10)
        return row_bounds_from_centers(rows, region_x, region_y, region_width, region_height, left_pad=18, right_pad=18, min_height=48, top_guard=38)
    if region_name == "packing-checklist":
        rows = text_rows_any(region, region_y + 44, 9, min_chars=3)
        rows = [row for row in rows if not any("item" in text.lower() for text in row.get("texts") or [])]
        return row_bounds_from_centers(rows, region_x, region_y, region_width, region_height, left_pad=16, right_pad=16, min_height=22, top_guard=42, bottom_guard=60)
    if region_name == "incident-timeline":
        rows = grouped_text_rows(region, region_y + 40, min_count=3, min_span=160, cluster_gap=9)
        return row_bounds_from_centers(rows, region_x, region_y, region_width, region_height, left_pad=8, right_pad=8, min_height=36, top_guard=38, bottom_guard=18)
    if region_name == "deployment-queue":
        anchor_rows = asset_anchor_rows(
            region,
            min_y=region_y + 44,
            max_y=region_y + region_height - 18,
            min_rel_x=0,
            max_rel_x=64,
            min_size=24,
        )
        if len(anchor_rows) >= 3:
            return row_bounds_from_centers(
                anchor_rows,
                region_x,
                region_y,
                region_width,
                region_height,
                left_pad=8,
                right_pad=8,
                min_height=38,
                top_guard=36,
                bottom_guard=12,
            )
        rows = grouped_text_rows(region, region_y + 36, min_count=3, min_span=220, cluster_gap=9)
        return row_bounds_from_centers(rows, region_x, region_y, region_width, region_height, left_pad=8, right_pad=8, min_height=38, top_guard=36, bottom_guard=12)
    if region_name in {"audit-log", "regional-health"}:
        rows = grouped_text_rows(region, region_y + 36, min_count=3, min_span=260, cluster_gap=7)
        return row_bounds_from_centers(rows, region_x, region_y, region_width, region_height, left_pad=8, right_pad=8, min_height=24, top_guard=34, bottom_guard=8)
    return []


def synthetic_collection_shells(region: dict[str, Any]) -> list[dict[str, Any]]:
    region_name = str(region.get("name") or "").lower()
    bounds = region.get("bounds") or {}
    try:
        region_x = int(bounds.get("x") or 0)
        region_y = int(bounds.get("y") or 0)
        region_width = int(bounds.get("width") or 0)
        region_height = int(bounds.get("height") or 0)
    except (TypeError, ValueError):
        return []
    if region_width <= 0 or region_height <= 0:
        return []

    collection_kind = collection_region_kind(region_name)
    if not collection_kind:
        return []

    item_bounds = collection_items_for_region(region)
    items: list[dict[str, Any]] = []
    dark_collection = region_name in {
        "audit-log",
        "deployment-queue",
        "incident-timeline",
    }
    for index, item in enumerate(item_bounds, start=1):
        item_meta = {
            "id": f"item-{index:02d}",
            "bounds": item,
            "fill": "rgba(0, 0, 0, 0)",
        }
        if dark_collection:
            item_meta["border_bottom"] = "rgba(148, 163, 184, 0.105)"
        items.append(
            item_meta
        )

    if not items:
        return []
    return [
        {
            "id": f"{region.get('name')}-collection",
            "bounds": {"x": region_x, "y": region_y, "width": region_width, "height": region_height},
            "kind": f"synthetic-{collection_kind}-collection",
            "items": items,
        }
    ]


def is_dark_collection_noise_placeholder(region: dict[str, Any], element: dict[str, Any], bg: tuple[int, int, int]) -> bool:
    if element.get("asset_path") or is_text_element(element) or luminance(bg) >= 96:
        return False
    region_name = str(region.get("name") or "").lower()
    if region_name not in {"audit-log", "regional-health"}:
        return False
    kind = str(element.get("kind_hint") or "")
    if kind not in {"text-line", "icon-or-badge", "primitive", "control-or-card-fragment", "shape-or-sparkline"}:
        return False
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    if width <= 0 or height <= 0:
        return False
    if kind == "primitive" and (width <= 10 or height <= 10):
        return True
    if kind == "shape-or-sparkline" and region_name == "audit-log":
        return height <= 70 and width <= 180
    return height <= 28


def is_light_control_noise_placeholder(region: dict[str, Any], element: dict[str, Any], bg: tuple[int, int, int]) -> bool:
    if element.get("asset_path") or is_text_element(element) or luminance(bg) < 180:
        return False
    region_name = str(region.get("name") or "").lower()
    if region_name not in {"intro-actions", "bottom-nav"}:
        return False
    kind = str(element.get("kind_hint") or "")
    if kind not in {"text-line", "icon-or-badge", "primitive", "control-or-card-fragment"}:
        return False
    if region_name == "intro-actions":
        return True
    bounds = element.get("bounds") or {}
    return float(bounds.get("height") or 0) <= 34


def is_light_micro_fragment_noise(region: dict[str, Any], element: dict[str, Any], bg: tuple[int, int, int]) -> bool:
    if element.get("asset_path") or is_text_element(element) or luminance(bg) < 180:
        return False
    region_name = str(region.get("name") or "").lower()
    if region_name not in {
        "ai-assistant",
        "header",
        "insights-row",
        "intro-actions",
        "itinerary",
        "packing-checklist",
        "recommendations",
        "tabs",
    }:
        return False
    kind = str(element.get("kind_hint") or "")
    if kind not in {"icon-or-badge", "text-line", "primitive", "control-or-card-fragment"}:
        return False
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    area = width * height
    if width <= 0 or height <= 0:
        return False
    if height <= 8 and area <= 900:
        return True
    if kind == "icon-or-badge" and area <= 360:
        return True
    if kind == "primitive" and width <= 10 and height <= 120:
        return True
    return False


def is_bottom_nav_center_backing_noise(region: dict[str, Any], element: dict[str, Any], bg: tuple[int, int, int]) -> bool:
    if element.get("asset_path") or is_text_element(element) or luminance(bg) < 180:
        return False
    if str(region.get("name") or "").lower() != "bottom-nav":
        return False
    kind = str(element.get("kind_hint") or "")
    if kind not in {"shape-or-sparkline", "control-or-card-fragment"}:
        return False
    region_bounds = region.get("bounds") or {}
    bounds = element.get("bounds") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    if width < 100 or height < 48:
        return False
    center_x, _ = bounds_center(bounds)
    region_center_x = float(region_bounds.get("x") or 0) + float(region_bounds.get("width") or 0) / 2
    return abs(center_x - region_center_x) <= max(42.0, width * 0.35)


def is_ai_assistant_input_backing_noise(region: dict[str, Any], element: dict[str, Any], bg: tuple[int, int, int]) -> bool:
    if element.get("asset_path") or is_text_element(element) or luminance(bg) < 180:
        return False
    if str(region.get("name") or "").lower() != "ai-assistant":
        return False
    if str(element.get("kind_hint") or "") != "shape-or-sparkline":
        return False
    region_bounds = region.get("bounds") or {}
    bounds = element.get("bounds") or {}
    try:
        region_y = float(region_bounds.get("y") or 0)
        region_height = float(region_bounds.get("height") or 0)
        region_width = float(region_bounds.get("width") or 0)
        y = float(bounds.get("y") or 0)
        width = float(bounds.get("width") or 0)
        height = float(bounds.get("height") or 0)
    except (TypeError, ValueError):
        return False
    if region_width <= 0 or region_height <= 0:
        return False
    return y >= region_y + region_height * 0.68 and width >= region_width * 0.82 and 36 <= height <= 64


def synthetic_group_shells(region: dict[str, Any], bg: tuple[int, int, int]) -> list[dict[str, Any]]:
    """Infer obvious group backgrounds that primitive detection splits into parts."""
    region_name = str(region.get("name") or "").lower()
    region_bounds = region.get("bounds") or {}
    try:
        region_x = int(region_bounds.get("x") or 0)
        region_y = int(region_bounds.get("y") or 0)
        region_width = int(region_bounds.get("width") or 0)
        region_height = int(region_bounds.get("height") or 0)
    except (TypeError, ValueError):
        return []
    if region_width <= 0 or region_height <= 0:
        return []

    elements = [element for element in region.get("elements") or [] if isinstance(element, dict)]
    asset_elements = [element for element in elements if element.get("asset_path")]
    shells: list[dict[str, Any]] = []
    bg_lum = luminance(bg)
    is_light = bg_lum >= 180
    light_card_border = "rgba(148, 163, 184, 0.10)"

    if is_light and region_name == "intro-actions":
        for asset in asset_elements:
            bounds = asset.get("bounds") or {}
            if str(asset.get("asset_type") or "") != "icon":
                continue
            center_x, _ = bounds_center(bounds)
            if center_x < region_x + region_width * 0.45:
                continue
            width = 106
            x = max(region_x, min(region_x + region_width - width, int(round(center_x - width / 2))))
            shells.append(
                make_shell(
                    region,
                    f"action-{int(round(center_x))}",
                    {"x": x, "y": region_y, "width": width, "height": region_height},
                    14,
                    "rgba(255, 255, 255, 0.24)",
                    "rgba(148, 163, 184, 0.12)",
                    "none",
                )
            )

    if is_light and region_name == "tabs":
        shells.append(
            make_shell(
                region,
                "active-underline",
                {"x": region_x + 5, "y": region_y + region_height - 4, "width": 132, "height": 4},
                3,
                "rgba(15, 158, 150, 0.95)",
                "rgba(15, 158, 150, 0)",
                "none",
                "synthetic-decoration",
            )
        )

    if is_light and region_name == "bottom-nav":
        indicator_width = min(170, max(96, int(round(region_width * 0.18))))
        shells.append(
            make_shell(
                region,
                "home-indicator",
                {
                    "x": region_x + int(round((region_width - indicator_width) / 2)),
                    "y": region_y + max(0, region_height - 10),
                    "width": indicator_width,
                    "height": 5,
                },
                4,
                "rgba(17, 24, 39, 0.18)",
                "rgba(17, 24, 39, 0)",
                "none",
                "synthetic-decoration",
            )
        )

    if is_light and region_name in {"itinerary", "packing-checklist", "ai-assistant"}:
        shells.append(
            make_shell(
                region,
                "card",
                {"x": region_x, "y": region_y, "width": region_width, "height": region_height},
                14,
                "rgba(255, 255, 255, 0)",
                light_card_border,
                "none",
                "synthetic-card-shell",
            )
        )

    return shells


def ocr_covered_placeholders(region: dict[str, Any]) -> set[str]:
    text_bounds = [
        element.get("bounds") or {}
        for element in region.get("elements") or []
        if isinstance(element, dict) and is_text_element(element) and element.get("source") == "ocr"
    ]
    covered: set[str] = set()
    for element in region.get("elements") or []:
        if not isinstance(element, dict) or not element.get("id"):
            continue
        if element.get("asset_path") or is_text_element(element):
            continue
        kind = str(element.get("kind_hint") or "")
        bounds = element.get("bounds") or {}
        height = float(bounds.get("height") or 0)
        width = float(bounds.get("width") or 0)
        if kind not in {"text-line", "control-or-card-fragment", "primitive", "icon-or-badge"}:
            continue
        if height > 44 or width < 5:
            continue
        area = bounds_area(bounds)
        if area <= 0:
            continue
        if kind == "icon-or-badge" and area > 1400:
            continue
        point = bounds_center(bounds)
        for text_bound in text_bounds:
            overlap = bounds_intersection(bounds, text_bound)
            overlap_ratio = overlap / area
            required_overlap = 0.25 if height <= 26 else 0.45
            if overlap_ratio >= required_overlap or (height <= 26 and bounds_contains(text_bound, point)):
                covered.add(str(element["id"]))
                break
    return covered


def is_reference_backing_asset(element: dict[str, Any]) -> bool:
    return str(element.get("asset_role") or "") == "chart-reference-backing" or element.get("render_asset_in_library") is False


def asset_covered_placeholders(region: dict[str, Any], chart_rendering: str = "asset") -> set[str]:
    asset_bounds = [
        element.get("bounds") or {}
        for element in region.get("elements") or []
        if (
            isinstance(element, dict)
            and element.get("asset_path")
            and not (chart_rendering == "library" and is_reference_backing_asset(element))
        )
    ]
    covered: set[str] = set()
    if not asset_bounds:
        return covered
    for element in region.get("elements") or []:
        if not isinstance(element, dict) or not element.get("id"):
            continue
        if element.get("asset_path") or is_text_element(element):
            continue
        if is_light_card_shell(region, element):
            continue
        bounds = element.get("bounds") or {}
        area = bounds_area(bounds)
        if area <= 0:
            continue
        kind = str(element.get("kind_hint") or "")
        width = float(bounds.get("width") or 0)
        height = float(bounds.get("height") or 0)
        small_visual_fragment = (
            kind in {"icon-or-badge", "primitive", "shape-or-sparkline", "control-or-card-fragment"}
            and area <= 2400
            and max(width, height) <= 72
        )
        point = bounds_center(bounds)
        for asset_bound in asset_bounds:
            overlap = bounds_intersection(bounds, asset_bound)
            overlap_ratio = overlap / area
            if (
                overlap_ratio >= 0.70
                or (overlap_ratio >= 0.45 and bounds_contains(asset_bound, point))
                or (small_visual_fragment and overlap_ratio >= 0.32 and bounds_contains(asset_bound, point))
            ):
                covered.add(str(element["id"]))
                break
    return covered


def asset_covered_texts(region: dict[str, Any]) -> set[str]:
    control_bounds = [
        element.get("bounds") or {}
        for element in region.get("elements") or []
        if (
            isinstance(element, dict)
            and element.get("asset_path")
            and (
                str(element.get("asset_type") or "") in {"badge", "component", "control"}
                or str(element.get("asset_source") or "") == "component-fragment"
            )
        )
    ]
    covered: set[str] = set()
    if not control_bounds:
        return covered
    for element in region.get("elements") or []:
        if not isinstance(element, dict) or not element.get("id") or not is_text_element(element):
            continue
        bounds = element.get("bounds") or {}
        area = bounds_area(bounds)
        if area <= 0:
            continue
        point = bounds_center(bounds)
        for asset_bound in control_bounds:
            overlap = bounds_intersection(bounds, asset_bound)
            if overlap / area >= 0.55 or bounds_contains(asset_bound, point):
                covered.add(str(element["id"]))
                break
    return covered


def component_asset_covered_elements(region: dict[str, Any], chart_rendering: str = "asset") -> set[str]:
    coverers = [
        element
        for element in region.get("elements") or []
        if (
            isinstance(element, dict)
            and element.get("asset_path")
            and not (chart_rendering == "library" and is_reference_backing_asset(element))
            and (
                str(element.get("asset_type") or "") == "component"
                or str(element.get("asset_source") or "") == "component-fragment"
            )
        )
    ]
    covered: set[str] = set()
    if not coverers:
        return covered
    for element in region.get("elements") or []:
        if not isinstance(element, dict) or not element.get("id"):
            continue
        if element in coverers:
            continue
        bounds = element.get("bounds") or {}
        element_area = bounds_area(bounds)
        if element_area <= 0:
            continue
        point = bounds_center(bounds)
        for coverer in coverers:
            coverer_bounds = coverer.get("bounds") or {}
            if not coverer_bounds or str(coverer.get("id") or "") == str(element.get("id") or ""):
                continue
            overlap = bounds_intersection(bounds, coverer_bounds)
            if overlap <= 0:
                continue
            overlap_ratio = overlap / element_area
            if overlap_ratio >= 0.72 or (overlap_ratio >= 0.45 and bounds_contains(coverer_bounds, point)):
                covered.add(str(element["id"]))
                break
    return covered


def radius_for(element: dict[str, Any]) -> int:
    bounds = element.get("bounds") or {}
    width = int(bounds.get("width") or 0)
    height = int(bounds.get("height") or 0)
    kind = str(element.get("kind_hint") or "")
    element_type = str(element.get("type") or "")
    if element_type == "image" and str(element.get("asset_type")) == "avatar":
        return max(4, min(width, height) // 2)
    if "icon" in kind or element_type == "icon":
        return max(3, min(width, height) // 4)
    if "control" in kind or "card" in kind:
        return min(12, max(4, min(width, height) // 5))
    if "text-line" in kind:
        return 2
    return min(8, max(1, min(width, height) // 8))


def normalize_manifest(raw: dict[str, Any]) -> list[dict[str, Any]]:
    regions = raw.get("regions") if isinstance(raw, dict) else None
    if not isinstance(regions, list):
        raise SystemExit("Element manifest has no regions list.")
    return [region for region in regions if isinstance(region, dict)]


def has_chart_hosts(regions: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(element, dict) and is_chart_host(element)
        for region in regions
        for element in region.get("elements") or []
    )


def render_index(title: str, include_echarts: bool = False) -> str:
    echarts_script = '    <script src="./assets/echarts.min.js"></script>\n' if include_echarts else ""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <link rel="stylesheet" href="./styles.css" />
  </head>
  <body>
    <div class="toolbar" data-capture-hidden>
      <div>
        <strong>Pixel Twin Lab</strong>
        <span id="canvasMeta">Loading reference...</span>
      </div>
      <div class="toolbar-actions" role="group" aria-label="View mode">
        <button class="mode-button is-active" type="button" data-mode-button="rebuilt">Rebuilt</button>
        <button class="mode-button" type="button" data-mode-button="reference">Reference</button>
        <button class="mode-button" type="button" data-mode-button="overlay">Overlay</button>
        <button class="mode-button" type="button" data-mode-button="exact">Exact Slice</button>
      </div>
      <label class="opacity-control">
        Overlay
        <input id="overlayOpacity" type="range" min="0" max="100" value="42" />
      </label>
    </div>

    <main class="stage-shell">
      <section class="stage" aria-label="Pixel twin stage">
        <img class="reference-layer" src="./assets/reference.png" alt="Reference UI" />
        <div class="slice-layer" aria-hidden="true"></div>
        <div class="rebuilt-layer">
          <div class="pt-element-rebuild" aria-label="Element manifest rebuilt layer"></div>
        </div>
      </section>
    </main>

{echarts_script}    <script src="./script.js"></script>
  </body>
</html>
"""


def ensure_echarts_script(index_html: str, include_echarts: bool) -> str:
    if not include_echarts or "./assets/echarts.min.js" in index_html:
        return index_html
    return index_html.replace('    <script src="./script.js"></script>', '    <script src="./assets/echarts.min.js"></script>\n    <script src="./script.js"></script>')


def chart_option(region_name: str, chart_role: str = "panel") -> dict[str, Any]:
    dark = True
    base_text = "#d8e2ee" if dark else "#1f2937"
    muted = "#7d8da0" if dark else "#64748b"
    grid = "#2b3846" if dark else "#e2e8f0"
    if region_name == "system-performance-chart":
        tick_labels = ["14:10", "14:15", "14:20", "14:25", "14:30", "14:35"]
        labels = [tick_labels[index // 5] if index % 5 == 0 else "" for index in range(30)]
        latency = [132, 121, 138, 127, 136, 132, 141, 129, 125, 131, 118, 116, 124, 136, 139, 132, 126, 121, 119, 132, 137, 128, 126, 134, 131, 137, 118, 186, 126, 123]
        error = [8, 9, 10, 7, 11, 8, 9, 12, 10, 9, 12, 13, 8, 10, 12, 14, 10, 9, 8, 9, 12, 11, 10, 9, 10, 12, 15, 64, 11, 9]
        traffic = [262, 244, 232, 255, 238, 271, 279, 248, 264, 282, 291, 276, 319, 274, 296, 286, 302, 279, 246, 255, 272, 281, 236, 229, 273, 287, 299, 314, 252, 275]
        saturation = [66, 61, 68, 64, 69, 71, 67, 72, 68, 66, 64, 65, 62, 69, 71, 67, 66, 64, 61, 63, 67, 69, 70, 66, 68, 71, 69, 73, 70, 72]
        if chart_role == "plot":
            return {
                "animation": False,
                "backgroundColor": "transparent",
                "color": ["#f59e0b", "#ef4444", "#22c7f2", "#22c55e"],
                "grid": {"left": 0, "right": 0, "top": 0, "bottom": 0, "containLabel": False},
                "xAxis": {
                    "type": "category",
                    "boundaryGap": False,
                    "data": labels,
                    "axisLine": {"show": False},
                    "axisTick": {"show": False},
                    "axisLabel": {"show": False},
                    "splitLine": {"show": True, "lineStyle": {"color": grid, "type": "dashed", "opacity": 0.72}},
                },
                "yAxis": [
                    {
                        "type": "value",
                        "min": 0,
                        "max": 400,
                        "axisLine": {"show": False},
                        "axisTick": {"show": False},
                        "axisLabel": {"show": False},
                        "splitLine": {"show": True, "lineStyle": {"color": grid, "type": "dashed", "opacity": 0.72}},
                    },
                    {
                        "type": "value",
                        "min": 0,
                        "max": 100,
                        "axisLine": {"show": False},
                        "axisTick": {"show": False},
                        "axisLabel": {"show": False},
                        "splitLine": {"show": False},
                    },
                ],
                "series": [
                    {"name": "Latency p95 (ms)", "type": "line", "data": latency, "showSymbol": False, "smooth": True, "lineStyle": {"width": 2, "color": "#f59e0b"}},
                    {"name": "Error Rate (%)", "type": "line", "data": error, "showSymbol": False, "smooth": True, "lineStyle": {"width": 2, "color": "#ef4444"}},
                    {"name": "Traffic (rps)", "type": "line", "data": traffic, "showSymbol": False, "smooth": True, "lineStyle": {"width": 2, "color": "#22c7f2"}},
                    {"name": "Saturation (%)", "type": "line", "yAxisIndex": 1, "data": saturation, "showSymbol": False, "smooth": True, "lineStyle": {"width": 2, "color": "#22c55e"}},
                ],
            }
        return {
            "animation": False,
            "backgroundColor": "transparent",
            "color": ["#f59e0b", "#ef4444", "#22c7f2", "#22c55e"],
            "title": {
                "text": "System Performance",
                "left": 14,
                "top": 12,
                "textStyle": {"color": base_text, "fontSize": 15, "fontWeight": 700},
            },
            "legend": {
                "left": 14,
                "top": 58,
                "itemWidth": 8,
                "itemHeight": 8,
                "icon": "circle",
                "textStyle": {"color": base_text, "fontSize": 11},
                "data": ["Latency p95 (ms)", "Error Rate (%)", "Traffic (rps)", "Saturation (%)"],
            },
            "grid": {"left": 44, "right": 42, "top": 96, "bottom": 32},
            "xAxis": {
                "type": "category",
                "boundaryGap": False,
                "data": labels,
                "axisLine": {"lineStyle": {"color": grid}},
                "axisTick": {"show": False},
                "axisLabel": {"color": muted, "fontSize": 11, "interval": 0},
                "splitLine": {"show": True, "lineStyle": {"color": grid, "type": "dashed", "opacity": 0.65}},
            },
            "yAxis": [
                {
                    "type": "value",
                    "min": 0,
                    "max": 400,
                    "interval": 100,
                    "axisLabel": {"color": muted, "fontSize": 11},
                    "axisLine": {"show": False},
                    "axisTick": {"show": False},
                    "splitLine": {"show": True, "lineStyle": {"color": grid, "type": "dashed", "opacity": 0.65}},
                },
                {
                    "type": "value",
                    "min": 0,
                    "max": 100,
                    "interval": 25,
                    "axisLabel": {"color": base_text, "fontSize": 11, "formatter": "{value}%"},
                    "axisLine": {"show": False},
                    "axisTick": {"show": False},
                    "splitLine": {"show": False},
                },
            ],
            "series": [
                {"name": "Latency p95 (ms)", "type": "line", "data": latency, "showSymbol": False, "smooth": True, "itemStyle": {"color": "#f59e0b"}, "lineStyle": {"width": 2, "color": "#f59e0b"}},
                {"name": "Error Rate (%)", "type": "line", "data": error, "showSymbol": False, "smooth": True, "itemStyle": {"color": "#ef4444"}, "lineStyle": {"width": 2, "color": "#ef4444"}},
                {"name": "Traffic (rps)", "type": "line", "data": traffic, "showSymbol": False, "smooth": True, "itemStyle": {"color": "#22c7f2"}, "lineStyle": {"width": 2, "color": "#22c7f2"}},
                {"name": "Saturation (%)", "type": "line", "yAxisIndex": 1, "data": saturation, "showSymbol": False, "smooth": True, "itemStyle": {"color": "#22c55e"}, "lineStyle": {"width": 2, "color": "#22c55e"}},
            ],
        }
    return {
        "animation": False,
        "backgroundColor": "transparent",
        "grid": {"left": 24, "right": 18, "top": 18, "bottom": 22},
        "xAxis": {"type": "category", "data": ["1", "2", "3", "4", "5"], "axisLabel": {"color": muted}, "axisLine": {"lineStyle": {"color": grid}}},
        "yAxis": {"type": "value", "axisLabel": {"color": muted}, "splitLine": {"lineStyle": {"color": grid}}},
        "series": [{"type": "line", "data": [12, 18, 14, 24, 20], "showSymbol": False, "lineStyle": {"color": "#22c7f2", "width": 2}}],
    }


def render_chart_bootstrap(regions: list[dict[str, Any]]) -> str:
    configs: dict[str, dict[str, Any]] = {}
    for region in regions:
        name = str(region.get("name") or "chart")
        for element in region.get("elements") or []:
            if not isinstance(element, dict) or not is_chart_host(element) or not element.get("id"):
                continue
            configs[str(element["id"])] = chart_option(name, str(element.get("chart_role") or "panel"))
    if not configs:
        return ""
    return f"""

window.__PIXEL_TWIN_CHARTS__ = {json.dumps(configs, ensure_ascii=False, separators=(",", ":"))};

function initPixelTwinCharts() {{
  if (!window.echarts || !window.__PIXEL_TWIN_CHARTS__) return;
  for (const el of document.querySelectorAll("[data-chart-host='echarts']")) {{
    if (el.dataset.chartReady === "1") continue;
    const key = el.getAttribute("data-chart-config-id");
    const option = window.__PIXEL_TWIN_CHARTS__[key];
    if (!option) continue;
    const chart = window.echarts.init(el, null, {{
      renderer: "svg",
      devicePixelRatio: 1,
      width: el.clientWidth,
      height: el.clientHeight
    }});
    chart.setOption(option, true);
    el.dataset.chartReady = "1";
  }}
}}

window.addEventListener("load", () => {{
  requestAnimationFrame(initPixelTwinCharts);
}});
"""


def render_html(
    regions: list[dict[str, Any]],
    chart_rendering: str = "asset",
    background_patches: list[dict[str, Any]] | None = None,
    forbid_assets: bool = False,
) -> str:
    lines = ['          <div class="pt-element-rebuild" aria-label="Element manifest rebuilt layer">']
    for patch in background_patches or []:
        patch_id = html.escape(str(patch.get("id") or "gap"), quote=True)
        patch_class = css_ident(f"bg-{patch.get('id') or 'gap'}")
        lines.append(
            f'            <span class="pt-background-patch pt-background-patch--{patch_class}" '
            f'data-background-patch="{patch_id}" aria-hidden="true"></span>'
        )
    for region in regions:
        name = html.escape(str(region.get("name") or "region"), quote=True)
        region_class = css_ident(str(region.get("name") or "region"))
        lines.append(f'            <section class="pt-region pt-region--{region_class}" aria-label="{name}">')
        for collection in synthetic_collection_shells(region):
            collection_id = html.escape(str(collection["id"]), quote=True)
            collection_class = css_ident(str(collection["id"]))
            collection_kind = html.escape(str(collection.get("kind") or "synthetic-collection"), quote=True)
            lines.append(
                f'              <span class="pt-element pt-element--{collection_class} pt-element--synthetic-collection" '
                f'data-element="{collection_id}" data-kind="{collection_kind}" role="list">'
            )
            for item in collection.get("items") or []:
                item_id = html.escape(str(item.get("id") or "item"), quote=True)
                item_class = css_ident(f"{collection['id']}-{item.get('id') or 'item'}")
                lines.append(
                    f'                <span class="pt-collection-item pt-collection-item--{item_class}" '
                    f'data-element-item="{item_id}" role="listitem"></span>'
                )
            lines.append("              </span>")
        for shell in synthetic_group_shells(region, (255, 255, 255)):
            shell_id = html.escape(str(shell["id"]), quote=True)
            shell_kind = html.escape(str(shell.get("kind") or "synthetic-shell"), quote=True)
            shell_class = css_ident(str(shell["id"]))
            lines.append(
                f'              <span class="pt-element pt-element--{shell_class} pt-element--synthetic-shell" '
                f'data-element="{shell_id}" data-kind="{shell_kind}"></span>'
            )
        for shell in synthetic_text_control_shells(region):
            shell_id = html.escape(str(shell["id"]), quote=True)
            shell_kind = html.escape(str(shell.get("kind") or "synthetic-shell"), quote=True)
            shell_class = css_ident(str(shell["id"]))
            lines.append(
                f'              <span class="pt-element pt-element--{shell_class} pt-element--synthetic-shell" '
                f'data-element="{shell_id}" data-kind="{shell_kind}"></span>'
            )
        for element in region.get("elements") or []:
            if not isinstance(element, dict) or not element.get("id"):
                continue
            element_id = html.escape(str(element["id"]), quote=True)
            kind = html.escape(str(element.get("kind_hint") or "primitive"), quote=True)
            asset_id = str(element.get("asset_id") or "")
            element_class = css_ident(str(element["id"]))
            tag_name = "button" if str(element.get("type") or "") == "control" else "span"
            tag_extra = ' type="button"' if tag_name == "button" else ""
            base_attrs = f'class="pt-element pt-element--{element_class}" data-element="{element_id}" data-kind="{kind}"'
            if is_chart_host(element) and chart_rendering == "library":
                chart_key = html.escape(str(element["id"]), quote=True)
                chart_attrs = (
                    f'class="pt-element pt-element--{element_class} pt-chart-host" '
                    f'data-element="{element_id}" data-kind="{kind}"'
                )
                lines.append(
                    f'              <{tag_name} {chart_attrs}{tag_extra} data-chart-host="echarts" '
                    f'data-chart-config-id="{chart_key}"></{tag_name}>'
                )
            elif is_chart_host(element):
                chart_attrs = (
                    f'class="pt-element pt-element--{element_class} pt-chart-host pt-chart-host--semantic" '
                    f'data-element="{element_id}" data-kind="{kind}"'
                )
                lines.append(f"              <{tag_name} {chart_attrs}{tag_extra}>")
                lines.append(
                    '                <svg aria-hidden="true" focusable="false" '
                    'viewBox="0 0 1 1" preserveAspectRatio="none">'
                    '<path d="M0 0H1V1H0Z" /></svg>'
                )
                lines.append(f"              </{tag_name}>")
            elif (
                element.get("asset_path")
                and not forbid_assets
                and not (chart_rendering == "library" and is_reference_backing_asset(element))
            ):
                asset_attr = html.escape(asset_id, quote=True) if asset_id else element_id
                asset_type = html.escape(str(element.get("asset_type") or "asset"), quote=True)
                src = html.escape(asset_src(str(element["asset_path"])), quote=True)
                lines.append(f'              <{tag_name} {base_attrs}{tag_extra} data-element-asset-id="{asset_attr}" data-asset-type="{asset_type}">')
                lines.append(f'                <img src="{src}" alt="" draggable="false" />')
                if str(element.get("type") or "") == "text" and str(element.get("content") or "").strip():
                    semantic_text = html.escape(display_text(region, element))
                    lines.append(f'                <span class="pt-semantic-text">{semantic_text}</span>')
                lines.append(f"              </{tag_name}>")
            elif is_text_element(element):
                text = render_text_markup(region, element)
                lines.append(f"              <{tag_name} {base_attrs}{tag_extra}>{text}</{tag_name}>")
            elif fallback_text := semantic_fallback_text(region, element):
                text = html.escape(fallback_text)
                lines.append(f"              <{tag_name} {base_attrs}{tag_extra}>{text}</{tag_name}>")
            else:
                lines.append(f"              <{tag_name} {base_attrs}{tag_extra}></{tag_name}>")
        lines.append("            </section>")
    lines.append("          </div>")
    return "\n".join(lines)


def render_css(
    regions: list[dict[str, Any]],
    reference: Image.Image,
    page_bg: tuple[int, int, int],
    threshold: int,
    chart_rendering: str = "asset",
    background_patches: list[dict[str, Any]] | None = None,
    surface_chrome: bool = False,
    forbid_assets: bool = False,
) -> str:
    lines = [
        "",
        "/* Pixel Twin Lab element manifest rebuild: start */",
        ".pt-element-rebuild, .pt-region, .pt-element, .pt-background-patch {",
        "  position: absolute;",
        "  box-sizing: border-box;",
        "}",
        ".pt-element-rebuild {",
        "  inset: 0;",
        f"  background: {hex_color(page_bg)};",
        "}",
        ".pt-region {",
        "  overflow: hidden;",
        "}",
        ".pt-background-patch {",
        "  display: block;",
        "  pointer-events: none;",
        "}",
        ".pt-element {",
        "  all: unset;",
        "  appearance: none;",
        "  border: 0;",
        "  box-sizing: border-box;",
        "  display: block;",
        "  font-family: inherit;",
        "  margin: 0;",
        "  overflow: hidden;",
        "  padding: 0;",
        "  pointer-events: none;",
        "  position: absolute;",
        "  z-index: 2;",
        "}",
        ".pt-element--synthetic-collection, .pt-element--synthetic-shell {",
        "  z-index: 1;",
        "}",
        ".pt-element[data-kind=\"ocr-text-line\"] {",
        "  z-index: 7;",
        "}",
        ".pt-chart-host {",
        "  overflow: hidden;",
        "}",
        ".pt-chart-host > div, .pt-chart-host svg, .pt-chart-host canvas {",
        "  display: block;",
        "  width: 100%;",
        "  height: 100%;",
        "}",
        ".pt-element[data-kind=\"ocr-text-line\"] {",
        "  overflow: visible;",
        "  white-space: nowrap;",
        "  background: transparent !important;",
        "  letter-spacing: 0;",
        "}",
        ".pt-accent-teal { color: #0c9b92; }",
        ".pt-wave { display: inline-block; margin-left: 2px; }",
    ]
    if not forbid_assets:
        lines.extend(
            [
                ".pt-element[data-element-asset-id] {",
                "  background: transparent !important;",
                "  z-index: 6;",
                "}",
                ".pt-element > img {",
                "  display: block;",
                "  width: 100%;",
                "  height: 100%;",
                "  object-fit: fill;",
                "}",
                ".pt-semantic-text {",
                "  position: absolute;",
                "  width: 1px;",
                "  height: 1px;",
                "  margin: -1px;",
                "  overflow: hidden;",
                "  clip: rect(0 0 0 0);",
                "  white-space: nowrap;",
                "  opacity: 0;",
                "}",
            ]
        )
    for patch in background_patches or []:
        bounds = patch.get("bounds") or {}
        patch_class = css_ident(f"bg-{patch.get('id') or 'gap'}")
        color = patch.get("color") or page_bg
        src = str(patch.get("src") or "")
        background = f"background: {hex_color(color)};"
        if src and not forbid_assets:
            background = f"background: {hex_color(color)} url('{asset_src(src)}') center / 100% 100% no-repeat;"
        lines.extend(
            [
                f".pt-background-patch--{patch_class} {{",
                f"  left: {int(bounds.get('x') or 0)}px;",
                f"  top: {int(bounds.get('y') or 0)}px;",
                f"  width: {int(bounds.get('width') or 0)}px;",
                f"  height: {int(bounds.get('height') or 0)}px;",
                f"  {background}",
                "}",
            ]
        )
    for region in regions:
        bounds = region.get("bounds") or {}
        region_name = str(region.get("name") or "").lower()
        region_class = css_ident(str(region.get("name") or "region"))
        bg = region_bg(region, reference, page_bg)
        surface = (
            region_surface_style(region_name, bg, page_bg)
            if surface_chrome
            else {
                "background": hex_color(bg),
                "border": "0",
                "border-radius": "0",
                "box-shadow": "none",
            }
        )
        region_x = int(bounds.get("x") or 0)
        region_y = int(bounds.get("y") or 0)
        lines.extend(
            [
                f".pt-region--{region_class} {{",
                f"  left: {region_x}px;",
                f"  top: {region_y}px;",
                f"  width: {int(bounds.get('width') or 0)}px;",
                f"  height: {int(bounds.get('height') or 0)}px;",
                f"  background: {surface.get('background')};",
                f"  border: {surface.get('border')};",
                f"  border-radius: {surface.get('border-radius')};",
                f"  box-shadow: {surface.get('box-shadow')};",
            "}",
            ]
        )
        suppressed = ocr_covered_placeholders(region)
        suppressed_text: set[str] = set()
        if not forbid_assets:
            suppressed |= asset_covered_placeholders(region, chart_rendering)
            suppressed |= component_asset_covered_elements(region, chart_rendering)
            suppressed_text = asset_covered_texts(region)
        for collection in synthetic_collection_shells(region):
            collection_bounds = collection.get("bounds") or {}
            collection_class = css_ident(str(collection["id"]))
            lines.extend(
                [
                    f".pt-element--{collection_class} {{",
                    f"  left: {int(collection_bounds.get('x') or 0) - region_x}px;",
                    f"  top: {int(collection_bounds.get('y') or 0) - region_y}px;",
                    f"  width: {int(collection_bounds.get('width') or 0)}px;",
                    f"  height: {int(collection_bounds.get('height') or 0)}px;",
                    "  overflow: hidden;",
                    "  background: transparent;",
                    "  border: 0;",
                    "  opacity: 1;",
                    "}",
                ]
            )
            for item in collection.get("items") or []:
                item_bounds = item.get("bounds") or {}
                item_class = css_ident(f"{collection['id']}-{item.get('id') or 'item'}")
                item_rule = [
                    f".pt-collection-item--{item_class} {{",
                    "  position: absolute;",
                    "  display: block;",
                    f"  left: {int(item_bounds.get('x') or 0) - int(collection_bounds.get('x') or 0)}px;",
                    f"  top: {int(item_bounds.get('y') or 0) - int(collection_bounds.get('y') or 0)}px;",
                    f"  width: {int(item_bounds.get('width') or 0)}px;",
                    f"  height: {int(item_bounds.get('height') or 0)}px;",
                    f"  background: {item.get('fill')};",
                    "  box-sizing: border-box;",
                ]
                if item.get("border_bottom"):
                    item_rule.append(f"  border-bottom: 1px solid {item.get('border_bottom')};")
                item_rule.append("}")
                lines.extend(item_rule)
        for shell in synthetic_group_shells(region, bg):
            shell_bounds = shell.get("bounds") or {}
            shell_class = css_ident(str(shell["id"]))
            lines.extend(
                [
                    f".pt-element--{shell_class} {{",
                    f"  left: {int(shell_bounds.get('x') or 0) - region_x}px;",
                    f"  top: {int(shell_bounds.get('y') or 0) - region_y}px;",
                    f"  width: {int(shell_bounds.get('width') or 0)}px;",
                    f"  height: {int(shell_bounds.get('height') or 0)}px;",
                    f"  border-radius: {int(shell.get('radius') or 12)}px;",
                    f"  background: {shell.get('fill')};",
                    f"  border: 1px solid {shell.get('border')};",
                    f"  box-shadow: {shell.get('shadow')};",
                    "  opacity: 1;",
                    "}",
                ]
            )
        for shell in synthetic_text_control_shells(region):
            shell_bounds = shell.get("bounds") or {}
            shell_class = css_ident(str(shell["id"]))
            lines.extend(
                [
                    f".pt-element--{shell_class} {{",
                    f"  left: {int(shell_bounds.get('x') or 0) - region_x}px;",
                    f"  top: {int(shell_bounds.get('y') or 0) - region_y}px;",
                    f"  width: {int(shell_bounds.get('width') or 0)}px;",
                    f"  height: {int(shell_bounds.get('height') or 0)}px;",
                    f"  border-radius: {int(shell.get('radius') or 14)}px;",
                    f"  background: {shell.get('fill')};",
                    f"  border: 1px solid {shell.get('border')};",
                    f"  box-shadow: {shell.get('shadow')};",
                    "  opacity: 1;",
                    "}",
                ]
            )
        for element in region.get("elements") or []:
            if not isinstance(element, dict) or not element.get("id"):
                continue
            element_bounds = element.get("bounds") or {}
            shell_bounds = None
            element_class = css_ident(str(element["id"]))
            text_affix: dict[str, Any] = {}
            text_font_size = 0
            text_like = is_text_like_placeholder(element, reference, bg, threshold)
            timeline_rail = is_timeline_rail_placeholder(region, element)
            nested_container = is_nested_container_placeholder(region, element)
            low_signal_fragment = is_low_signal_light_fragment(element, reference, bg, threshold)
            collection_noise = is_dark_collection_noise_placeholder(region, element, bg)
            light_control_noise = is_light_control_noise_placeholder(region, element, bg)
            light_micro_noise = is_light_micro_fragment_noise(region, element, bg)
            bottom_nav_backing_noise = is_bottom_nav_center_backing_noise(region, element, bg)
            ai_assistant_input_noise = is_ai_assistant_input_backing_noise(region, element, bg)
            surface = (is_surface_placeholder(element) or bool(shell_bounds)) and not text_like and not nested_container
            light_card_shell = is_light_card_shell(region, element)
            background_fill = nested_container
            color = median_crop_color(element, reference, bg) if surface or background_fill else element_color(element, reference, bg, threshold)
            if light_card_shell:
                color = (255, 255, 255)
            radius = min(18, max(8, int((element_bounds.get("height") or 0) // 2))) if shell_bounds else radius_for(element)
            if (
                str(region.get("name") or "").lower() == "bottom-nav"
                and element.get("asset_path")
                and str(element.get("asset_type") or "") == "control"
            ):
                radius = max(radius, min(int(element_bounds.get("width") or 0), int(element_bounds.get("height") or 0)) // 2)
            opacity = 1.0
            if text_like:
                opacity = 0.58 if float((element_bounds or {}).get("height") or 0) <= 9 else 0.48
            if (
                str(element["id"]) in suppressed
                or str(element["id"]) in suppressed_text
                or low_signal_fragment
                or collection_noise
                or light_control_noise
                or light_micro_noise
                or bottom_nav_backing_noise
                or ai_assistant_input_noise
            ):
                opacity = 0.0
            elif background_fill:
                opacity = 0.92
            elif surface:
                opacity = 0.98
            rule = [
                f".pt-element--{element_class} {{",
                f"  left: {int(element_bounds.get('x') or 0) - region_x}px;",
                f"  top: {int(element_bounds.get('y') or 0) - region_y}px;",
                f"  width: {int(element_bounds.get('width') or 0)}px;",
                f"  height: {int(element_bounds.get('height') or 0)}px;",
                f"  border-radius: {radius}px;",
                f"  opacity: {opacity:.2f};",
            ]
            fallback_text = semantic_fallback_text(region, element)
            if is_text_element(element) or fallback_text:
                region_name = str(region.get("name") or "").lower()
                text_element = element if is_text_element(element) else {**element, "content": fallback_text, "source": "semantic-fallback"}
                if (
                    luminance(bg) < 96
                    or region_name in {"intro-actions", "tabs"}
                    or should_resample_light_text_color(region_name, bg, text_element)
                ):
                    color = text_color(text_element, reference, bg, threshold)
                font_size = font_size_for(text_element, region)
                text_font_size = font_size
                text_affix = text_visual_affix(region, text_element)
                line_height = max(font_size, int(element_bounds.get("height") or font_size))
                rule.extend(
                    [
                        "  background: transparent;",
                        f"  color: {hex_color(color)};",
                        f"  font-size: {font_size}px;",
                        f"  line-height: {line_height}px;",
                        f"  font-weight: {text_weight(text_element, bg, region_name)};",
                    ]
                )
                if needs_dark_text_baseline_shift(region_name, bg, text_element):
                    rule.append("  transform: translateY(-1px);")
                if shell_bounds:
                    original_bounds = element.get("bounds") or {}
                    padding_left = max(0, int(original_bounds.get("x") or 0) - int(shell_bounds.get("x") or 0))
                    if (
                        ("checklist" in str(region.get("name") or "").lower() or "packing" in str(region.get("name") or "").lower())
                        and str(element.get("content") or "").strip().lower() == "all items"
                    ):
                        padding_left = 22
                    rule.extend(
                        [
                            "  background: rgba(255, 255, 255, 0.92);",
                            "  border: 1px solid rgba(132, 148, 164, 0.22);",
                            f"  padding-left: {padding_left}px;",
                            "  padding-right: 12px;",
                            "  color: #111827;",
                        ]
                    )
            else:
                if is_chart_host(element):
                    rule.append("  background: transparent;")
                elif timeline_rail:
                    line_color = hex_color(color)
                    rule.append(
                        "  background: "
                        f"linear-gradient(to right, transparent 0%, transparent calc(50% - 1px), "
                        f"{line_color} calc(50% - 1px), {line_color} calc(50% + 1px), "
                        "transparent calc(50% + 1px), transparent 100%);"
                    )
                elif text_like:
                    line_color = hex_color(color)
                    rule.append(
                        "  background: "
                        f"linear-gradient(to bottom, transparent 0%, transparent 42%, {line_color} 42%, "
                        f"{line_color} 58%, transparent 58%, transparent 100%);"
                    )
                else:
                    rule.append(f"  background: {hex_color(color)};")
                if surface and not is_chart_host(element):
                    border = "rgba(128, 128, 128, 0.06)" if light_card_shell else "rgba(128, 128, 128, 0.12)"
                    rule.append(f"  border: 1px solid {border};")
            rule.append("}")
            lines.extend(rule)
            if text_affix and opacity > 0.0 and text_font_size > 0:
                for pseudo_name, pseudo in text_affix.items():
                    if pseudo_name not in {"before", "after"} or not isinstance(pseudo, dict):
                        continue
                    size_scale = float(pseudo.get("font_size") or 1.0)
                    pseudo_size = max(6, int(round(text_font_size * size_scale)))
                    pseudo_rule = [
                        f".pt-element--{element_class}::{pseudo_name} {{",
                        f"  content: {css_string(str(pseudo.get('content') or ''))};",
                        f"  position: {pseudo.get('position') or 'absolute'};",
                        f"  left: {pseudo.get('left') or '0'};",
                        f"  top: {pseudo.get('top') or '0'};",
                        f"  color: {pseudo.get('color') or 'inherit'};",
                        f"  font-size: {pseudo_size}px;",
                        "  line-height: inherit;",
                        f"  font-weight: {int(pseudo.get('font_weight') or text_weight(text_element, bg, region_name))};",
                        "  pointer-events: none;",
                        "}",
                    ]
                    lines.extend(pseudo_rule)
    lines.append("/* Pixel Twin Lab element manifest rebuild: end */")
    return "\n".join(lines) + "\n"


def inject_rebuilt_layer(index_html: str, rebuilt_html: str) -> str:
    pattern = r"<div\s+[^>]*class=[\"'][^\"']*\brebuilt-layer\b[^\"']*[\"'][^>]*>[\s\S]*?</div>\s*</section>"
    match = re.search(pattern, index_html)
    if not match:
        return index_html
    replacement = f'<div class="rebuilt-layer">\n{rebuilt_html}\n        </div>\n      </section>'
    return index_html[: match.start()] + replacement + index_html[match.end() :]


def copy_echarts_runtime(skill_root: Path, out_dir: Path) -> str:
    source = skill_root / "node_modules" / "echarts" / "dist" / "echarts.min.js"
    if not source.exists():
        raise SystemExit(
            "Missing ECharts runtime for chart-host rendering. Run `npm install echarts` in the pixel-twin-lab skill root."
        )
    target_dir = out_dir / "assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "echarts.min.js"
    shutil.copy2(source, target)
    return str(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="Pixel Twin Lab output directory")
    parser.add_argument("--manifest-name", default="element-manifest.json", help="Element manifest filename/path")
    parser.add_argument("--reference", help="Reference image path; defaults to <out-dir>/assets/reference.png")
    parser.add_argument("--template-root", help="Optional skill root; defaults to parent of this script")
    parser.add_argument("--threshold", type=int, default=12, help="Foreground sampling threshold")
    parser.add_argument(
        "--chart-rendering",
        choices=("asset", "library"),
        default="asset",
        help="Render chart-host elements as existing chart assets for visual QA, or as ECharts SVG for semantic chart-host QA.",
    )
    parser.add_argument(
        "--surface-chrome",
        action="store_true",
        help="Experiment: infer region card chrome with non-layout-affecting shadows. Off by default until it beats baseline.",
    )
    parser.add_argument(
        "--forbid-assets",
        action="store_true",
        help="Strict component mode: do not render element image assets or gap slice background images.",
    )
    parser.add_argument("--no-backup", action="store_true", help="Do not write .before-element-manifest backups")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    manifest_path = Path(args.manifest_name).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = out_dir / manifest_path
    reference_path = Path(args.reference).expanduser().resolve() if args.reference else out_dir / "assets/reference.png"
    skill_root = Path(args.template_root).expanduser().resolve() if args.template_root else Path(__file__).resolve().parents[1]
    template = skill_root / "assets" / "prototype-template"
    if not (template / "styles.css").exists():
        raise SystemExit(f"Missing prototype template: {template}")

    manifest = load_json(manifest_path)
    regions = normalize_manifest(manifest)
    include_echarts = args.chart_rendering == "library" and has_chart_hosts(regions)
    reference = Image.open(reference_path).convert("RGB")
    config = load_json(out_dir / "lab-config.json", {})
    page_bg = parse_hex(str(config.get("background") or ""), estimate_border_bg(reference))
    background_patches = background_patches_from_config(
        config,
        reference,
        page_bg,
        allow_slice_assets=not args.forbid_assets,
    )

    index_path = out_dir / "index.html"
    styles_path = out_dir / "styles.css"
    script_path = out_dir / "script.js"
    if not args.no_backup:
        for path in (index_path, styles_path, script_path):
            if path.exists():
                backup = path.with_suffix(path.suffix + ".before-element-manifest")
                if not backup.exists():
                    shutil.copy2(path, backup)

    if include_echarts:
        copy_echarts_runtime(skill_root, out_dir)

    rebuilt_html = render_html(
        regions,
        chart_rendering=args.chart_rendering,
        background_patches=background_patches,
        forbid_assets=args.forbid_assets,
    )
    if index_path.exists():
        index_html = inject_rebuilt_layer(index_path.read_text(encoding="utf-8"), rebuilt_html)
        if index_html == index_path.read_text(encoding="utf-8"):
            index_html = render_index("Pixel Twin Lab Element Manifest", include_echarts=include_echarts)
            index_html = inject_rebuilt_layer(index_html, rebuilt_html)
    else:
        index_html = render_index("Pixel Twin Lab Element Manifest", include_echarts=include_echarts)
        index_html = inject_rebuilt_layer(index_html, rebuilt_html)
    index_html = ensure_echarts_script(index_html, include_echarts)
    index_path.write_text(index_html, encoding="utf-8")

    base_css = (template / "styles.css").read_text(encoding="utf-8")
    styles_path.write_text(
        base_css
        + "\n\n"
        + render_css(
            regions,
            reference,
            page_bg,
            args.threshold,
            args.chart_rendering,
            background_patches=background_patches,
            surface_chrome=args.surface_chrome,
            forbid_assets=args.forbid_assets,
        ),
        encoding="utf-8",
    )
    script_text = (template / "script.js").read_text(encoding="utf-8") + render_chart_bootstrap(regions)
    script_path.write_text(script_text, encoding="utf-8")

    asset_elements = sum(
        1
        for region in regions
        for element in region.get("elements") or []
        if isinstance(element, dict) and element.get("asset_path")
    )
    total_elements = sum(
        1
        for region in regions
        for element in region.get("elements") or []
        if isinstance(element, dict) and element.get("id")
    )
    text_elements = sum(
        1
        for region in regions
        for element in region.get("elements") or []
        if isinstance(element, dict) and is_text_element(element)
    )
    print(
        json.dumps(
            {
                "out_dir": str(out_dir),
                "manifest": str(manifest_path),
                "regions": len(regions),
                "elements": total_elements,
                "asset_elements": asset_elements,
                "text_elements": text_elements,
                "chart_hosts": include_echarts,
                "background_patches": len(background_patches),
                "forbid_assets": args.forbid_assets,
                "index": str(index_path),
                "styles": str(styles_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

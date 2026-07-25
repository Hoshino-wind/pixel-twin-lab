"""Bounded visual repair hints for static screenshot mismatches.

The module inspects a bounded hotspot pool and exposes at most three
conservative, actionable repair hints. It never edits source files, writes
artifacts, or claims a repair when local visual correspondence is ambiguous.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from os import PathLike
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


MAX_HINTS = 3
MAX_CANDIDATE_HINTS = 12
MAX_REPAIR_SOLVES = 6
MAX_SOURCE_CANDIDATES = 2
MAX_INDEXED_NODES = 240
MAX_COLOR_SAMPLES = 16_384
ROUND_DIGITS = 4

SAFE_COMPUTED_PROPERTIES = {
    "align-items",
    "background-color",
    "border",
    "border-radius",
    "box-shadow",
    "color",
    "display",
    "font-size",
    "font-weight",
    "gap",
    "grid-template-columns",
    "height",
    "justify-content",
    "letter-spacing",
    "line-height",
    "margin",
    "object-fit",
    "opacity",
    "padding",
    "position",
    "width",
}

STYLE_EXTENSIONS = {".css", ".less", ".pcss", ".sass", ".scss", ".styl"}
GENERIC_SELECTOR_TOKENS = {
    "absolute",
    "block",
    "container",
    "flex",
    "grid",
    "hidden",
    "inline",
    "relative",
    "root",
    "wrapper",
}
UTILITY_PREFIXES = {
    "align-items": ("items-",),
    "background-color": ("bg-",),
    "border": ("border",),
    "border-radius": ("rounded",),
    "color": ("text-",),
    "font-size": ("text-",),
    "font-weight": ("font-",),
    "gap": ("gap-", "space-"),
    "grid-template-columns": ("grid-cols-",),
    "height": ("h-", "min-h-", "max-h-"),
    "justify-content": ("justify-",),
    "line-height": ("leading-",),
    "margin": ("m-", "mx-", "my-", "mt-", "mr-", "mb-", "ml-"),
    "padding": ("p-", "px-", "py-", "pt-", "pr-", "pb-", "pl-"),
    "position": ("absolute", "fixed", "relative", "sticky"),
    "width": ("w-", "min-w-", "max-w-"),
}


def _rounded(value: float) -> float:
    return round(float(value), ROUND_DIGITS)


def _bounded_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _rectangle_overlap(
    left: list[float] | tuple[float, ...],
    right: list[float] | tuple[float, ...],
) -> tuple[float, bool]:
    left_x, left_y, left_width, left_height = (float(value) for value in left)
    right_x, right_y, right_width, right_height = (float(value) for value in right)
    overlap_width = max(
        0.0,
        min(left_x + left_width, right_x + right_width) - max(left_x, right_x),
    )
    overlap_height = max(
        0.0,
        min(left_y + left_height, right_y + right_height) - max(left_y, right_y),
    )
    center_x = left_x + left_width / 2
    center_y = left_y + left_height / 2
    contains_center = (
        right_x <= center_x <= right_x + right_width
        and right_y <= center_y <= right_y + right_height
    )
    return overlap_width * overlap_height, contains_center


def _css_levers(node: dict[str, Any], hotspot: dict[str, Any]) -> list[str]:
    computed = node.get("computed") if isinstance(node.get("computed"), dict) else {}
    role = str(node.get("role") or "")
    display = str(computed.get("display") or "")
    bounds = node.get("bounds") or [0, 0, 0, 0]
    visible_bounds = node.get("visible_bounds") or bounds
    hotspot_bounds = hotspot.get("bounds") or [0, 0, 0, 0]
    x, y, hotspot_width, hotspot_height = (float(value) for value in hotspot_bounds)
    visible_width, visible_height = float(visible_bounds[2]), float(visible_bounds[3])
    node_x, node_y = float(visible_bounds[0]), float(visible_bounds[1])
    edge_distance = min(
        abs(x - node_x),
        abs(y - node_y),
        abs((x + hotspot_width) - (node_x + visible_width)),
        abs((y + hotspot_height) - (node_y + visible_height)),
    )
    near_edge = edge_distance <= max(4.0, min(visible_width, visible_height) * 0.08)

    if role in {"canvas", "image", "img", "svg", "video", "iframe"}:
        candidates = ["width", "height", "object-fit"]
    elif node.get("has_direct_text"):
        candidates = ["color", "font-size", "line-height", "font-weight"]
    elif display in {"flex", "inline-flex", "grid", "inline-grid"}:
        candidates = ["gap", "padding", "align-items", "grid-template-columns"]
    elif hotspot.get("dominant_color_hint") and computed.get("background-color") not in {
        None,
        "rgba(0, 0, 0, 0)",
        "transparent",
    }:
        candidates = ["background-color", "border", "border-radius"]
    elif near_edge:
        candidates = ["width", "height", "padding", "border"]
    else:
        candidates = ["background-color", "padding", "border-radius"]
    return list(dict.fromkeys(candidates))[:3]


def attach_dom_hints(
    report: dict[str, Any],
    dom_index: dict[str, Any] | None,
    *,
    maximum: int = MAX_HINTS,
) -> None:
    """Attach one bounded, non-authoritative DOM candidate to each hotspot."""

    if not isinstance(dom_index, dict) or dom_index.get("space") != "capture-css-px":
        return
    nodes = dom_index.get("nodes")
    if not isinstance(nodes, list):
        return
    maximum = max(0, min(MAX_CANDIDATE_HINTS, int(maximum)))
    mapped = 0
    for hotspot in (report.get("hotspots") or [])[:maximum]:
        hotspot_bounds = hotspot.get("bounds")
        if not isinstance(hotspot_bounds, list) or len(hotspot_bounds) != 4:
            continue
        hotspot_area = max(1.0, float(hotspot_bounds[2]) * float(hotspot_bounds[3]))
        best: tuple[float, bool, dict[str, Any]] | None = None
        for node in nodes[:MAX_INDEXED_NODES]:
            bounds = node.get("bounds") if isinstance(node, dict) else None
            visible_bounds = node.get("visible_bounds") if isinstance(node, dict) else None
            if not isinstance(bounds, list) or len(bounds) != 4:
                continue
            if not isinstance(visible_bounds, list) or len(visible_bounds) != 4:
                visible_bounds = bounds
            try:
                element_area = max(1.0, float(bounds[2]) * float(bounds[3]))
                overlap, center = _rectangle_overlap(hotspot_bounds, visible_bounds)
            except (TypeError, ValueError):
                continue
            if overlap <= 0:
                continue
            hotspot_coverage = overlap / hotspot_area
            element_coverage = overlap / element_area
            scale_match = min(hotspot_area, element_area) / max(hotspot_area, element_area)
            score = (
                0.34 * hotspot_coverage
                + 0.28 * element_coverage
                + 0.18 * scale_match
                + (0.14 if center else 0.0)
                + (0.04 if node.get("visual") else 0.0)
                + min(0.02, max(0, int(node.get("depth") or 0)) / 1000)
            )
            if str(node.get("tag") or "").lower() in {"html", "body"}:
                score -= 0.25
            if best is None or score > best[0]:
                best = (score, center, node)
        if best is None:
            continue

        score, center, node = best
        levers = _css_levers(node, hotspot)
        raw_computed = node.get("computed") if isinstance(node.get("computed"), dict) else {}
        computed: dict[str, str] = {}
        for property_name in levers:
            if property_name not in SAFE_COMPUTED_PROPERTIES:
                continue
            value = raw_computed.get(property_name)
            if value is None and property_name in {"width", "height"}:
                index = 2 if property_name == "width" else 3
                value = f"{round(float(node['bounds'][index]), 2):g}px"
            if value is not None:
                computed[property_name] = _bounded_text(value, 80)
        unique = bool(node.get("unique"))
        mapped_bounds = node.get("bounds") or [0, 0, 0, 0]
        mapped_visible = node.get("visible_bounds") or mapped_bounds
        try:
            mapped_area = max(1.0, float(mapped_bounds[2]) * float(mapped_bounds[3]))
            mapped_overlap, _ = _rectangle_overlap(hotspot_bounds, mapped_visible)
            mapped_coverage = mapped_overlap / mapped_area
            ownership_ratio = mapped_area / hotspot_area
        except (IndexError, TypeError, ValueError):
            mapped_coverage = 0.0
            ownership_ratio = float("inf")
        precise_ownership = (
            unique
            and center
            and score >= 0.5
            and mapped_coverage >= 0.1
            and ownership_ratio <= 8.0
        )
        confidence = (
            "high"
            if precise_ownership
            else "medium"
            if score >= 0.4
            else "low"
        )
        if dom_index.get("truncated") and confidence == "high":
            confidence = "medium"
        hotspot["dom"] = {
            "selector": _bounded_text(node.get("selector"), 160),
            "tag": _bounded_text(node.get("tag"), 24).lower(),
            "role": _bounded_text(node.get("role"), 24).lower(),
            "selector_unique": unique,
            "bounds": [round(float(value), 2) for value in node.get("bounds", [])[:4]],
            "visible_bounds": [
                round(float(value), 2)
                for value in (node.get("visible_bounds") or node.get("bounds") or [])[:4]
            ],
            "confidence": confidence,
            "computed": computed,
            "css_levers": levers,
        }
        mapped += 1
    report["dom_mapping"] = {
        "mapped_hotspots": mapped,
        "indexed_nodes": min(len(nodes), MAX_INDEXED_NODES),
        "truncated": bool(dom_index.get("truncated")),
    }


def _fit_size(size: tuple[int, int], maximum: int = 64) -> tuple[int, int]:
    width, height = size
    scale = min(1.0, maximum / max(width, height))
    return max(4, round(width * scale)), max(4, round(height * scale))


def _integer_bounds(bounds: list[Any], size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    try:
        x, y, width, height = (round(float(value)) for value in bounds)
    except (TypeError, ValueError):
        return None
    image_width, image_height = size
    if width < 4 or height < 4:
        return None
    if x < 0 or y < 0 or x + width > image_width or y + height > image_height:
        return None
    return x, y, width, height


def _context_box(
    bounds: tuple[int, int, int, int], size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    x, y, width, height = bounds
    margin_x = max(3, min(18, round(width * 0.18)))
    margin_y = max(3, min(18, round(height * 0.18)))
    box = (x - margin_x, y - margin_y, x + width + margin_x, y + height + margin_y)
    if box[0] < 0 or box[1] < 0 or box[2] > size[0] or box[3] > size[1]:
        return None
    return box


def _signature(
    image: Image.Image,
    box: tuple[int, int, int, int],
    sample_size: tuple[int, int],
) -> tuple[bytes, bytes, float, float]:
    gray = image.crop(box).convert("L").resize(sample_size, Image.Resampling.BOX)
    normalized = ImageOps.autocontrast(gray, cutoff=2).filter(ImageFilter.GaussianBlur(0.35))
    edge_image = normalized.filter(ImageFilter.FIND_EDGES)
    edges = bytearray(edge_image.point(lambda value: 255 if value >= 20 else 0).tobytes())
    width, height = sample_size
    for x in range(width):
        edges[x] = 0
        edges[(height - 1) * width + x] = 0
    for y in range(height):
        edges[y * width] = 0
        edges[y * width + width - 1] = 0
    edge_density = sum(value > 0 for value in edges) / max(1, len(edges))
    contrast = float(ImageStat.Stat(normalized).stddev[0])
    return normalized.tobytes(), bytes(edges), contrast, edge_density


def _edge_similarity(left: bytes, right: bytes, size: tuple[int, int]) -> float:
    left_count = sum(value > 0 for value in left)
    right_count = sum(value > 0 for value in right)
    if left_count == 0 and right_count == 0:
        return 1.0
    if left_count == 0 or right_count == 0:
        return 0.0
    left_near = Image.frombytes("L", size, left).filter(ImageFilter.MaxFilter(3)).tobytes()
    right_near = Image.frombytes("L", size, right).filter(ImageFilter.MaxFilter(3)).tobytes()
    left_recall = sum(
        value > 0 and right_near[index] > 0 for index, value in enumerate(left)
    ) / left_count
    right_recall = sum(
        value > 0 and left_near[index] > 0 for index, value in enumerate(right)
    ) / right_count
    return (left_recall + right_recall) / 2


def _axis_values(radius: int, step: int) -> list[int]:
    values = list(range(-radius, radius + 1, step))
    values.extend((-radius, 0, radius))
    return sorted(set(values))


def _refinement_values(
    center: int,
    radius: int,
    *,
    minimum: int = 4,
    maximum_points: int = 17,
) -> list[int]:
    lower = max(minimum, center - radius)
    upper = max(lower, center + radius)
    step = max(1, math.ceil((upper - lower) / max(1, maximum_points - 1)))
    values = list(range(lower, upper + 1, step))
    values.extend((lower, center, upper))
    return sorted({value for value in values if value >= minimum})


def _position_values(center: int, radius: int, maximum_points: int = 11) -> list[int]:
    lower = center - radius
    upper = center + radius
    step = max(1, math.ceil((upper - lower) / max(1, maximum_points - 1)))
    values = list(range(lower, upper + 1, step))
    values.extend((lower, center, upper))
    return sorted(set(values))


def _confidence_cap(value: str, cap: str) -> str:
    levels = {"low": 0, "medium": 1, "high": 2}
    bounded = min(levels.get(value, 0), levels.get(cap, 0))
    return ("low", "medium", "high")[bounded]


def _css_rgb(value: Any) -> tuple[int, int, int] | None:
    match = re.fullmatch(
        r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,[^)]*)?\)",
        str(value or ""),
    )
    if not match:
        return None
    channels = tuple(int(item) for item in match.groups())
    return channels if all(0 <= item <= 255 for item in channels) else None


def _target_color_from_pixels(
    reference: Image.Image,
    actual: Image.Image,
    bounds: tuple[int, int, int, int],
    dom: dict[str, Any],
    tolerance: int = 8,
) -> str | None:
    x, y, width, height = bounds
    step = max(1, math.ceil(math.sqrt(width * height / MAX_COLOR_SAMPLES)))
    offset = step // 2
    reference_pixels = reference.load()
    actual_pixels = actual.load()

    def coordinates():
        for sample_y in range(y + offset, y + height, step):
            for sample_x in range(x + offset, x + width, step):
                yield sample_x, sample_y

    computed = dom.get("computed") if isinstance(dom.get("computed"), dict) else {}
    actual_text_color = _css_rgb(computed.get("color")) if dom.get("role") == "text" else None
    reference_background: tuple[int, int, int] | None = None
    if actual_text_color is not None:
        reference_bins: Counter[tuple[int, int, int]] = Counter()
        actual_bins: Counter[tuple[int, int, int]] = Counter()
        sample_count = 0
        for sample_x, sample_y in coordinates():
            reference_pixel = reference_pixels[sample_x, sample_y]
            actual_pixel = actual_pixels[sample_x, sample_y]
            reference_bins[tuple(channel // 16 for channel in reference_pixel)] += 1
            actual_bins[tuple(channel // 16 for channel in actual_pixel)] += 1
            sample_count += 1
        if sample_count == 0:
            return None
        reference_bin, reference_count = reference_bins.most_common(1)[0]
        actual_bin, actual_count = actual_bins.most_common(1)[0]
        if reference_count / sample_count < 0.3 or actual_count / sample_count < 0.3:
            return None
        reference_background = tuple(channel * 16 + 7 for channel in reference_bin)
        actual_background = tuple(channel * 16 + 7 for channel in actual_bin)
        if max(
            abs(channel - background)
            for channel, background in zip(actual_text_color, actual_background)
        ) < 24:
            return None
        intersection = 0
        actual_foreground_count = 0
        for sample_x, sample_y in coordinates():
            reference_pixel = reference_pixels[sample_x, sample_y]
            actual_pixel = actual_pixels[sample_x, sample_y]
            actual_foreground = max(
                abs(channel - expected)
                for channel, expected in zip(actual_pixel, actual_text_color)
            ) <= 28
            reference_foreground = max(
                abs(channel - background)
                for channel, background in zip(reference_pixel, reference_background)
            ) >= 24
            actual_foreground_count += int(actual_foreground)
            intersection += int(actual_foreground and reference_foreground)
        if (
            intersection < 8
            or intersection / max(1, actual_foreground_count) < 0.65
        ):
            return None

    def selected_pixel(
        reference_pixel: tuple[int, int, int],
        actual_pixel: tuple[int, int, int],
    ) -> bool:
        if max(abs(left - right) for left, right in zip(reference_pixel, actual_pixel)) <= tolerance:
            return False
        if actual_text_color is None:
            return True
        actual_foreground = max(
            abs(channel - expected)
            for channel, expected in zip(actual_pixel, actual_text_color)
        ) <= 28
        reference_foreground = max(
            abs(channel - background)
            for channel, background in zip(reference_pixel, reference_background or (0, 0, 0))
        ) >= 24
        return actual_foreground and reference_foreground

    bins: Counter[tuple[int, int, int]] = Counter()
    selected_count = 0
    for sample_x, sample_y in coordinates():
        reference_pixel = reference_pixels[sample_x, sample_y]
        actual_pixel = actual_pixels[sample_x, sample_y]
        if not selected_pixel(reference_pixel, actual_pixel):
            continue
        bins[tuple(channel // 16 for channel in reference_pixel)] += 1
        selected_count += 1
    minimum = 8 if actual_text_color is not None else 16
    if selected_count < minimum:
        return None
    dominant, count = bins.most_common(1)[0]
    if count / selected_count < (0.35 if actual_text_color is not None else 0.55):
        return None
    cluster: list[tuple[int, int, int]] = []
    for sample_x, sample_y in coordinates():
        reference_pixel = reference_pixels[sample_x, sample_y]
        actual_pixel = actual_pixels[sample_x, sample_y]
        if selected_pixel(reference_pixel, actual_pixel) and tuple(
            channel // 16 for channel in reference_pixel
        ) == dominant:
            cluster.append(reference_pixel)
    medians = [
        sorted(pixel[channel] for pixel in cluster)[len(cluster) // 2]
        for channel in range(3)
    ]
    deviations = [
        sorted(abs(pixel[channel] - medians[channel]) for pixel in cluster)[len(cluster) // 2]
        for channel in range(3)
    ]
    if max(deviations) > 10:
        return None
    return "#{:02x}{:02x}{:02x}".format(*medians)


def _uncertain(reason: str, match_pct: float | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "uncertain",
        "confidence": "low",
        "reason": reason,
    }
    if match_pct is not None:
        result["match_pct"] = _rounded(match_pct)
    return result


def _solve_repair(
    reference: Image.Image,
    actual: Image.Image,
    hotspot: dict[str, Any],
) -> dict[str, Any]:
    dom = hotspot.get("dom") if isinstance(hotspot.get("dom"), dict) else {}
    bounds = _integer_bounds(dom.get("bounds") or [], actual.size)
    if bounds is None:
        return _uncertain("element is clipped or too small")
    actual_box = _context_box(bounds, actual.size)
    if actual_box is None:
        return _uncertain("element context is clipped")
    sample_size = _fit_size((actual_box[2] - actual_box[0], actual_box[3] - actual_box[1]))
    actual_luma, actual_edges, contrast, edge_density = _signature(
        actual, actual_box, sample_size
    )
    if contrast < 6 and edge_density < 0.008:
        return _uncertain("insufficient local visual structure")

    evaluated: dict[tuple[int, int, int, int], float] = {}

    def score(candidate: tuple[int, int, int, int]) -> float:
        if candidate in evaluated:
            return evaluated[candidate]
        context = _context_box(candidate, reference.size)
        if context is None:
            evaluated[candidate] = -1.0
            return -1.0
        candidate_luma, candidate_edges, _, _ = _signature(
            reference, context, sample_size
        )
        luma_error = sum(
            abs(left - right) for left, right in zip(actual_luma, candidate_luma)
        ) / (255 * max(1, len(actual_luma)))
        similarity = 0.65 * (1.0 - luma_error) + 0.35 * _edge_similarity(
            actual_edges, candidate_edges, sample_size
        )
        evaluated[candidate] = max(0.0, min(1.0, similarity))
        return evaluated[candidate]

    x, y, width, height = bounds
    current_score = score(bounds)
    if current_score >= 0.94:
        target_color = _target_color_from_pixels(reference, actual, bounds, dom)
        if target_color:
            return {
                "kind": "color",
                "confidence": _confidence_cap("high", str(dom.get("confidence") or "low")),
                "match_pct": _rounded(current_score * 100),
                "target_color": target_color,
            }
        return _uncertain("no unique repair delta", current_score * 100)
    radius = min(64, max(12, round(max(width, height) * 0.85)))
    step = max(2, math.ceil(radius / 8))
    for dx in _axis_values(radius, step):
        for dy in _axis_values(radius, step):
            score((x + dx, y + dy, width, height))
    best = max(evaluated, key=evaluated.get)

    base_x, base_y, _, _ = best
    hotspot_bounds = hotspot.get("bounds") or []
    try:
        hotspot_x = round(float(hotspot_bounds[0]))
        hotspot_y = round(float(hotspot_bounds[1]))
    except (IndexError, TypeError, ValueError):
        hotspot_x, hotspot_y = x, y
    for width_scale in (0.82, 0.91, 1.0, 1.09, 1.18):
        candidate_width = max(4, round(width * width_scale))
        for height_scale in (0.82, 0.91, 1.0, 1.09, 1.18):
            candidate_height = max(4, round(height * height_scale))
            x_values = {
                base_x,
                x,
                hotspot_x,
                x + round((width - candidate_width) / 2),
            }
            y_values = {
                base_y,
                y,
                hotspot_y,
                y + round((height - candidate_height) / 2),
            }
            for candidate_x in x_values:
                for candidate_y in y_values:
                    score((candidate_x, candidate_y, candidate_width, candidate_height))
    best = max(evaluated, key=evaluated.get)

    best_x, best_y, best_width, best_height = best
    for candidate_x in _position_values(best_x, step):
        for candidate_y in _position_values(best_y, step):
            score((candidate_x, candidate_y, best_width, best_height))
    best = max(evaluated, key=evaluated.get)
    best_x, best_y, best_width, best_height = best

    width_radius = max(3, round(width * 0.06))
    for candidate_width in _refinement_values(best_width, width_radius):
        score((best_x, best_y, candidate_width, best_height))
        score(
            (
                best_x + round((best_width - candidate_width) / 2),
                best_y,
                candidate_width,
                best_height,
            )
        )
    best = max(evaluated, key=evaluated.get)
    best_x, best_y, best_width, best_height = best
    height_radius = max(3, round(height * 0.06))
    for candidate_height in _refinement_values(best_height, height_radius):
        score((best_x, best_y, best_width, candidate_height))
        score(
            (
                best_x,
                best_y + round((best_height - candidate_height) / 2),
                best_width,
                candidate_height,
            )
        )
    best = max(evaluated, key=evaluated.get)

    for _ in range(2):
        best_x, best_y, best_width, best_height = best
        position_radius = max(6, step)
        for candidate_x in _position_values(best_x, position_radius):
            for candidate_y in _position_values(best_y, position_radius):
                score((candidate_x, candidate_y, best_width, best_height))
        best = max(evaluated, key=evaluated.get)
        best_x, best_y, best_width, best_height = best
        dimension_radius = max(4, round(max(width, height) * 0.1))
        for candidate_width in _refinement_values(best_width, dimension_radius):
            score((best_x, best_y, candidate_width, best_height))
        best = max(evaluated, key=evaluated.get)
        best_x, best_y, best_width, best_height = best
        for candidate_height in _refinement_values(best_height, dimension_radius):
            score((best_x, best_y, best_width, candidate_height))
        best = max(evaluated, key=evaluated.get)
    best_score = evaluated[best]

    if best_score - current_score < 0.025:
        best = bounds
        best_score = current_score

    center_threshold_x = max(4, round(width * 0.12))
    center_threshold_y = max(4, round(height * 0.12))
    size_threshold_x = max(8, round(width * 0.18))
    size_threshold_y = max(8, round(height * 0.18))

    alternate_seeds = [
        (candidate, value)
        for candidate, value in evaluated.items()
        if value >= 0
        and (
            abs((candidate[0] + candidate[2] / 2) - (best[0] + best[2] / 2))
            >= center_threshold_x
            or abs((candidate[1] + candidate[3] / 2) - (best[1] + best[3] / 2))
            >= center_threshold_y
            or abs(candidate[2] - best[2]) >= size_threshold_x
            or abs(candidate[3] - best[3]) >= size_threshold_y
        )
    ]
    if alternate_seeds:
        alternate, _ = max(alternate_seeds, key=lambda item: item[1])
        alternate_x, alternate_y, alternate_width, alternate_height = alternate
        alternate_radius = max(2, step)
        for candidate_x in _position_values(alternate_x, alternate_radius):
            for candidate_y in _position_values(alternate_y, alternate_radius):
                score((candidate_x, candidate_y, alternate_width, alternate_height))
        for candidate_width in range(max(4, alternate_width - 2), alternate_width + 3):
            score((alternate_x, alternate_y, candidate_width, alternate_height))
        for candidate_height in range(max(4, alternate_height - 2), alternate_height + 3):
            score((alternate_x, alternate_y, alternate_width, candidate_height))
        best = max(evaluated, key=evaluated.get)
        best_score = evaluated[best]

    alternatives = [
        value
        for candidate, value in evaluated.items()
        if value >= 0
        and (
            abs((candidate[0] + candidate[2] / 2) - (best[0] + best[2] / 2))
            >= center_threshold_x
            or abs((candidate[1] + candidate[3] / 2) - (best[1] + best[3] / 2))
            >= center_threshold_y
            or abs(candidate[2] - best[2]) >= size_threshold_x
            or abs(candidate[3] - best[3]) >= size_threshold_y
        )
    ]
    ambiguity_gap = best_score - max(alternatives, default=0.0)
    match_pct = best_score * 100
    if best_score < 0.76:
        return _uncertain("local content does not correspond", match_pct)

    dx = best[0] - x
    dy = best[1] - y
    dw = best[2] - width
    dh = best[3] - height
    moved = abs(dx) >= 2 or abs(dy) >= 2
    resized = abs(dw) >= 2 or abs(dh) >= 2
    target_color = None
    if not moved and not resized:
        target_color = _target_color_from_pixels(reference, actual, bounds, dom)
    dom_confidence = str(dom.get("confidence") or "low")

    if moved or resized:
        if (
            abs(dx) >= radius - 1
            or abs(dy) >= radius - 1
            or abs(dw) > width * 0.3
            or abs(dh) > height * 0.3
        ):
            return _uncertain("repair lies outside the bounded search", match_pct)
        if ambiguity_gap < 0.025:
            return _uncertain("local correspondence is ambiguous", match_pct)
        confidence = "high" if best_score >= 0.88 and ambiguity_gap >= 0.05 else "medium"
        confidence = _confidence_cap(confidence, dom_confidence)
        if confidence == "low":
            return _uncertain("DOM ownership is uncertain", match_pct)
        kind = "position_size" if moved and resized else "position" if moved else "size"
        result: dict[str, Any] = {
            "kind": kind,
            "confidence": confidence,
            "match_pct": _rounded(match_pct),
            "target_bounds": list(best),
            "delta": {"x": dx, "y": dy, "width": dw, "height": dh},
        }
        if target_color:
            result["target_color"] = target_color
        return result

    if target_color:
        confidence = _confidence_cap(
            "high" if best_score >= 0.9 else "medium", dom_confidence
        )
        if confidence == "low":
            return _uncertain("DOM ownership is uncertain", match_pct)
        return {
            "kind": "color",
            "confidence": confidence,
            "match_pct": _rounded(match_pct),
            "target_color": target_color,
        }
    return _uncertain("no unique repair delta", match_pct)


def attach_repair_hints(
    report: dict[str, Any],
    dom_index: dict[str, Any] | None,
    reference: Image.Image,
    actual: Image.Image,
    dynamic_regions: list[dict[str, Any]] | None = None,
    repair_exclusions: list[dict[str, Any]] | None = None,
    *,
    output_limit: int = MAX_HINTS,
    solve_limit: int = MAX_REPAIR_SOLVES,
) -> None:
    """Attach bounded DOM and visual repair hints using already-open images."""

    hotspots = list(report.get("hotspots") or [])
    output_limit = max(0, int(output_limit))
    solve_limit = max(0, min(MAX_REPAIR_SOLVES, int(solve_limit)))
    attach_dom_hints(
        report,
        dom_index,
        maximum=min(len(hotspots), MAX_CANDIDATE_HINTS),
    )
    used_selectors: set[str] = set()
    solve_count = 0
    current_dynamic_bounds = {
        tuple(region.get("bounds") or []) for region in (dynamic_regions or [])
    }
    for hotspot in hotspots[:MAX_CANDIDATE_HINTS]:
        dom = hotspot.get("dom") if isinstance(hotspot.get("dom"), dict) else None
        if dom is None:
            continue
        selector = str(dom.get("selector") or "")
        hotspot_bounds = hotspot.get("bounds") or [0, 0, 0, 0]
        exclusions = [
            *((region, False) for region in (dynamic_regions or [])),
            *(
                (region, True)
                for region in (repair_exclusions or [])
                if tuple(region.get("bounds") or []) not in current_dynamic_bounds
            ),
        ]
        overlaps_dynamic = False
        for region, expand in exclusions:
            bounds = region.get("bounds") or [0, 0, 0, 0]
            try:
                padding = (
                    max(4.0, min(24.0, max(float(bounds[2]), float(bounds[3])) * 0.1))
                    if expand
                    else 0.0
                )
                padded = [
                    float(bounds[0]) - padding,
                    float(bounds[1]) - padding,
                    float(bounds[2]) + padding * 2,
                    float(bounds[3]) + padding * 2,
                ]
                overlaps_dynamic = any(
                    _rectangle_overlap(candidate, padded)[0] > 0
                    for candidate in (hotspot_bounds, dom.get("bounds") or [])
                    if isinstance(candidate, list) and len(candidate) == 4
                )
            except (TypeError, ValueError, IndexError):
                continue
            if overlaps_dynamic:
                break
        if not selector:
            hint = _uncertain("DOM selector is missing")
            claim_selector = False
        elif not dom.get("selector_unique"):
            hint = _uncertain("DOM selector is not unique")
            claim_selector = False
        elif dom.get("confidence") != "high":
            hint = _uncertain("DOM ownership is not precise enough")
            claim_selector = False
        elif selector in used_selectors:
            hint = _uncertain("duplicate DOM target")
            claim_selector = False
        elif overlaps_dynamic:
            hint = _uncertain("hotspot overlaps a dynamic surface")
            claim_selector = True
        elif solve_count >= solve_limit:
            hint = _uncertain("repair analysis budget exhausted")
            claim_selector = True
        else:
            solve_count += 1
            hint = _solve_repair(reference, actual, hotspot)
            claim_selector = True
        if selector and claim_selector:
            used_selectors.add(selector)
        hotspot["repair_hint"] = hint

    original_order = {id(hotspot): index for index, hotspot in enumerate(hotspots)}
    selected: list[dict[str, Any]] = []
    selected_ids: set[int] = set()
    selected_by_selector: dict[str, dict[str, Any]] = {}
    confidence_order = {"high": 0, "medium": 1, "low": 2}

    def is_actionable(hotspot: dict[str, Any]) -> bool:
        hint = hotspot.get("repair_hint")
        return isinstance(hint, dict) and hint.get("kind") != "uncertain"

    def hint_priority(hotspot: dict[str, Any]) -> int:
        hint = hotspot.get("repair_hint")
        confidence = hint.get("confidence") if isinstance(hint, dict) else None
        return confidence_order.get(str(confidence or ""), 3)

    def add(hotspot: dict[str, Any]) -> bool:
        identity = id(hotspot)
        if identity in selected_ids:
            return False
        dom = hotspot.get("dom") if isinstance(hotspot.get("dom"), dict) else {}
        selector = str(dom.get("selector") or "")
        representative = selected_by_selector.get(selector) if selector else None
        if representative is not None:
            if is_actionable(hotspot) and (
                not is_actionable(representative)
                or hint_priority(hotspot) < hint_priority(representative)
            ):
                representative["dom"] = hotspot["dom"]
                representative["repair_hint"] = hotspot["repair_hint"]
            return False
        selected.append(hotspot)
        selected_ids.add(identity)
        if selector:
            selected_by_selector[selector] = hotspot
        return True

    actionable_hotspots = [
        hotspot
        for hotspot in hotspots[:MAX_CANDIDATE_HINTS]
        if is_actionable(hotspot)
    ]
    actionable_hotspots.sort(
        key=lambda hotspot: (
            hint_priority(hotspot),
            original_order[id(hotspot)],
        )
    )
    anchor = hotspots[0] if hotspots else None
    if output_limit and anchor is not None:
        add(anchor)
    for hotspot in actionable_hotspots:
        if len(selected) >= output_limit:
            dom = hotspot.get("dom") if isinstance(hotspot.get("dom"), dict) else {}
            selector = str(dom.get("selector") or "")
            if not selector or selector not in selected_by_selector:
                continue
        add(hotspot)
    for hotspot in hotspots:
        if len(selected) >= output_limit:
            break
        add(hotspot)
    for rank, hotspot in enumerate(selected, start=1):
        hotspot["rank"] = rank
    report["hotspots"] = selected

    mapping = report.get("dom_mapping")
    if isinstance(mapping, dict):
        mapping["mapped_hotspots"] = sum(
            isinstance(hotspot.get("dom"), dict) for hotspot in selected
        )

    actionable = sum(
        isinstance(hotspot.get("repair_hint"), dict)
        and hotspot["repair_hint"].get("kind") != "uncertain"
        for hotspot in selected
    )
    uncertain = sum(
        isinstance(hotspot.get("repair_hint"), dict)
        and hotspot["repair_hint"].get("kind") == "uncertain"
        for hotspot in selected
    )
    report.pop("repair_summary", None)
    if actionable or uncertain:
        report["repair_summary"] = {
            "actionable": actionable,
            "uncertain": uncertain,
        }


def _selector_atoms(selector: str) -> list[tuple[str, str, bool]]:
    segments = [segment.strip() for segment in selector.split(">") if segment.strip()]
    atoms: list[tuple[str, str, bool]] = []
    for index, segment in enumerate(segments):
        leaf = index == len(segments) - 1
        for prefix, token in re.findall(r"([.#])([A-Za-z_][A-Za-z0-9_-]{0,63})", segment):
            atoms.append(("id" if prefix == "#" else "class", token, leaf))
    if any(token not in GENERIC_SELECTOR_TOKENS for _, token, _ in atoms):
        atoms = [item for item in atoms if item[1] not in GENERIC_SELECTOR_TOKENS]
    return atoms[:4]


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _without_comments(text: str) -> str:
    def blank(match: re.Match[str]) -> str:
        return "".join("\n" if character == "\n" else " " for character in match.group(0))

    text = re.sub(r"/\*.*?\*/|<!--.*?-->", blank, text, flags=re.DOTALL)
    return re.sub(r"(?m)//[^\n]*", blank, text)


def _lever_matches(text: str, token: str, levers: list[str]) -> list[str]:
    matches: list[str] = []
    for lever in levers[:3]:
        prefixes = UTILITY_PREFIXES.get(lever, ())
        if lever in text or any(token.startswith(prefix) for prefix in prefixes):
            matches.append(lever)
    return matches[:3]


def _source_candidates(
    selector: str,
    levers: list[str],
    documents: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    atoms = _selector_atoms(selector)
    if not atoms or (len(atoms) == 1 and atoms[0][1] in GENERIC_SELECTOR_TOKENS):
        return []
    candidates: list[dict[str, Any]] = []
    atom_tokens = [token for _, token, leaf in atoms if leaf] or [
        token for _, token, _ in atoms
    ]
    document_counts = {
        token: sum(
            bool(
                re.search(
                    rf"(?<![A-Za-z0-9_-]){re.escape(token)}(?![A-Za-z0-9_-])",
                    text,
                )
            )
            for _, text in documents
        )
        for _, token, _ in atoms
    }
    for relative, text in documents:
        suffix = Path(relative).suffix.lower()
        best: dict[str, Any] | None = None
        for atom_kind, token, leaf in atoms:
            if atom_kind != "id" and document_counts.get(token, 0) > 20 and len(atom_tokens) < 2:
                continue
            escaped = re.escape(token)
            pattern = re.compile(rf"(?<![A-Za-z0-9_-]){escaped}(?![A-Za-z0-9_-])")
            for match in pattern.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end < 0:
                    line_end = len(text)
                window_end = text.find("\n", line_end + 1)
                if window_end < 0:
                    window_end = len(text)
                line = text[line_start:line_end]
                window = text[line_start:window_end]
                lever_matches = _lever_matches(window, token, levers)
                score = 0
                kind = "class-binding"
                if atom_kind == "id" and (
                    f"#{token}" in line or re.search(rf"\bid\s*=.*{escaped}", line)
                ):
                    score = 100
                    kind = "id-binding"
                elif re.search(rf"\bstyles(?:\.{escaped}|\[['\"]{escaped}['\"]\])", line):
                    score = 88
                    kind = "css-module"
                elif suffix in STYLE_EXTENSIONS and f".{token}" in line:
                    score = 92 if lever_matches else 78
                    kind = "css-selector"
                elif re.search(r"\b(?:class|className)\s*=", line):
                    cooccurring = sum(
                        bool(
                            re.search(
                                rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])",
                                line,
                            )
                        )
                        for value in atom_tokens
                    )
                    utility = bool(_lever_matches(line, token, levers))
                    score = 88 if cooccurring >= 2 else 84 if utility else 72
                    kind = "tailwind-utility" if utility else "class-binding"
                elif re.search(rf"\b(?:cn|clsx)\s*\([^\n]*['\"][^'\"]*{escaped}", line):
                    score = 84 if _lever_matches(line, token, levers) else 72
                    kind = "tailwind-utility" if score == 84 else "class-binding"
                if not leaf:
                    score -= 15
                if score < 60:
                    continue
                item = {
                    "path": relative,
                    "line": _line_number(text, match.start()),
                    "kind": kind,
                    "confidence": "high" if score >= 90 else "medium" if score >= 72 else "low",
                    "matched_levers": lever_matches,
                    "_score": score,
                }
                if best is None or item["_score"] > best["_score"]:
                    best = item
                break
        if best is not None:
            candidates.append(best)
    candidates.sort(key=lambda item: (-item["_score"], item["path"], item["line"]))
    return [
        {key: value for key, value in item.items() if key != "_score"}
        for item in candidates[:MAX_SOURCE_CANDIDATES]
    ]


def attach_source_candidates(
    report: dict[str, Any],
    source_files: list[tuple[str, PathLike[str] | str]],
    *,
    truncated: bool = False,
) -> None:
    """Attach source locations from caller-approved UI files without snippets."""

    eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for hotspot in (report.get("hotspots") or [])[:MAX_HINTS]:
        dom = hotspot.get("dom") if isinstance(hotspot.get("dom"), dict) else {}
        hint = (
            hotspot.get("repair_hint")
            if isinstance(hotspot.get("repair_hint"), dict)
            else None
        )
        if hint is not None and hint.get("kind") != "uncertain" and dom.get("selector"):
            eligible.append((dom, hint))
    if not eligible:
        return

    documents: list[tuple[str, str]] = []
    for relative, value in source_files:
        try:
            text = Path(value).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        documents.append((str(relative), _without_comments(text)))

    mapped = 0
    for dom, hint in eligible:
        candidates = _source_candidates(
            str(dom["selector"]), list(dom.get("css_levers") or [])[:3], documents
        )
        if candidates:
            hint["source_candidates"] = candidates[:MAX_SOURCE_CANDIDATES]
            mapped += 1
    if documents:
        report["source_mapping"] = {
            "mapped_hotspots": mapped,
            "scanned_files": len(documents),
            "truncated": bool(truncated),
        }


__all__ = [
    "attach_dom_hints",
    "attach_repair_hints",
    "attach_source_candidates",
]

"""Bounded structural comparison for dynamic visual surfaces.

Canvas, complex SVG, maps, and charts should not pollute static pixel metrics.
This module compares those regions as separate, non-compensating vectors and
never writes an artifact.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat


ROUND_DIGITS = 4
MAX_DYNAMIC_REGIONS = 3
GRID_SIDE = 6


def _rounded(value: float) -> float:
    return round(float(value), ROUND_DIGITS)


def _fit_size(size: tuple[int, int], maximum: int) -> tuple[int, int]:
    width, height = size
    scale = min(1.0, maximum / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def normalize_regions(
    regions: list[dict[str, Any]] | None,
    size: tuple[int, int],
    maximum: int = MAX_DYNAMIC_REGIONS,
) -> list[dict[str, Any]]:
    """Validate, clip, de-duplicate, and bound browser-provided regions."""

    if not regions:
        return []
    if not isinstance(regions, list):
        raise ValueError("dynamic regions must be a list")
    width, height = size
    normalized: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, region in enumerate(regions):
        if not isinstance(region, dict):
            raise ValueError("each dynamic region must be an object")
        bounds = region.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError("dynamic region bounds must contain x, y, width, height")
        try:
            x, y, region_width, region_height = (float(value) for value in bounds)
        except (TypeError, ValueError) as error:
            raise ValueError("dynamic region bounds must be numeric") from error
        if not all(math.isfinite(value) for value in (x, y, region_width, region_height)):
            raise ValueError("dynamic region bounds must be finite")
        if region_width <= 0 or region_height <= 0:
            continue
        left = max(0, math.floor(x))
        top = max(0, math.floor(y))
        right = min(width, math.ceil(x + region_width))
        bottom = min(height, math.ceil(y + region_height))
        if right <= left or bottom <= top:
            continue
        identifier = str(region.get("id") or f"dynamic:{index}")[:180]
        if identifier in identifiers:
            continue
        identifiers.add(identifier)
        normalized.append(
            {
                "id": identifier,
                "source": str(region.get("source") or "auto")[:24],
                "kind": str(region.get("kind") or "dynamic")[:24],
                "selector": str(region.get("selector") or "")[:160],
                "bounds": [left, top, right - left, bottom - top],
            }
        )
        if len(normalized) >= maximum:
            break
    return normalized


def build_static_mask(size: tuple[int, int], regions: list[dict[str, Any]]) -> Image.Image:
    """Return a mask where static pixels are 255 and dynamic pixels are 0."""

    mask = Image.new("L", size, 255)
    draw = ImageDraw.Draw(mask)
    for region in regions:
        left, top, width, height = region["bounds"]
        draw.rectangle((left, top, left + width - 1, top + height - 1), fill=0)
    return mask


def _normalized_layout(reference: Image.Image, actual: Image.Image) -> float:
    scores: list[tuple[float, float]] = []
    for maximum, weight in ((8, 0.2), (16, 0.3), (32, 0.5)):
        size = _fit_size(reference.size, maximum)
        reference_values = list(reference.convert("L").resize(size, Image.Resampling.BOX).tobytes())
        actual_values = list(actual.convert("L").resize(size, Image.Resampling.BOX).tobytes())

        def normalize(values: list[int]) -> list[int]:
            ordered = sorted(values)
            low = ordered[int((len(ordered) - 1) * 0.05)]
            high = ordered[int((len(ordered) - 1) * 0.95)]
            if high - low < 2:
                return [128] * len(values)
            factor = 255 / (high - low)
            return [max(0, min(255, round((value - low) * factor))) for value in values]

        normalized_reference = normalize(reference_values)
        normalized_actual = normalize(actual_values)
        error = sum(
            abs(left - right)
            for left, right in zip(normalized_reference, normalized_actual)
        ) / max(1, len(normalized_reference))
        scores.append((max(0.0, 1.0 - error / 255), weight))
    return 100 * sum(score * weight for score, weight in scores)


def _edge_mask(image: Image.Image, maximum: int = 256) -> Image.Image:
    size = _fit_size(image.size, maximum)
    edges = (
        image.convert("L")
        .resize(size, Image.Resampling.BOX)
        .filter(ImageFilter.GaussianBlur(0.8))
        .filter(ImageFilter.FIND_EDGES)
    )
    return edges.point(lambda value: 255 if value >= 20 else 0)


def _density_grid(mask: Image.Image, side: int = GRID_SIDE) -> list[float]:
    width, height = mask.size
    values: list[float] = []
    for row in range(min(side, height)):
        top = row * height // min(side, height)
        bottom = (row + 1) * height // min(side, height)
        for column in range(min(side, width)):
            left = column * width // min(side, width)
            right = (column + 1) * width // min(side, width)
            crop = mask.crop((left, top, right, bottom))
            values.append(sum(value > 0 for value in crop.tobytes()) / (crop.width * crop.height))
    return values


def _distribution_overlap(reference: list[float], actual: list[float]) -> float:
    shared = sum(min(left, right) for left, right in zip(reference, actual))
    combined = sum(max(left, right) for left, right in zip(reference, actual))
    return 100.0 if combined == 0 else shared / combined * 100


def _edge_distribution(reference: Image.Image, actual: Image.Image) -> float:
    return _distribution_overlap(
        _density_grid(_edge_mask(reference)),
        _density_grid(_edge_mask(actual)),
    )


def _distribution_similarity(reference: list[int], actual: list[int]) -> float:
    total = sum(reference)
    if total == 0 or total != sum(actual) or len(reference) != len(actual):
        return 0.0
    cumulative = 0
    transport = 0
    for left, right in zip(reference[:-1], actual[:-1]):
        cumulative += left - right
        transport += abs(cumulative)
    return max(0.0, 1.0 - transport / (total * max(1, len(reference) - 1)))


def _opponent_histograms(image: Image.Image) -> tuple[list[int], list[int]]:
    red_green = [0] * 511
    blue_yellow = [0] * 511
    values = image.tobytes()
    for offset in range(0, len(values), 3):
        red, green, blue = values[offset : offset + 3]
        red_green[red - green + 255] += 1
        blue_yellow[blue - ((red + green) // 2) + 255] += 1
    return red_green, blue_yellow


def _color_similarity(reference: Image.Image, actual: Image.Image) -> float:
    size = _fit_size(reference.size, 256)
    reference = reference.resize(size, Image.Resampling.BOX)
    actual = actual.resize(size, Image.Resampling.BOX)
    reference_rgb = reference.histogram()
    actual_rgb = actual.histogram()
    channels = sum(
        _distribution_similarity(
            reference_rgb[index * 256 : (index + 1) * 256],
            actual_rgb[index * 256 : (index + 1) * 256],
        )
        for index in range(3)
    ) / 3
    luminance = _distribution_similarity(
        reference.convert("L").histogram(), actual.convert("L").histogram()
    )
    opponents = sum(
        _distribution_similarity(left, right)
        for left, right in zip(_opponent_histograms(reference), _opponent_histograms(actual))
    ) / 2
    return 100 * (0.6 * channels + 0.2 * luminance + 0.2 * opponents)


def _temporal_metrics(first: Image.Image, second: Image.Image) -> dict[str, Any]:
    raw_difference = ImageChops.difference(first, second)
    difference = ImageStat.Stat(raw_difference)
    red, green, blue = raw_difference.split()
    magnitude = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = magnitude.histogram()
    pixels = first.width * first.height
    changed = pixels - sum(histogram[:9])
    changed_pct = changed / pixels * 100
    structure_drift = 100 - _normalized_layout(first, second)
    edge_drift = 100 - _edge_distribution(first, second)
    color_drift = 100 - _color_similarity(first, second)
    return {
        "state": "active" if changed >= 32 and changed_pct >= 0.1 else "stable",
        "changed_pct": _rounded(changed_pct),
        "mae": _rounded(sum(difference.mean) / 3),
        "structure_drift_pct": _rounded(max(0.0, structure_drift)),
        "edge_drift_pct": _rounded(max(0.0, edge_drift)),
        "color_drift_pct": _rounded(max(0.0, color_drift)),
    }


def _bounds_iou(left: list[int], right: list[int]) -> float:
    left_x, left_y, left_width, left_height = left
    right_x, right_y, right_width, right_height = right
    overlap_width = max(
        0, min(left_x + left_width, right_x + right_width) - max(left_x, right_x)
    )
    overlap_height = max(
        0, min(left_y + left_height, right_y + right_height) - max(left_y, right_y)
    )
    overlap = overlap_width * overlap_height
    combined = left_width * left_height + right_width * right_height - overlap
    return 0.0 if combined <= 0 else overlap / combined


def _matched_regions(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    """Match exact IDs first, then conservatively recover a changed selector."""

    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    remaining_candidate = list(candidate)
    remaining_baseline: list[dict[str, Any]] = []
    for before in baseline:
        exact = next(
            (item for item in remaining_candidate if item.get("id") == before.get("id")),
            None,
        )
        if exact is None:
            remaining_baseline.append(before)
            continue
        remaining_candidate.remove(exact)
        pairs.append((before, exact))

    for before in remaining_baseline:
        compatible = [
            (_bounds_iou(before["bounds"], after["bounds"]), after)
            for after in remaining_candidate
            if before.get("kind") == after.get("kind")
        ]
        overlap, after = max(compatible, default=(0.0, None), key=lambda item: item[0])
        if after is not None and overlap >= 0.5:
            remaining_candidate.remove(after)
            pairs.append((before, after))
        else:
            pairs.append((before, None))
    pairs.extend((None, after) for after in remaining_candidate)
    return pairs[:MAX_DYNAMIC_REGIONS]


def analyze_regions(
    reference: Image.Image,
    actual: Image.Image,
    regions: list[dict[str, Any]],
    temporal: Image.Image | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for region in regions:
        left, top, width, height = region["bounds"]
        box = (left, top, left + width, top + height)
        reference_crop = reference.crop(box)
        actual_crop = actual.crop(box)
        result = {
            **region,
            "fidelity": {
                "coarse_structure_pct": _rounded(
                    _normalized_layout(reference_crop, actual_crop)
                ),
                "edge_distribution_pct": _rounded(
                    _edge_distribution(reference_crop, actual_crop)
                ),
                "color_similarity_pct": _rounded(
                    _color_similarity(reference_crop, actual_crop)
                ),
            },
            "temporal": {"state": "unavailable"},
        }
        if temporal is not None and temporal.size == actual.size:
            result["temporal"] = _temporal_metrics(actual_crop, temporal.crop(box))
        results.append(result)
    return results


def compare_region_reports(
    baseline: list[dict[str, Any]] | None,
    candidate: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    regions: list[dict[str, Any]] = []
    for before, after in _matched_regions(
        list(baseline or [])[:MAX_DYNAMIC_REGIONS],
        list(candidate or [])[:MAX_DYNAMIC_REGIONS],
    ):
        if before is None:
            regions.append(
                {
                    "id": after.get("id"),
                    "kind": after.get("kind"),
                    "bounds": after.get("bounds"),
                    "status": "review",
                    "reason": "new dynamic region",
                }
            )
            continue
        if after is None:
            regions.append(
                {
                    "id": before.get("id"),
                    "kind": before.get("kind"),
                    "bounds": before.get("bounds"),
                    "status": "regressed",
                    "reason": "dynamic region disappeared",
                }
            )
            continue

        gains: dict[str, float] = {}
        metric_states: list[str] = []
        for metric, drift_key in (
            ("coarse_structure_pct", "structure_drift_pct"),
            ("edge_distribution_pct", "edge_drift_pct"),
            ("color_similarity_pct", "color_drift_pct"),
        ):
            gain = float(after["fidelity"][metric]) - float(before["fidelity"][metric])
            gains[metric] = _rounded(gain)
            noise = max(
                0.5,
                float((before.get("temporal") or {}).get(drift_key, 0)),
                float((after.get("temporal") or {}).get(drift_key, 0)),
            )
            if noise > 5:
                metric_states.append("indeterminate")
            elif gain > noise:
                metric_states.append("improved")
            elif gain < -noise:
                metric_states.append("regressed")
            else:
                metric_states.append("unchanged")

        decisive = {state for state in metric_states if state != "unchanged"}
        if "improved" in decisive and "regressed" in decisive:
            status = "mixed"
        elif "regressed" in decisive:
            status = "regressed"
        elif "indeterminate" in decisive:
            status = "indeterminate"
        elif "improved" in decisive:
            status = "improved"
        else:
            status = "unchanged"
        before_bounds = before.get("bounds") or [0, 0, 0, 0]
        after_bounds = after.get("bounds") or [0, 0, 0, 0]
        geometry = {
            "before": before_bounds,
            "after": after_bounds,
            "iou": _rounded(_bounds_iou(before_bounds, after_bounds)),
            "dx": _rounded(float(after_bounds[0]) - float(before_bounds[0])),
            "dy": _rounded(float(after_bounds[1]) - float(before_bounds[1])),
            "dw": _rounded(float(after_bounds[2]) - float(before_bounds[2])),
            "dh": _rounded(float(after_bounds[3]) - float(before_bounds[3])),
        }
        regions.append(
            {
                "id": after.get("id"),
                "baseline_id": before.get("id"),
                "kind": after.get("kind"),
                "bounds": after.get("bounds"),
                "status": status,
                "gains": gains,
                "geometry": geometry,
            }
        )

    counts = {
        status: sum(item["status"] == status for item in regions)
        for status in ("improved", "regressed", "mixed", "unchanged", "indeterminate", "review")
    }
    return {"regions": regions, "summary": counts}


__all__ = [
    "analyze_regions",
    "build_static_mask",
    "compare_region_reports",
    "normalize_regions",
]

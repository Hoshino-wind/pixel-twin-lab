"""Lightweight, explainable visual comparison for Pixel Twin.

The module intentionally depends only on Pillow and the Python standard library.
It keeps the public surface small: analyze one image pair, then optionally compare
two analysis reports to measure improvement and local regressions.
"""

from __future__ import annotations

import math
import warnings
from collections import deque
from os import PathLike
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

try:
    from dynamic_compare import (
        analyze_regions as analyze_dynamic_regions,
        build_static_mask,
        compare_region_reports as compare_dynamic_region_reports,
        normalize_regions as normalize_dynamic_regions,
    )
except ImportError:
    from scripts.dynamic_compare import (
        analyze_regions as analyze_dynamic_regions,
        build_static_mask,
        compare_region_reports as compare_dynamic_region_reports,
        normalize_regions as normalize_dynamic_regions,
    )

try:
    from repair_hints import MAX_CANDIDATE_HINTS, attach_repair_hints
except ImportError:
    from scripts.repair_hints import MAX_CANDIDATE_HINTS, attach_repair_hints


PathValue = str | PathLike[str]
GRID_SIDE = 4
ROUND_DIGITS = 4
MAX_IMAGE_SIDE = 10_000
MAX_IMAGE_PIXELS = 9_000_000
ANALYSIS_VERSION = 2


def _rounded(value: float) -> float:
    return round(float(value), ROUND_DIGITS)


def _validate_options(tolerance: int, max_hotspots: int) -> None:
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 255:
        raise ValueError("tolerance must be an integer between 0 and 255")
    if isinstance(max_hotspots, bool) or not isinstance(max_hotspots, int) or max_hotspots < 0:
        raise ValueError("max_hotspots must be a non-negative integer")


def _open_rgb(path: PathValue) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as source:
                width, height = source.size
                if width <= 0 or height <= 0:
                    raise ValueError("image dimensions must be positive")
                if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
                    raise ValueError(
                        f"image must be at most {MAX_IMAGE_SIDE} pixels per side"
                    )
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError(
                        f"image must not exceed {MAX_IMAGE_PIXELS} total pixels"
                    )
                return ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError(f"unable to read image safely: {error}") from error


def _fit_size(size: tuple[int, int], maximum: int) -> tuple[int, int]:
    width, height = size
    scale = min(1.0, maximum / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _normalized_samples(
    image: Image.Image,
    maximum: int,
    static_mask: Image.Image | None = None,
) -> tuple[list[int], bytes]:
    size = _fit_size(image.size, maximum)
    resized = image.convert("L").resize(size, Image.Resampling.BOX)
    values = list(resized.tobytes())
    active = (
        static_mask.resize(size, Image.Resampling.NEAREST).tobytes()
        if static_mask is not None
        else bytes([255]) * len(values)
    )
    ordered = sorted(value for index, value in enumerate(values) if active[index] >= 128)
    if not ordered:
        return [128] * len(values), active
    low = ordered[int((len(ordered) - 1) * 0.05)]
    high = ordered[int((len(ordered) - 1) * 0.95)]
    if high - low < 2:
        return [128] * len(values), active
    factor = 255 / (high - low)
    return [max(0, min(255, round((value - low) * factor))) for value in values], active


def _layout_match(
    reference: Image.Image,
    actual: Image.Image,
    static_mask: Image.Image | None = None,
) -> float:
    """Compare coarse normalized luminance at multiple spatial scales.

    Per-image normalization makes this primarily a placement/geometry signal;
    global palette changes are handled independently by color similarity.
    """

    scores: list[tuple[float, float]] = []
    for maximum, weight in ((8, 0.2), (16, 0.3), (32, 0.5)):
        reference_values, active = _normalized_samples(reference, maximum, static_mask)
        actual_values, _ = _normalized_samples(actual, maximum, static_mask)
        differences = [
            abs(left - right)
            for index, (left, right) in enumerate(zip(reference_values, actual_values))
            if active[index] >= 128
        ]
        mean_error = sum(differences) / max(1, len(differences))
        scores.append((max(0.0, 1.0 - mean_error / 255), weight))
    return 100 * sum(score * weight for score, weight in scores)


def _tile_ssim(
    reference: Image.Image,
    actual: Image.Image,
    tile_size: int = 8,
    static_mask: Image.Image | None = None,
) -> float:
    width, height = reference.size
    reference_values = reference.tobytes()
    actual_values = actual.tobytes()
    mask_values = static_mask.tobytes() if static_mask is not None else None
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    scores: list[float] = []

    for top in range(0, height, tile_size):
        bottom = min(height, top + tile_size)
        for left in range(0, width, tile_size):
            right = min(width, left + tile_size)
            ref_tile: list[int] = []
            actual_tile: list[int] = []
            tile_pixels = (right - left) * (bottom - top)
            for row in range(top, bottom):
                start = row * width + left
                end = row * width + right
                if mask_values is None:
                    ref_tile.extend(reference_values[start:end])
                    actual_tile.extend(actual_values[start:end])
                else:
                    for index in range(start, end):
                        if mask_values[index] >= 128:
                            ref_tile.append(reference_values[index])
                            actual_tile.append(actual_values[index])

            count = len(ref_tile)
            if count < max(1, math.ceil(tile_pixels * 0.8)):
                continue
            ref_mean = sum(ref_tile) / count
            actual_mean = sum(actual_tile) / count
            ref_variance = sum((value - ref_mean) ** 2 for value in ref_tile) / count
            actual_variance = sum((value - actual_mean) ** 2 for value in actual_tile) / count
            covariance = sum(
                (left_value - ref_mean) * (right_value - actual_mean)
                for left_value, right_value in zip(ref_tile, actual_tile)
            ) / count
            numerator = (2 * ref_mean * actual_mean + c1) * (2 * covariance + c2)
            denominator = (ref_mean**2 + actual_mean**2 + c1) * (
                ref_variance + actual_variance + c2
            )
            scores.append(max(0.0, min(1.0, numerator / denominator)))

    return 1.0 if not scores else sum(scores) / len(scores)


def _structure_similarity(
    reference: Image.Image,
    actual: Image.Image,
    static_mask: Image.Image | None = None,
) -> float:
    base_size = _fit_size(reference.size, 256)
    reference_gray = reference.convert("L").resize(base_size, Image.Resampling.BOX)
    actual_gray = actual.convert("L").resize(base_size, Image.Resampling.BOX)
    base_mask = (
        static_mask.resize(base_size, Image.Resampling.NEAREST)
        if static_mask is not None
        else None
    )
    weighted_scores: list[tuple[float, float]] = []
    for divisor, weight in ((1, 0.5), (2, 0.3), (4, 0.2)):
        level_size = (
            max(1, base_size[0] // divisor),
            max(1, base_size[1] // divisor),
        )
        ref_level = reference_gray.resize(level_size, Image.Resampling.BOX)
        actual_level = actual_gray.resize(level_size, Image.Resampling.BOX)
        level_mask = (
            base_mask.resize(level_size, Image.Resampling.NEAREST)
            if base_mask is not None
            else None
        )
        weighted_scores.append((_tile_ssim(ref_level, actual_level, static_mask=level_mask), weight))
    return 100 * sum(score * weight for score, weight in weighted_scores)


def _edge_mask(image: Image.Image, size: tuple[int, int]) -> bytearray:
    grayscale = image.convert("L").resize(size, Image.Resampling.BOX)
    edges = grayscale.filter(ImageFilter.GaussianBlur(0.8)).filter(ImageFilter.FIND_EDGES)
    width, height = size
    values = edges.tobytes()
    mask = bytearray(len(values))
    for y in range(1, height - 1):
        row = y * width
        for x in range(1, width - 1):
            index = row + x
            if values[index] >= 20:
                mask[index] = 255
    return mask


def _dilate(mask: bytearray, size: tuple[int, int], kernel: int = 3) -> bytes:
    if min(size) < kernel:
        return bytes(mask)
    return Image.frombytes("L", size, bytes(mask)).filter(ImageFilter.MaxFilter(kernel)).tobytes()


def _edge_match(
    reference: Image.Image,
    actual: Image.Image,
    static_mask: Image.Image | None = None,
) -> float:
    size = _fit_size(reference.size, 512)
    reference_mask = _edge_mask(reference, size)
    actual_mask = _edge_mask(actual, size)
    active = (
        static_mask.resize(size, Image.Resampling.NEAREST).tobytes()
        if static_mask is not None
        else None
    )
    if active is not None:
        for index, value in enumerate(active):
            if value < 128:
                reference_mask[index] = 0
                actual_mask[index] = 0
    reference_count = sum(value > 0 for value in reference_mask)
    actual_count = sum(value > 0 for value in actual_mask)
    if reference_count == 0 and actual_count == 0:
        return 100.0
    if reference_count == 0 or actual_count == 0:
        return 0.0

    reference_near = _dilate(reference_mask, size)
    actual_near = _dilate(actual_mask, size)
    reference_recall = sum(
        value > 0 and actual_near[index] > 0 for index, value in enumerate(reference_mask)
    ) / reference_count
    actual_recall = sum(
        value > 0 and reference_near[index] > 0 for index, value in enumerate(actual_mask)
    ) / actual_count
    return 50 * (reference_recall + actual_recall)


def _distribution_similarity(reference: list[int], actual: list[int]) -> float:
    """Return a continuous 1-D Earth-Mover similarity for equal-mass histograms."""

    total = sum(reference)
    if total == 0 or total != sum(actual) or len(reference) != len(actual):
        return 0.0
    cumulative = 0
    transport = 0
    for reference_count, actual_count in zip(reference[:-1], actual[:-1]):
        cumulative += reference_count - actual_count
        transport += abs(cumulative)
    maximum = total * max(1, len(reference) - 1)
    return max(0.0, 1.0 - transport / maximum)


def _opponent_histograms(
    image: Image.Image,
    static_mask: Image.Image | None = None,
) -> tuple[list[int], list[int]]:
    red_green = [0] * 511
    blue_yellow = [0] * 511
    values = image.tobytes()
    active = static_mask.tobytes() if static_mask is not None else None
    for offset in range(0, len(values), 3):
        if active is not None and active[offset // 3] < 128:
            continue
        red, green, blue = values[offset : offset + 3]
        red_green[red - green + 255] += 1
        blue_yellow[blue - ((red + green) // 2) + 255] += 1
    return red_green, blue_yellow


def _color_similarity(
    reference: Image.Image,
    actual: Image.Image,
    static_mask: Image.Image | None = None,
) -> float:
    """Compare palette distributions without hard quantization boundaries."""

    sample_size = _fit_size(reference.size, 512)
    if reference.size != sample_size:
        reference = reference.resize(sample_size, Image.Resampling.BOX)
        actual = actual.resize(sample_size, Image.Resampling.BOX)
        if static_mask is not None:
            static_mask = static_mask.resize(sample_size, Image.Resampling.NEAREST)

    reference_rgb = reference.histogram(mask=static_mask)
    actual_rgb = actual.histogram(mask=static_mask)
    channel_similarity = sum(
        _distribution_similarity(
            reference_rgb[channel * 256 : (channel + 1) * 256],
            actual_rgb[channel * 256 : (channel + 1) * 256],
        )
        for channel in range(3)
    ) / 3
    luminance_similarity = _distribution_similarity(
        reference.convert("L").histogram(mask=static_mask),
        actual.convert("L").histogram(mask=static_mask),
    )
    reference_opponents = _opponent_histograms(reference, static_mask)
    actual_opponents = _opponent_histograms(actual, static_mask)
    opponent_similarity = sum(
        _distribution_similarity(left, right)
        for left, right in zip(reference_opponents, actual_opponents)
    ) / 2
    return 100 * (
        0.6 * channel_similarity + 0.2 * luminance_similarity + 0.2 * opponent_similarity
    )


def _difference_stats(
    diff: Image.Image,
    magnitude: Image.Image,
    tolerance: int,
    static_mask: Image.Image | None = None,
) -> dict[str, float | int]:
    histogram = magnitude.histogram(mask=static_mask)
    pixels = sum(histogram)
    if pixels == 0:
        return {
            "strict_match_pct": 100.0,
            "tolerant_match_pct": 100.0,
            "mae": 0.0,
            "max_delta": 0,
        }
    strict_differences = pixels - histogram[0]
    tolerant_differences = pixels - sum(histogram[: tolerance + 1])
    return {
        "strict_match_pct": _rounded((pixels - strict_differences) / pixels * 100),
        "tolerant_match_pct": _rounded((pixels - tolerant_differences) / pixels * 100),
        "mae": _rounded(sum(ImageStat.Stat(diff, mask=static_mask).mean) / 3),
        "max_delta": max(index for index, count in enumerate(histogram) if count),
    }


def _grid_report(
    diff: Image.Image,
    magnitude: Image.Image,
    tolerance: int,
    static_mask: Image.Image | None = None,
) -> dict[str, Any]:
    width, height = diff.size
    columns = min(GRID_SIDE, width)
    rows = min(GRID_SIDE, height)
    cells: list[dict[str, Any]] = []
    for row in range(rows):
        top = row * height // rows
        bottom = (row + 1) * height // rows
        for column in range(columns):
            left = column * width // columns
            right = (column + 1) * width // columns
            box = (left, top, right, bottom)
            cell_mask = static_mask.crop(box) if static_mask is not None else None
            static_pixels = (
                sum(value >= 128 for value in cell_mask.tobytes())
                if cell_mask is not None
                else (right - left) * (bottom - top)
            )
            cell_pixels = (right - left) * (bottom - top)
            static_coverage = static_pixels / cell_pixels * 100
            stats = _difference_stats(
                diff.crop(box), magnitude.crop(box), tolerance, cell_mask
            )
            cells.append(
                {
                    "id": f"r{row}c{column}",
                    "row": row,
                    "column": column,
                    "bounds": [left, top, right - left, bottom - top],
                    "static_coverage_pct": _rounded(static_coverage),
                    "dynamic_excluded": static_coverage < 20,
                    **stats,
                }
            )
    return {"rows": rows, "columns": columns, "cells": cells}


def _component_boxes(mask: bytes, size: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    width, height = size
    visited = bytearray(len(mask))
    boxes: list[tuple[int, int, int, int]] = []
    for index, value in enumerate(mask):
        if value == 0 or visited[index]:
            continue
        visited[index] = 1
        queue: deque[int] = deque([index])
        min_x = max_x = index % width
        min_y = max_y = index // width
        while queue:
            current = queue.popleft()
            x = current % width
            y = current // width
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for next_y in range(max(0, y - 1), min(height, y + 2)):
                for next_x in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = next_y * width + next_x
                    if mask[neighbor] and not visited[neighbor]:
                        visited[neighbor] = 1
                        queue.append(neighbor)
        boxes.append((min_x, min_y, max_x + 1, max_y + 1))
    return boxes


def _hex_color(values: list[float]) -> str:
    red, green, blue = (max(0, min(255, round(value))) for value in values[:3])
    return f"#{red:02x}{green:02x}{blue:02x}"


def _hotspots(
    reference: Image.Image,
    actual: Image.Image,
    magnitude: Image.Image,
    tolerance: int,
    maximum: int,
) -> list[dict[str, Any]]:
    if maximum == 0:
        return []
    width, height = magnitude.size
    analysis_size = _fit_size(magnitude.size, 384)
    binary = magnitude.point(lambda value: 255 if value > tolerance else 0)
    if binary.size != analysis_size:
        binary = binary.resize(analysis_size, Image.Resampling.BOX).point(
            lambda value: 255 if value else 0
        )
    kernel = 5 if min(analysis_size) >= 5 else 3
    if min(analysis_size) >= kernel:
        binary = binary.filter(ImageFilter.MaxFilter(kernel))

    candidates: list[dict[str, Any]] = []
    analysis_width, analysis_height = analysis_size
    for left, top, right, bottom in _component_boxes(binary.tobytes(), analysis_size):
        original_left = math.floor(left * width / analysis_width)
        original_top = math.floor(top * height / analysis_height)
        original_right = min(width, math.ceil(right * width / analysis_width))
        original_bottom = min(height, math.ceil(bottom * height / analysis_height))
        crop = magnitude.crop((original_left, original_top, original_right, original_bottom))
        histogram = crop.histogram()
        changed_pixels = sum(histogram[tolerance + 1 :])
        if changed_pixels == 0:
            continue
        weighted_delta = sum(delta * count for delta, count in enumerate(histogram) if delta > tolerance)
        mean_delta = weighted_delta / changed_pixels
        max_delta = max(delta for delta, count in enumerate(histogram) if count and delta > tolerance)
        region_pixels = crop.width * crop.height
        candidate = {
                "bounds": [original_left, original_top, crop.width, crop.height],
                "x": original_left,
                "y": original_top,
                "width": crop.width,
                "height": crop.height,
                "changed_pixels": changed_pixels,
                "changed_pct": _rounded(changed_pixels / region_pixels * 100),
                "page_changed_pct": _rounded(changed_pixels / (width * height) * 100),
                "mean_delta": _rounded(mean_delta),
                "max_delta": max_delta,
                "score": _rounded(weighted_delta / 255),
            }
        box = (original_left, original_top, original_right, original_bottom)
        reference_color = ImageStat.Stat(reference.crop(box)).median
        actual_color = ImageStat.Stat(actual.crop(box)).median
        if max(abs(left - right) for left, right in zip(reference_color, actual_color)) > tolerance:
            candidate["dominant_color_hint"] = {
                "actual": _hex_color(actual_color),
                "reference": _hex_color(reference_color),
            }
        candidates.append(candidate)

    candidates.sort(key=lambda item: (item["score"], item["changed_pixels"]), reverse=True)
    selected = candidates[:maximum]
    for rank, hotspot in enumerate(selected, start=1):
        hotspot["rank"] = rank
    return selected


def analyze_images(
    reference_path: PathValue,
    actual_path: PathValue,
    tolerance: int = 8,
    max_hotspots: int = 3,
    dynamic_regions: list[dict[str, Any]] | None = None,
    temporal_path: PathValue | None = None,
    dom_index: dict[str, Any] | None = None,
    repair_exclusions: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], Image.Image | None]:
    """Analyze a reference/actual image pair without writing any artifacts.

    ``pass`` means the pair was valid and comparable; it is deliberately not an
    acceptance threshold. Callers can apply their own policy to the metrics.
    """

    _validate_options(tolerance, max_hotspots)
    reference = _open_rgb(reference_path)
    actual = _open_rgb(actual_path)
    if reference.size != actual.size:
        return {
            "pass": False,
            "reason": "size mismatch",
            "reference_size": list(reference.size),
            "actual_size": list(actual.size),
            "tolerance": tolerance,
        }, None

    regions = normalize_dynamic_regions(dynamic_regions, reference.size)
    static_mask = build_static_mask(reference.size, regions) if regions else None
    static_actual = actual.copy()
    for region in regions:
        left, top, width, height = region["bounds"]
        box = (left, top, left + width, top + height)
        static_actual.paste(reference.crop(box), box)

    temporal = None
    if temporal_path is not None:
        temporal = _open_rgb(temporal_path)
        if temporal.size != actual.size:
            temporal = None

    diff = ImageChops.difference(reference, static_actual)
    red, green, blue = diff.split()
    magnitude = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    hotspot_limit = (
        max(max_hotspots, MAX_CANDIDATE_HINTS)
        if dom_index is not None and max_hotspots > 0
        else max_hotspots
    )
    static_pixels = (
        sum(value >= 128 for value in static_mask.tobytes())
        if static_mask is not None
        else reference.width * reference.height
    )
    report: dict[str, Any] = {
        "pass": True,
        "_analysis_version": ANALYSIS_VERSION,
        **_difference_stats(diff, magnitude, tolerance, static_mask),
        "size": [reference.width, reference.height],
        "tolerance": tolerance,
        "static_coverage_pct": _rounded(
            static_pixels / (reference.width * reference.height) * 100
        ),
        "layout_match_pct": _rounded(_layout_match(reference, static_actual, static_mask)),
        "structure_similarity_pct": _rounded(
            _structure_similarity(reference, static_actual, static_mask)
        ),
        "edge_match_pct": _rounded(_edge_match(reference, static_actual, static_mask)),
        "color_similarity_pct": _rounded(
            _color_similarity(reference, static_actual, static_mask)
        ),
        "hotspots": _hotspots(
            reference, static_actual, magnitude, tolerance, hotspot_limit
        ),
        "_grid": _grid_report(diff, magnitude, tolerance, static_mask),
    }
    if regions:
        report["dynamic_region_count"] = len(regions)
        report["dynamic_regions"] = analyze_dynamic_regions(
            reference, actual, regions, temporal
        )
    if dom_index is not None:
        attach_repair_hints(
            report,
            dom_index,
            reference,
            actual,
            dynamic_regions=regions,
            repair_exclusions=repair_exclusions,
            output_limit=max_hotspots,
        )
    return report, diff


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare reports as a fidelity vector plus direct 4x4 regressions."""

    if not baseline.get("pass", True) or not candidate.get("pass", True):
        return {"pass": False, "reason": "cannot compare an invalid image report"}
    baseline_grid = baseline.get("_grid")
    candidate_grid = candidate.get("_grid")
    if not isinstance(baseline_grid, dict) or not isinstance(candidate_grid, dict):
        return {"pass": False, "reason": "analysis report is missing its regional grid"}
    if baseline.get("size") != candidate.get("size"):
        return {"pass": False, "reason": "report size mismatch"}
    if baseline.get("_analysis_version") != candidate.get("_analysis_version"):
        return {"pass": False, "reason": "analysis version mismatch"}
    if (
        baseline_grid.get("rows"),
        baseline_grid.get("columns"),
    ) != (
        candidate_grid.get("rows"),
        candidate_grid.get("columns"),
    ):
        return {"pass": False, "reason": "regional grid mismatch"}

    baseline_cells = {cell["id"]: cell for cell in baseline_grid.get("cells", [])}
    candidate_cells = {cell["id"]: cell for cell in candidate_grid.get("cells", [])}
    if baseline_cells.keys() != candidate_cells.keys():
        return {"pass": False, "reason": "regional grid cells mismatch"}

    def static_basis(report: dict[str, Any]) -> tuple[float, tuple[tuple[int, ...], ...]]:
        bounds = tuple(
            sorted(
                tuple(int(value) for value in item.get("bounds", []))
                for item in (report.get("dynamic_regions") or [])
                if isinstance(item, dict)
                and isinstance(item.get("bounds"), list)
                and len(item["bounds"]) == 4
            )
        )
        return round(float(report.get("static_coverage_pct", 100)), 4), bounds

    static_basis_changed = static_basis(baseline) != static_basis(candidate)

    regions: list[dict[str, Any]] = []
    for cell_id in baseline_cells:
        before = baseline_cells[cell_id]
        after = candidate_cells[cell_id]
        if static_basis_changed:
            regions.append(
                {
                    "id": cell_id,
                    "row": before["row"],
                    "column": before["column"],
                    "bounds": before["bounds"],
                    "status": "indeterminate",
                    "reason": "dynamic exclusion basis changed",
                }
            )
            continue
        if before.get("dynamic_excluded") or after.get("dynamic_excluded"):
            regions.append(
                {
                    "id": cell_id,
                    "row": before["row"],
                    "column": before["column"],
                    "bounds": before["bounds"],
                    "status": "indeterminate",
                    "reason": "insufficient static coverage",
                }
            )
            continue
        tolerant_gain = float(after["tolerant_match_pct"]) - float(before["tolerant_match_pct"])
        strict_gain = float(after["strict_match_pct"]) - float(before["strict_match_pct"])
        mae_reduction = float(before["mae"]) - float(after["mae"])
        signals = (tolerant_gain, mae_reduction)
        has_gain = any(value > 0.01 for value in signals)
        has_loss = any(value < -0.01 for value in signals)
        if has_gain and has_loss:
            status = "mixed"
        elif has_gain:
            status = "improved"
        elif has_loss:
            status = "regressed"
        else:
            status = "unchanged"
        regions.append(
            {
                "id": cell_id,
                "row": before["row"],
                "column": before["column"],
                "bounds": before["bounds"],
                "status": status,
                "tolerant_match_gain_pct": _rounded(tolerant_gain),
                "strict_match_gain_pct": _rounded(strict_gain),
                "mae_reduction": _rounded(mae_reduction),
            }
        )

    improved = sorted(
        (region for region in regions if region["status"] == "improved"),
        key=lambda region: (region["tolerant_match_gain_pct"], region["mae_reduction"]),
        reverse=True,
    )
    regression_risks = sorted(
        (region for region in regions if region["status"] in {"regressed", "mixed"}),
        key=lambda region: (
            min(
                region["tolerant_match_gain_pct"],
                region["mae_reduction"] * 100 / 255,
            ),
            region["mae_reduction"],
        ),
    )
    mixed_count = sum(region["status"] == "mixed" for region in regions)
    indeterminate_count = sum(region["status"] == "indeterminate" for region in regions)
    overall: dict[str, float | None] = {
        "strict_match_gain_pct": _rounded(
            float(candidate["strict_match_pct"]) - float(baseline["strict_match_pct"])
        ),
        "tolerant_match_gain_pct": _rounded(
            float(candidate["tolerant_match_pct"]) - float(baseline["tolerant_match_pct"])
        ),
        "mae_reduction": _rounded(float(baseline["mae"]) - float(candidate["mae"])),
        "max_delta_reduction": float(baseline["max_delta"]) - float(candidate["max_delta"]),
        "layout_gain_pct": _rounded(
            float(candidate["layout_match_pct"]) - float(baseline["layout_match_pct"])
        ),
        "structure_gain_pct": _rounded(
            float(candidate["structure_similarity_pct"])
            - float(baseline["structure_similarity_pct"])
        ),
        "edge_gain_pct": _rounded(
            float(candidate["edge_match_pct"]) - float(baseline["edge_match_pct"])
        ),
        "color_gain_pct": _rounded(
            float(candidate["color_similarity_pct"]) - float(baseline["color_similarity_pct"])
        ),
    }
    if static_basis_changed:
        overall = {key: None for key in overall}
        status = "indeterminate"
    else:
        vector = [value for value in overall.values() if value is not None]
        has_gain = any(value > 0.01 for value in vector)
        has_loss = any(value < -0.01 for value in vector)
        if has_gain and has_loss:
            status = "mixed"
        elif has_gain:
            status = "improved"
        elif has_loss:
            status = "regressed"
        else:
            status = "unchanged"
    result = {
        "pass": True,
        "status": status,
        "static_basis_changed": static_basis_changed,
        "overall": overall,
        "summary": {
            "improved_regions": len(improved),
            "regressed_regions": len(regression_risks),
            "mixed_regions": mixed_count,
            "unchanged_regions": sum(region["status"] == "unchanged" for region in regions),
            "indeterminate_regions": indeterminate_count,
        },
        "improved_regions": improved[:3],
        "regressed_regions": regression_risks[:3],
        "regions": regions,
    }
    baseline_dynamic = baseline.get("dynamic_regions")
    candidate_dynamic = candidate.get("dynamic_regions")
    if baseline_dynamic or candidate_dynamic:
        result["dynamic"] = compare_dynamic_region_reports(
            baseline_dynamic, candidate_dynamic
        )
    return result


__all__ = ["analyze_images", "compare_reports"]

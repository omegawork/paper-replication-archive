#!/usr/bin/env python3
"""Deterministic, typed comparison contracts for v4 Evidence Bundles."""

from __future__ import annotations

import math
import statistics
from typing import Any


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be finite")
    return converted


def _convert_units(value: Any, conversion: dict[str, Any]) -> Any:
    scale = _number(conversion.get("scale", 1.0), "unit conversion scale")
    offset = _number(conversion.get("offset", 0.0), "unit conversion offset")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) * scale + offset
    if isinstance(value, list):
        return [_convert_units(item, conversion) for item in value]
    if isinstance(value, dict):
        return {key: _convert_units(item, conversion) for key, item in value.items()}
    return value


def _convert_for_type(value: Any, comparison_type: str, conversion: dict[str, Any], acceptance: dict[str, Any]) -> Any:
    """Apply one declared linear conversion to measured values, never ids/seeds/x labels."""
    if comparison_type == "curve" and isinstance(value, dict):
        converted = dict(value)
        converted["y"] = _convert_units(value.get("y", []), conversion)
        return converted
    if comparison_type == "stochastic" and isinstance(value, list):
        return [
            {**item, "value": _convert_units(item.get("value"), conversion)}
            if isinstance(item, dict) else item
            for item in value
        ]
    if comparison_type == "table" and isinstance(value, dict):
        row_label_key = acceptance.get("row_label_key")
        rows = []
        for row in value.get("rows", []):
            if isinstance(row, dict):
                rows.append({key: item if key == row_label_key else _convert_units(item, conversion) for key, item in row.items()})
            else:
                rows.append(_convert_units(row, conversion))
        return {**value, "rows": rows}
    if comparison_type in {"string", "category", "set", "file", "visual", "manual"}:
        return value
    return _convert_units(value, conversion)


def _scalar_rules(actual: float, reference: float, acceptance: dict[str, Any]) -> dict[str, Any]:
    absolute_error = abs(actual - reference)
    denominator = max(abs(reference), float(acceptance.get("relative_epsilon", 1e-15)))
    relative_error = absolute_error / denominator
    rules = acceptance.get("rules")
    if rules is None:
        rules = []
        if "absolute_tolerance" in acceptance:
            rules.append({"metric": "absolute_error", "max": acceptance["absolute_tolerance"]})
        if "relative_tolerance" in acceptance:
            rules.append({"metric": "relative_error", "max": acceptance["relative_tolerance"]})
        if "rounding_decimals" in acceptance:
            rules.append({"metric": "rounding", "decimals": acceptance["rounding_decimals"]})
    if not isinstance(rules, list) or not rules:
        raise ValueError("scalar acceptance requires at least one rule")
    outcomes: list[dict[str, Any]] = []
    for rule in rules:
        metric = rule.get("metric")
        if metric == "absolute_error":
            threshold = _number(rule.get("max"), "absolute error maximum")
            passed = absolute_error <= threshold
            observed = absolute_error
        elif metric == "relative_error":
            threshold = _number(rule.get("max"), "relative error maximum")
            passed = relative_error <= threshold
            observed = relative_error
        elif metric == "rounding":
            decimals = int(rule.get("decimals"))
            threshold = decimals
            passed = round(actual, decimals) == round(reference, decimals)
            observed = {"actual": round(actual, decimals), "reference": round(reference, decimals)}
        else:
            raise ValueError(f"unsupported scalar acceptance metric: {metric}")
        outcomes.append({"metric": metric, "observed": observed, "threshold": threshold, "passed": passed})
    combine = acceptance.get("combine", "all")
    if combine not in {"all", "any"}:
        raise ValueError("acceptance combine must be all or any")
    passed = all(item["passed"] for item in outcomes) if combine == "all" else any(item["passed"] for item in outcomes)
    return {
        "passed": passed,
        "absolute_error": absolute_error,
        "relative_error": relative_error,
        "combine": combine,
        "rules": outcomes,
    }


def _flatten_numeric(value: Any, label: str) -> tuple[list[float], list[int]]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    if not value:
        return [], [0]
    if all(not isinstance(item, list) for item in value):
        return [_number(item, label) for item in value], [len(value)]
    if not all(isinstance(item, list) for item in value):
        raise ValueError(f"{label} has ragged nesting")
    rows = len(value)
    widths = {len(item) for item in value}
    if len(widths) != 1:
        raise ValueError(f"{label} is ragged")
    flat = [_number(cell, label) for row in value for cell in row]
    return flat, [rows, next(iter(widths), 0)]


def _numeric_array(actual: Any, reference: Any, acceptance: dict[str, Any]) -> dict[str, Any]:
    actual_flat, actual_shape = _flatten_numeric(actual, "actual array")
    reference_flat, reference_shape = _flatten_numeric(reference, "reference array")
    if actual_shape != reference_shape:
        return {"passed": False, "reason": "shape_mismatch", "actual_shape": actual_shape, "reference_shape": reference_shape}
    if acceptance.get("mode", "elementwise") == "norm":
        norm = acceptance.get("norm", "l2")
        differences = [left - right for left, right in zip(actual_flat, reference_flat)]
        if norm == "l1":
            observed = sum(abs(item) for item in differences)
        elif norm == "l2":
            observed = math.sqrt(sum(item * item for item in differences))
        elif norm == "linf":
            observed = max((abs(item) for item in differences), default=0.0)
        else:
            raise ValueError(f"unsupported norm: {norm}")
        maximum = _number(acceptance.get("max"), "norm maximum")
        return {"passed": observed <= maximum, "shape": actual_shape, "norm": norm, "observed": observed, "maximum": maximum}
    scalar_acceptance = acceptance.get("scalar", acceptance)
    cells = [_scalar_rules(left, right, scalar_acceptance) for left, right in zip(actual_flat, reference_flat)]
    combine = acceptance.get("element_combine", "all")
    passed = all(item["passed"] for item in cells) if combine == "all" else any(item["passed"] for item in cells)
    return {
        "passed": passed,
        "shape": actual_shape,
        "element_combine": combine,
        "max_absolute_error": max((item["absolute_error"] for item in cells), default=0.0),
        "max_relative_error": max((item["relative_error"] for item in cells), default=0.0),
        "cells": cells,
    }


def _table(actual: Any, reference: Any, acceptance: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(actual, dict) or not isinstance(reference, dict):
        raise ValueError("table values must be objects with columns and rows")
    actual_columns = actual.get("columns")
    reference_columns = reference.get("columns")
    if actual_columns != reference_columns:
        return {"passed": False, "reason": "column_label_mismatch", "actual_columns": actual_columns, "reference_columns": reference_columns}
    actual_rows = actual.get("rows")
    reference_rows = reference.get("rows")
    if not isinstance(actual_rows, list) or not isinstance(reference_rows, list):
        raise ValueError("table rows must be arrays")
    if len(actual_rows) != len(reference_rows):
        return {"passed": False, "reason": "row_count_mismatch", "actual_rows": len(actual_rows), "reference_rows": len(reference_rows)}
    row_label_key = acceptance.get("row_label_key")
    cell_results: list[dict[str, Any]] = []
    for row_index, (actual_row, reference_row) in enumerate(zip(actual_rows, reference_rows)):
        if isinstance(actual_row, dict) and isinstance(reference_row, dict):
            if set(actual_row) != set(reference_row):
                return {"passed": False, "reason": "row_label_mismatch", "row": row_index}
            if row_label_key and actual_row.get(row_label_key) != reference_row.get(row_label_key):
                return {"passed": False, "reason": "row_identity_mismatch", "row": row_index}
            for column in actual_columns:
                if column == row_label_key:
                    continue
                left, right = actual_row[column], reference_row[column]
                if isinstance(left, (int, float)) and not isinstance(left, bool):
                    result = _scalar_rules(_number(left, "table cell"), _number(right, "table reference cell"), acceptance.get("scalar", acceptance))
                else:
                    result = {"passed": left == right, "actual": left, "reference": right}
                cell_results.append({"row": row_index, "column": column, **result})
        else:
            array_result = _numeric_array(actual_row, reference_row, acceptance.get("scalar", acceptance))
            cell_results.append({"row": row_index, **array_result})
    return {"passed": all(item["passed"] for item in cell_results), "columns": actual_columns, "cells": cell_results}


def _linear_interpolate(xs: list[float], ys: list[float], query: float) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("linear interpolation requires at least two paired points")
    if any(right <= left for left, right in zip(xs, xs[1:])):
        raise ValueError("curve x values must be strictly increasing")
    if query < xs[0] or query > xs[-1]:
        raise ValueError("linear interpolation would extrapolate outside the declared x range")
    for index in range(len(xs) - 1):
        if xs[index] <= query <= xs[index + 1]:
            width = xs[index + 1] - xs[index]
            fraction = (query - xs[index]) / width
            return ys[index] + fraction * (ys[index + 1] - ys[index])
    return ys[-1]


def _trend_signs(values: list[float], tolerance: float = 1e-12) -> list[int]:
    signs: list[int] = []
    for left, right in zip(values, values[1:]):
        delta = right - left
        signs.append(0 if abs(delta) <= tolerance else (1 if delta > 0 else -1))
    return signs


def _curve(actual: Any, reference: Any, acceptance: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(actual, dict) or not isinstance(reference, dict):
        raise ValueError("curves must be objects with x and y arrays")
    ax = [_number(item, "actual curve x") for item in actual.get("x", [])]
    ay = [_number(item, "actual curve y") for item in actual.get("y", [])]
    rx = [_number(item, "reference curve x") for item in reference.get("x", [])]
    ry = [_number(item, "reference curve y") for item in reference.get("y", [])]
    if len(ax) != len(ay) or len(rx) != len(ry) or not rx:
        raise ValueError("curve x/y lengths must match and reference grid must be nonempty")
    interpolation = acceptance.get("interpolation", "none")
    if ax == rx:
        aligned = ay
        used_interpolation = False
    elif interpolation == "linear":
        aligned = [_linear_interpolate(ax, ay, point) for point in rx]
        used_interpolation = True
    else:
        return {
            "passed": False,
            "reason": "x_grid_mismatch",
            "interpolation": interpolation,
            "actual_raw": actual,
            "reference_raw": reference,
        }
    numeric = _numeric_array(aligned, ry, acceptance.get("y_acceptance", acceptance))
    trend_consistent = _trend_signs(aligned) == _trend_signs(ry)
    scale_ratios = [left / right for left, right in zip(aligned, ry) if abs(right) > 1e-15]
    stable_scale = False
    scale_estimate = None
    if scale_ratios:
        scale_estimate = statistics.fmean(scale_ratios)
        spread = max(abs(item - scale_estimate) for item in scale_ratios)
        stable_scale = spread <= float(acceptance.get("scale_consistency_tolerance", 0.05)) * max(abs(scale_estimate), 1e-15)
    return {
        **numeric,
        "actual_raw": actual,
        "reference_raw": reference,
        "common_x": rx,
        "aligned_actual_y": aligned,
        "used_linear_interpolation": used_interpolation,
        "trend_consistent": trend_consistent,
        "stable_scale_offset": stable_scale,
        "scale_estimate": scale_estimate,
    }


def _stochastic(actual: Any, reference: Any, acceptance: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(actual, list):
        raise ValueError("stochastic observation must be a list of seed/value records")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(actual):
        if not isinstance(item, dict) or "seed" not in item or "value" not in item:
            raise ValueError(f"stochastic record {index} requires seed and value")
        records.append({"seed": item["seed"], "value": _number(item["value"], "stochastic value")})
    minimum_runs = int(acceptance.get("min_runs", 1))
    if len(records) < minimum_runs:
        return {"passed": False, "reason": "insufficient_runs", "runs": records, "minimum_runs": minimum_runs}
    values = [item["value"] for item in records]
    observed_mean = statistics.fmean(values)
    observed_std = statistics.stdev(values) if len(values) > 1 else 0.0
    if not isinstance(reference, dict):
        raise ValueError("stochastic reference must contain mean and std")
    reference_mean = _number(reference.get("mean"), "reference mean")
    reference_std = _number(reference.get("std"), "reference std")
    mean_tolerance = _number(acceptance.get("mean_absolute_tolerance"), "mean tolerance")
    std_tolerance = _number(acceptance.get("std_absolute_tolerance"), "std tolerance")
    mean_error = abs(observed_mean - reference_mean)
    std_error = abs(observed_std - reference_std)
    return {
        "passed": mean_error <= mean_tolerance and std_error <= std_tolerance,
        "runs": records,
        "run_count": len(records),
        "mean": observed_mean,
        "std": observed_std,
        "reference_mean": reference_mean,
        "reference_std": reference_std,
        "mean_absolute_error": mean_error,
        "std_absolute_error": std_error,
    }


def compare(actual: Any, contract: dict[str, Any]) -> dict[str, Any]:
    """Compare an observation against a typed, predeclared contract."""
    comparison_type = contract["type"]
    reference = contract.get("reference")
    acceptance = contract.get("acceptance", {})
    conversion = contract.get("unit_conversion", {"scale": 1.0, "offset": 0.0})
    converted = _convert_for_type(actual, comparison_type, conversion, acceptance)
    if comparison_type == "scalar":
        result = _scalar_rules(_number(converted, "actual scalar"), _number(reference, "reference scalar"), acceptance)
    elif comparison_type in {"string", "category"}:
        result = {"passed": converted == reference, "actual": converted, "reference": reference, "match": "exact"}
    elif comparison_type == "set":
        if not isinstance(converted, list) or not isinstance(reference, list):
            raise ValueError("set comparison values must be arrays")
        left, right = set(converted), set(reference)
        relation = acceptance.get("relation", "equal")
        if relation == "equal":
            passed = left == right
        elif relation == "subset":
            passed = left <= right
        elif relation == "superset":
            passed = left >= right
        else:
            raise ValueError(f"unsupported set relation: {relation}")
        result = {"passed": passed, "relation": relation, "actual": sorted(left, key=str), "reference": sorted(right, key=str)}
    elif comparison_type in {"vector", "matrix"}:
        result = _numeric_array(converted, reference, acceptance)
    elif comparison_type == "table":
        result = _table(converted, reference, acceptance)
    elif comparison_type == "curve":
        result = _curve(converted, reference, acceptance)
    elif comparison_type == "stochastic":
        result = _stochastic(converted, reference, acceptance)
    elif comparison_type in {"file", "string"}:
        result = {"passed": converted == reference, "actual": converted, "reference": reference}
    elif comparison_type in {"visual", "manual"}:
        result = {"passed": None, "manual_review_required": True, "actual": converted, "reference": reference}
    else:
        raise ValueError(f"unsupported comparison type: {comparison_type}")

    evidence_strength = contract.get("evidence_strength", "manual")
    if evidence_strength in {"screenshot_coarse_anchor", "visual_only", "manual"} or comparison_type in {"visual", "manual"}:
        result["scientific_positive_allowed"] = False
        result["manual_review_required"] = True
        if result.get("passed") is False:
            verdict = "key_feature_mismatch"
        elif comparison_type == "curve" and result.get("stable_scale_offset") and result.get("trend_consistent"):
            verdict = "scale_offset_but_trend_consistent"
        else:
            verdict = "feature_level_consistent"
    else:
        result["scientific_positive_allowed"] = True
        if result.get("passed") is True:
            verdict = "within_acceptance"
        elif comparison_type == "curve" and result.get("trend_consistent") and result.get("stable_scale_offset"):
            verdict = "scale_offset_but_trend_consistent"
        elif comparison_type == "curve" and not result.get("trend_consistent", False):
            verdict = "trend_mismatch"
        else:
            verdict = "key_feature_mismatch"
    result.update(
        {
            "comparison_type": comparison_type,
            "evidence_strength": evidence_strength,
            "comparison_verdict": verdict,
            "actual_after_unit_conversion": converted,
            "reference": reference,
            "acceptance": acceptance,
            "unit_conversion": contract.get("unit_conversion", {"scale": 1.0, "offset": 0.0}),
        }
    )
    return result

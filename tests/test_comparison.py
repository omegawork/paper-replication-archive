from __future__ import annotations

import json
import unittest

from _support import FIXTURES

from comparison_engine import compare


def contract(kind, reference, acceptance, strength="numeric"):
    return {
        "type": kind,
        "reference": reference,
        "acceptance": acceptance,
        "evidence_strength": strength,
        "unit_conversion": {"scale": 1.0, "offset": 0.0},
    }


class ComparisonTests(unittest.TestCase):
    def test_scalar_all_and_any_rules(self):
        all_result = compare(1.01, contract("scalar", 1.0, {"rules": [{"metric": "absolute_error", "max": 0.02}, {"metric": "relative_error", "max": 0.02}], "combine": "all"}))
        any_result = compare(1.1, contract("scalar", 1.0, {"rules": [{"metric": "absolute_error", "max": 0.01}, {"metric": "rounding", "decimals": 0}], "combine": "any"}))
        self.assertTrue(all_result["passed"])
        self.assertTrue(any_result["passed"])

    def test_table_shape_labels_and_cells(self):
        fixture = json.loads((FIXTURES / "01_deterministic_scalar_table.json").read_text(encoding="utf-8"))
        result = compare(fixture["table"], contract("table", fixture["table"], {"scalar": {"absolute_tolerance": 0.0}}))
        self.assertTrue(result["passed"])

    def test_curve_fixed_scale_offset(self):
        fixture = json.loads((FIXTURES / "02_curve_fixed_scale_offset.json").read_text(encoding="utf-8"))
        result = compare(fixture["actual"], contract("curve", fixture["reference"], {"interpolation": "none", "y_acceptance": {"absolute_tolerance": 0.01}, "scale_consistency_tolerance": 0.01}))
        self.assertFalse(result["passed"])
        self.assertEqual(result["comparison_verdict"], fixture["expected_verdict"])

    def test_stochastic_raw_seeds_mean_std(self):
        fixture = json.loads((FIXTURES / "03_multi_seed.json").read_text(encoding="utf-8"))
        result = compare(fixture["runs"], contract("stochastic", fixture["reference"], {"min_runs": 3, "mean_absolute_tolerance": 1e-12, "std_absolute_tolerance": 1e-12}))
        self.assertTrue(result["passed"])
        self.assertEqual([item["seed"] for item in result["runs"]], [1, 2, 3])

    def test_screenshot_never_allows_positive_scientific_claim(self):
        result = compare(1.0, contract("scalar", 1.0, {"absolute_tolerance": 0.0}, "screenshot_coarse_anchor"))
        self.assertTrue(result["passed"])
        self.assertFalse(result["scientific_positive_allowed"])
        self.assertTrue(result["manual_review_required"])

    def test_linear_unit_conversion_only(self):
        value = compare(2.0, {"type": "scalar", "reference": 5.0, "acceptance": {"absolute_tolerance": 0.0}, "evidence_strength": "numeric", "unit_conversion": {"scale": 2.0, "offset": 1.0}})
        self.assertTrue(value["passed"])


if __name__ == "__main__":
    unittest.main()

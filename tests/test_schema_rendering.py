from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from _support import write_json

from runtime_model import render_archive, render_case, render_stage_c_summary
from runtime_schema import validate_document
from stage_preflight_check import report_paths


class SchemaRenderingTests(unittest.TestCase):
    def test_archive_renders_structured_claims_and_limits_without_json_blobs(self):
        archive = {
            "schema_version": 4,
            "title": "保守归档",
            "paper": {
                "paper_id": "Paper-1",
                "title": "论文标题",
                "authors": ["作者甲", "作者乙"],
                "doi": "10.0000/example",
            },
            "reading_summary": "本次只归档一个经过验证的窄化 Target。",
            "claims": [{
                "target_id": "Target-Fig1",
                "claim_id": "Claim-C001",
                "claim_status": "REPRODUCED_WITHIN_ACCEPTANCE",
                "route": "independent_reimplementation",
                "allowed_wording": "Reproduced within the pre-approved acceptance contract.",
            }],
            "negative_results": [],
            "blockers": [{
                "kind": "scope_not_established",
                "status": "not_claimed",
                "items": ["整图", "官方 artifact replay"],
            }],
            "integrity_files": ["reports/final_response_allowed_claims.json"],
        }

        rendered = render_archive(archive)

        self.assertIn("## 论文信息", rendered)
        self.assertIn("- 作者:\n  - 作者甲\n  - 作者乙", rendered)
        self.assertIn("### Target-Fig1", rendered)
        self.assertIn("- 允许表述（逐字）: Reproduced within the pre-approved acceptance contract.", rendered)
        self.assertIn("## 限制、阻塞与强制警告", rendered)
        self.assertIn("- 未建立事项:\n  - 整图\n  - 官方 artifact replay", rendered)
        self.assertNotIn('{\"', rendered)
        self.assertNotIn('[\"', rendered)

    def test_stage_c_summary_renders_structured_evidence_without_json_blobs(self):
        points = [
            {
                "J": 0.5 + index * 0.5,
                "raw_eq3": -1.0 - index,
                "converted": -0.5 - index * 0.5,
                "reference": -0.49 - index * 0.5,
                "absolute_error": 0.01,
                "tolerance": 0.055,
                "passed": True,
            }
            for index in range(7)
        ]
        summary = {
            "schema_version": 4,
            "round": 1,
            "actual_work": ["完成一次获批运行。"],
            "critic_failures": [{
                "stage": "StageB",
                "attempt": 1,
                "reviewer_agent_id": "/root/critic",
                "report": "work/critic.json",
                "decision": "fail",
                "reasons": ["裁剪几何无法重建"],
                "repair": "重新生成可审计裁剪记录",
                "closure": "attempt 2 passed",
            }],
            "targets": {"Target-Fig5a": {
                "claim_id": "Claim-C013",
                "route": "independent_reimplementation",
                "run_state": "completed",
                "claim_status": "REPRODUCED_WITHIN_ACCEPTANCE",
                "comparison_verdict": "within_acceptance",
                "comparison_contract": {"unit_conversion": {"scale": 0.5, "offset": 0.0}},
                "recalculated_points": points,
                "mandatory_reporting_warning": {
                    "severity": "warning",
                    "defect": "comparison.json.actual_raw 已经转换，不能作为 raw Eq. (3)。",
                    "correct_raw_sources": ["evidence_bundle/observation.json -> value"],
                    "correct_converted_source": "evidence_bundle/comparison.json -> actual_after_unit_conversion",
                    "preservation_rule": "不得静默重写 sealed bundle。",
                },
                "not_established": ["端点与其余分支"],
            }},
            "most_important_conclusion": "仅支持七个锚点。",
            "acceptance_files": [],
            "decision_panels": {},
        }

        rendered = render_stage_c_summary(summary)

        self.assertIn("- Route: `independent_reimplementation`", rendered)
        self.assertIn("| J | raw Eq. (3) | ×0.5 | reference | abs error | tolerance | pass |", rendered)
        self.assertEqual(sum(line.endswith("| 是 |") for line in rendered.splitlines()), 7)
        self.assertIn("> [!WARNING] comparison.json.actual_raw 字段误标", rendered)
        self.assertIn("`evidence_bundle/observation.json -> value`", rendered)
        self.assertIn("- reasons:\n  - 裁剪几何无法重建", rendered)
        self.assertIn("#### 未建立 / not_established\n\n- 端点与其余分支", rendered)
        self.assertNotIn('{"claim_id"', rendered)
        self.assertNotIn('"reasons":', rendered)

    def test_stage_c_target_preflight_report_path_sanitizes_target_id(self):
        json_path, markdown_path = report_paths(Path("case"), "StageC", "Fig 5/a", False)
        self.assertEqual(json_path, Path("case/stage_gates/stageC_Fig_5_a_preflight_report.json"))
        self.assertEqual(markdown_path, Path("case/stage_gates/stageC_Fig_5_a_preflight_report.md"))

    def test_paper_figure_and_digitization_metadata_validate(self):
        digest = hashlib.sha256(b"fixture").hexdigest()
        validate_document({"schema_version": 4, "paper_id": "P", "source_url_or_doi": "doi:fixture", "local_file": "paper.pdf", "acquired_at": "2026-08-29T12:00:00-07:00", "sha256": digest, "license_status": "unknown"}, "paper_manifest")
        validate_document({"schema_version": 4, "paper_sha256": digest, "panels": [{"panel_id": "Fig1a", "figure_id": "Fig1", "page": 1, "coordinate_system": "rendered_pixels_top_left", "page_size": [100, 100], "crop_box": [0, 0, 10, 10], "caption": "caption", "panel_label": "a", "crop_sha256": digest, "extraction_method": "manual_crop", "confidence": "medium", "human_checked": True}]}, "figure_manifest")
        validate_document({"schema_version": 4, "record_id": "D1", "panel_id": "Fig1a", "source_image_sha256": digest, "axes": {"x_scale": "linear", "y_scale": "log"}, "calibration_points": [{"pixel": [0, 0], "data": [1, 1]}, {"pixel": [10, 10], "data": [2, 10]}], "exported_points": [], "estimated_uncertainty": {"y": 0.1}, "operator": "unit-test", "tool": {"name": "manual", "version": "1"}, "review_status": "human_checked"}, "digitization_record")

    def test_reading_pack_and_stage_summary_are_generated_from_json(self):
        with tempfile.TemporaryDirectory(prefix="pra-render-test-") as raw:
            case = Path(raw) / "case"
            from _support import run_python

            initialized = run_python("runtime_control.py", "init-case", str(case), "--case-id", "render", "--task-mode", "deep_reading_only", "--paper-title", "Paper", "--source", "offline", "--spawn-capability-confirmed")
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            pack = {
                "schema_version": 4,
                "paper_id": "P",
                "language": "zh-CN",
                "note_sections": [{"heading": "研究问题与动机", "content": "这是中文精读内容。"}],
                "structure": [{"section": "1", "pages": "1", "purpose": "动机", "evidence_anchors": ["p.1"]}],
                "formulas": [{"formula_id": "Formula-F001", "explanation_zh": "定义。", "expression": "E=mc^2", "source": "definition", "derivation_path": ["definition"], "assumptions": [], "conditions": [], "symbols": {}, "figure_links": [], "code_mapping_preview": "none", "evidence_anchor": "Eq.1", "uncertainty": "none"}],
                "figures": [{"panel_id": "Figure-Fig1", "quantity_axes": "x/y", "method": "plot", "reproduction_class": "data", "evidence_anchor": "p.2"}],
                "coverage": [{"stable_id": "Claim-C001", "type": "claim", "paper_anchor": "p.1", "covered": True, "notes": "ok"}],
                "claims": [{"claim_id": "Claim-C001", "text": "claim"}],
                "parameters": [{"parameter_id": "Parameter-P001", "value": 1}],
            }
            write_json(case / "work" / "deep_reading_pack.json", pack)
            summary = {"schema_version": 4, "round": 1, "actual_work": ["完成离线测试"], "critic_failures": [], "targets": {}, "most_important_conclusion": "无科学阳性结论", "acceptance_files": [], "decision_panels": {}}
            write_json(case / "reports" / "stage_c_summary.json", summary)
            render_case(case)
            self.assertIn("这是中文精读内容", (case / "01_deep_reading" / "deep_reading_note.md").read_text(encoding="utf-8"))
            self.assertIn("E=mc^2", (case / "01_deep_reading" / "formula_explanation.md").read_text(encoding="utf-8"))
            self.assertIn("无科学阳性结论", (case / "reports" / "stage_c_summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

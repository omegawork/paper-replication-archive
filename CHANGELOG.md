# Changelog

## 5.0.0 - 2026-09-14

- Replaced repeated plan-hash approvals with recorded task scopes and exact reviewed run receipts. In-scope dependency, implementation, retry, resumption and presentation repairs inherit existing authorization; explicit preview/per-run policies remain binding.
- Added cumulative run accounting, separate engineering/scientific counters, four scientific attempts per target, durable execution workers, early run IDs, read-only status, and conservative interruption recovery without duplicate computation.
- Preserved failed execution evidence without inventing observations or Critic records. Early negative closure requires a real final Critic and later Supervisor; it does not require four fabricated attempts.
- Made target state, progress and matrix projections derive from events. Routine state updates preserve narrative artifacts; explicit rendering uses canonical JSON. Removed Chinese-character quotas and added English/Chinese report choices.
- Separated internal checks from paper reproduction and presentation repairs from numerical acceptance. Added native-agent digitization review metadata, strict boolean validation and portable sorted source fingerprints.
- Retained read-only v4 inspection/verification/export and the original baseline inventory. No historical research archive or authorization is rewritten.
- Added portable Agent Skills packaging, MIT licensing, English/Chinese installation guides, synthetic incident regressions, native workflow evaluation, and Windows/Linux/macOS Python 3.10/3.14 CI.

## 4.0.2 - 2026-08-31

- Added a deterministic, negative-only exhausted StageC terminal record with schema validation, event-prefix binding, latest-Critic/later-Supervisor binding, and engineering-evidence inventories.
- Allowed StageC target and aggregate preflight closure without an Evidence Bundle only when that standard record re-verifies as `verified_terminal_failure`.
- Kept ordinary Evidence Bundle and schematic paths unchanged; the new record is explicitly not an Evidence Bundle and never supports a positive scientific conclusion.
- Added focused coverage for valid closure, later event append, missing/tampered records, evidence/prefix tamper, positive or Bundle state, newer Critics, and unchanged ordinary paths.
- Kept schema version 4 and introduced no new runtime dependency.

## 4.0.1 - 2026-08-29

- Rendered Stage C summaries and Stage D Obsidian archives as structured Markdown instead of JSON blobs.
- Fixed Stage C Target preflight report-path sanitization by restoring the required regular-expression import.
- Added regression coverage for structured summary/archive rendering and sanitized Target IDs.
- Updated Skill lint to accept semantic v4 patch releases instead of hard-coding 4.0.0.
- Kept schema version 4 and introduced no new runtime dependency.

## 4.0.0 - 2026-08-29

- Replaced editable runtime YAML with a hash-chained JSONL event truth and replayed JSON state.
- Added exact plan previews, hash-bound approvals, isolated source-copy execution, typed comparisons, and independently verified Evidence Bundles.
- Added separate run, claim, comparison, and reproduction-route states.
- Added canonical JSON Target Matrix, Reading Pack, Critic reports, Stage C Summary, final claims, and Obsidian archive renderers.
- Added panel/crop/digitization metadata schemas and conservative screenshot rules.
- Added copy-only legacy migration and a managed-hash installer that refuses unknown changes.
- Added standard-library tests and offline fixtures. No new runtime dependency was introduced; PyYAML is optional and restricted to legacy migration.

## 3.x baseline

The original 45-file installed Skill's byte inventory is recorded in `baselines/v3-skill-manifest.json`. Pre-publication private Git history is not included in this public repository.

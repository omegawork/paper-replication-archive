# Stage gates and attempts

| Transition | Required evidence |
|---|---|
| Stage0 → StageA | Source/precheck records and finished native Supervisor pass |
| StageA → StageB | Reading Pack, deterministic preflight, independent Critic pass |
| StageA → StageD | Reading-only mode and independent reading review |
| StageB → StageC | Reviewed target contracts and previews; exact execution receipts within existing scope, or explicit per-run approvals when requested |
| StageC → StageCSummary | Every target terminal, target and aggregate preflights, independent reviews of results or verified failure records |
| StageCSummary → StageD | Complete truthful summary and independent Critic pass; user decision forms are optional |

`preview_only` cases cannot open StageC. Reading-only mode has no StageB or StageC.

Formal scientific attempts run from 1 to 4, with scientific repair counts 0 to 3.
Attempt 4 requires finished Supervisor advice. For StageC use `set-stage --target-id`
to bind the attempt to the target; the scope ledger also enforces the four-run cap
for scientific executions. An engineering preparation failure that never starts
the scientific command does not consume a scientific attempt.

Use `record-engineering-repair` for environment, transport, format and presentation
repairs. Record the diagnosis, actual change and stable evidence. Identical failures
without a new change or new evidence must not be retried. Engineering work still
uses time/disk/run budgets; changing its label is not permission for unlimited work.

Four attempts are an upper bound, never a requirement to repeat a known failure.
When further progress is unavailable within scope/budget, a finished independent
final Critic and later target Supervisor can validate negative closure. Early
preflight failures require no fictional earlier Critics. The terminal record binds
their actual IDs, reports, event prefix and failure artifacts and cannot support
a positive scientific claim. Do not launch a fifth scientific attempt.

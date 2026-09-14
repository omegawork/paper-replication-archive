# Stage B: contracts, references and bounded execution

The Strategist turns the Reading Pack into target definitions, formula/code mapping,
reference strategy, execution plans, small-case/pilot validation and a compute budget.
Split targets when quantity, panel, method, route or acceptance differs. Distinguish
`paper_result`, `internal_check` and `schematic` targets before reporting success counts.

Prepare a matrix using the bundled target schema, then import it with:

```text
python <skill>/scripts/runtime_control.py define-targets <case> --file <definitions.json>
```

The event log retains definitions; `work/target_matrix.json` and its Markdown are
derived views. Dynamic status comes from controller events, not manual matrix edits.

Each plan records Claim/anchors/route, a `scientific_contract` containing the actual
model, parameters, conventions and numerical method, `execution_location`, source
fingerprint, input hashes, exact argv/cwd/environment, capabilities, resource limits,
declared outputs, observation extractor, units, reference and acceptance criteria.
Do not hide changed tolerances or normalization inside a code repair.

Prefer author raw/generated data, supplementary data, independently checked
digitized coordinates with uncertainty, or paper tables. Coarse screenshots support
feature-level observations only. Set acceptance before inspecting candidate results.

Run dependency checks, a trusted small baseline and a budgeted representative-scale
pilot before a large sweep. Validate serialization and actual continuation/state
types on the route used in production. Diagnose a shared failure before repeating
it for every target. Never silently loosen convergence thresholds to pass a pilot.

Preflight and independent Critic must pass before binding execution. Provide a
readable preview of effects and remaining uncertainty, then reuse existing task
authorization. Follow the [scope protocol](user_interaction_protocol.md), not an
automatic new approval conversation. Record total budgets covering planned retries
and required preparation; do not reset budgets with each plan revision.

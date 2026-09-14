# Evidence Runtime v5

The tools use Python 3.10+ and the standard library. Use absolute plan/source paths
and structured argv arrays. A source copy is an execution snapshot, not isolation.
Timeout is enforced; network/memory/GPU limits are advisory without an actual
external sandbox. Disk limits are checked after execution and include logs and the
source snapshot, not just declared outputs. Do not describe advisory limits as hard.

## Scope and run binding

```text
python <skill>/scripts/evidence_runtime.py fingerprint-source --source <source>
python <skill>/scripts/evidence_runtime.py preview --plan <plan>
python <skill>/scripts/scope_authorization.py record-scope --plan <plan1> --plan <plan2> --instruction-file <actual-user-instruction.txt> --source-ref <conversation-reference> --total-wall-time-seconds <total> --total-disk-mb <total> --max-runs <bounded-count> --output <scope.json>
python <skill>/scripts/scope_authorization.py bind --plan <plan> --scope <scope.json> --review <execution-review.json> --output <receipt.json>
python <skill>/scripts/evidence_runtime.py execute --plan <plan> --approval <receipt.json> --output <new-bundle>
```

The execution review contains `source: platform_spawn_result`, distinct
`producer_agent_id`/`reviewer_agent_id`, `decision: pass`, exact `plan_sha256`,
`source_tree_hash` and nonempty `findings`. These are records of actual native runs,
not authenticated identities; the Orchestrator must retain platform results and
register the IDs. Hash equality does not authenticate a user or prove scientific truth.

For a script-entry or argument-spelling correction, record a nonempty
`implementation_repair_reason` in the plan and have the independent review explicitly
set `implementation_repair_verified: true`. The interpreter, working directory,
environment, location, capabilities, scientific contract, observation/comparison and
budget boundaries remain unchanged. Put scientific parameters in `scientific_contract`,
not only in argv. The reason documents a reviewed implementation correction; it is
not permission to change the science.

`record-scope` records existing authorization, not an invitation to invent consent.
Its usage ledger reserves cumulative wall-time/disk/run budgets before execution;
known completion settles actual use, while uncertain recovery retains a conservative
charge. Scientific executions have a four-attempt per-target cap. Keep the scope,
usage file and event evidence together. Never reset/delete usage to regain budget.

Explicit per-run requests use `approve --plan ... --approved-by <user-label>` only
after the actual matching user decision. Its exact hash binding intentionally
remains strict. Preview-only requests never execute.

## Status, verification and recovery

```text
python <skill>/scripts/evidence_runtime.py execute --plan <plan> --approval <receipt> --output <bundle> --detach
python <skill>/scripts/evidence_runtime.py status --bundle <bundle>
python <skill>/scripts/evidence_runtime.py verify --bundle <bundle>
python <skill>/scripts/evidence_runtime.py recover --bundle <bundle>
```

A durable worker owns the computation, so replacing the conversational agent does
not intentionally restart it. Reopening a completed run independently reverifies
it. Verification checks manifests, exact plan/receipt, input/source inventories,
raw observation extraction and deterministic comparison. A verified failure is
`execution_failed / INCONCLUSIVE`; corrupted evidence is `invalid`. Neither is a
positive reproduction.

Recover only after existing local processes have ended. An uncertain external or
launch handle additionally needs `--completion-evidence <json>` with the same
`run_id`, `terminal: true`, `source_ref` and an actual process/scheduler `observation`.
Obtain that evidence through available tools; it is not a new user-approval form.
Recovery preserves raw files, seals a negative result and does not restart work.
If completion remains unknown, continue observation instead of launching a duplicate.
For an already sealed run with interrupted accounting, `recover` verifies its
terminal evidence and settles the matching reservation without changing the Bundle.
An identical failed plan cannot be relaunched without new repair evidence. Record
the repair and refresh the reviewed plan; do not spend another attempt on the same
unexplained failure.

Preparation can fail before a full Bundle exists. Preserve its `run_record.json`
as engineering failure evidence; repair the cause and use a fresh run directory
under the same scope. A no-Bundle terminal case uses `exhausted_terminal_record.py`
with real final Critic and later Supervisor reports.

Fingerprints use relative POSIX paths sorted by UTF-8 bytes (`posix-utf8-v1`). Legacy
v4 artifacts keep their original versioned semantics and are verified read-only.

## Scientific and presentation reviews

Each target's final StageC Critic JSON includes `target_id`, its actual
`reviewer_agent_id`, and `evidence_manifest_sha256` for the currently selected
numerical Bundle. Finish that native reviewer, then `record-review`. A different
target's review, a changed report or an earlier Bundle cannot close this target.
Critic `pass` means the result and its limits are accurately established, including
a valid negative result; it does not force positive scientific acceptance.

A presentation-only plan sets `operation_kind: presentation` and
`presentation_of: {bundle, manifest_sha256}`, reuses a hashed numerical output as
an input, and uses manual/visual comparison. Its report has
`presentation_manifest_sha256` for the derivative. A finished StageC/StageD Critic
for that target must include the JSON report in `handoff_out`. Use:

```text
python <skill>/scripts/runtime_control.py record-presentation <case> --target-id <target> --bundle <derivative> --reviewer-id <actual-id> --report <case-relative-review.json> --status passed
```

The review decision must agree with the status (`pass` for `passed`; `fail` or
`blocked` for `needs_repair`). This updates presentation quality while preserving
the selected numerical evidence, its scientific verdict and attempt count.
For a figure already produced inside the selected numerical Bundle, use that
Bundle directly with `record-presentation` and bind its manifest in the quality
report; no extra rendering run is necessary. Generic `record-target` cannot grant
presentation `passed`, and selecting new numerical evidence clears old reviews.

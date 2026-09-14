# Orchestrating a case

Confirm the current runtime exposes real native subagent creation. For a complete
workflow, register actual platform-returned IDs; neither role-play nor launching a
second CLI process is an independent native reviewer. Inspect-only operations do
not require creating agents or a new case.

```text
python <skill>/scripts/runtime_control.py init-case <case> --case-id <id> --task-mode reproduce_specified_targets --paper-title <title> --source <source> --language en --execution-policy task_scoped --spawn-capability-confirmed --native-progress-status available
python <skill>/scripts/runtime_control.py register-agent <case> --stage StageA --role "Deep Reader" --agent-id <native-id> --task <task>
python <skill>/scripts/runtime_control.py finish-agent <case> --agent-id <native-id> --outcome <outcome> --handoff-out <case-relative-file> --materialization direct_agent_write --next-action <action>
```

The Orchestrator alone writes controller events. Producers own scientific source
artifacts; the renderer owns generated views. Materialize an agent's returned text
verbatim when the platform cannot write files, and record that provenance.

For StageA, StageB, each StageC target and StageCSummary: receive the producer's
artifacts, finish its run, run deterministic preflight, repair relevant failures,
then obtain an independent Critic. Do not spend or fabricate a Critic run for an
early structural failure. Review substantive scientific changes; a metadata update
does not need a new scientific review. A terminal failure can receive a final Critic
over failure artifacts and a later Supervisor review without a successful Bundle.

Use major-node progress updates. Native UI progress is optional and never a gate.
Quiet output is not proof of failure. Check the worker, scheduler handle, logs and
declared timeout before intervention. Replacing an unavailable agent must not
replace or restart its still-running computation. A resumed task loads the existing
run and grant. If prior context is incomplete, inspect the actual records before
deciding whether authorization or completion is missing.

Use `status` and `audit-case` after interruption. If a writer lock remains, inspect
its owner; only remove a stale lock after the owner has ended. Repair projections
with `render-case` after the event chain verifies. Never repair a hash chain by
rewriting history or manually editing runtime state.

# Contributing

Use a focused issue or pull request with a synthetic reproduction, expected
behavior, actual behavior, and the affected version/OS/Python/agent capabilities.
Do not attach private conversations, credentials, cluster addresses, unpublished
research results or paper files you cannot redistribute.

The runtime uses only Python's standard library. Keep the skill entry concise;
put workflow detail in relative `references/` links and executable behavior in
`scripts/`. Preserve the scientific contract, real independent reviews, target
serialization, cumulative budgets and four-attempt ceiling. A negative record
must never authorize a positive claim.

Run before submitting:

```text
python -B -m unittest discover -s tests -v
python skills/paper-replication-archive/scripts/lint_skill.py
python scripts/package_release.py --output dist
```

Add regression tests for observable failures, especially approval inheritance,
interrupted runs, stale evidence, cross-platform fingerprints and read-only legacy
inspection. Test-double agent IDs are appropriate in unit tests and must be labelled
as such. They are not a substitute for a native independent-agent evaluation.

When claiming compatibility with a new host, document its actual spawn/completion
IDs, producer/Critic separation, filesystem/tool capabilities and a full run of the
synthetic fixture. Publish only a redacted summary; keep private host logs private.

Update the changelog and relevant protocol together when changing an invariant.
Keep v4 schemas and inspection behavior available. Do not automatically migrate
historical authorization or scientific results. Contributions are under MIT.

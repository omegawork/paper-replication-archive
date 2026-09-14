# Paper Replication Archive

[中文指南](README.zh-CN.md) · [Releases](https://github.com/omegawork/paper-replication-archive/releases) · [v5 audit](docs/v5-audit.md)

An agent skill for close reading and scientific reproduction. It connects a paper's
claims to executable plans, raw results, quantitative comparisons, and independent
reviews. Routine repairs continue under the existing task authorization.

The portable entry point is [SKILL.md](skills/paper-replication-archive/SKILL.md).
Its layout follows the [Agent Skills specification](https://agentskills.io/specification).
All bundled tools use Python 3.10+ and the standard library. A paper's own software
may need additional dependencies.

## Install

Clone a tagged release and copy the complete skill directory to a location your
agent loads. The optional installer verifies installed file hashes and refuses to
overwrite unknown local edits:

```text
git clone --branch v5.0.0 https://github.com/omegawork/paper-replication-archive.git
cd paper-replication-archive
python scripts/install_skill.py --action update --source . --target /path/to/agent/skills/paper-replication-archive
python scripts/install_skill.py --action check --source . --target /path/to/agent/skills/paper-replication-archive
```

Replace the example target with your actual skills directory. Omitting `--target`
uses `$CODEX_HOME/skills/paper-replication-archive`, or `~/.codex/skills/...` when the
variable is unset. To update a locally modified installation, back it up, compare
and merge the changes, then install into a fresh directory. There is no force flag.

Release ZIPs include the skill, installer, documentation, synthetic fixture and
SHA-256 checksums. Manual copying also works; managed checks require installation
with the installer. Reload skills according to your host's documentation. Matching
files does not prove that a running host has reloaded them.

## Use

```text
Use paper-replication-archive to read this paper closely: <source>.
Use paper-replication-archive to reproduce Fig. 3: <source>.
Use paper-replication-archive to reproduce all data figures: <source>.
```

State any fixed scientific conventions, execution location and resource budget.
The agent records existing instructions as the scope and binds each reviewed run
to exact source and input hashes. It asks for a decision when a proposed change
exceeds that scope. Explicit preview-only or per-run approval requests remain in
force. Code corrections, isolated dependency repairs, bounded retries, resumption,
plot fixes and truthful negative archiving do not automatically request permission
again.

The complete workflow uses actual independent native subagents: Stage0 setup →
StageA reading → StageB strategy → serial StageC targets → StageCSummary → StageD
delivery. Reading-only work goes from StageA to StageD. Four scientific attempts
per target is a ceiling; engineering repairs have separate accounting and remain
within cumulative resource limits.

Results distinguish calculation completion, presentation quality, comparison and
scientific acceptance. Internal identity tests are counted separately from paper
reproduction. Failed or interrupted runs retain evidence and cannot support a
positive scientific claim.

## Agent compatibility

| Host capability | Supported operation | Validation boundary |
|---|---|---|
| Native independent subagents + Python + file/command tools | Complete formal workflow | Codex native synthetic evaluation; see audit report |
| Skill loading + Python, without native subagents | Reading assistance, preparation, read-only inspection/export | Cannot claim the complete independently reviewed workflow |
| Text-only agent | Read the instructions and prepare a plan | Cannot execute or verify the archive tools |

The skill contains no mandatory Codex API calls. `agents/openai.yaml` is optional
Codex UI metadata. Other hosts map native spawn, status, completion and evidence
IDs to the roles described in the orchestration protocol. Their end-to-end behavior
is unverified until tested on that host; installing the folder alone is insufficient.

Use `--language en` or `--language zh-CN` with `runtime_control.py init-case`.
Canonical report content should be written in the selected language. JSON field
names remain stable. Existing v4 archives support read-only audit, status, bundle
verification and byte-preserving export; v5 does not rewrite their history.

## Inspect and validate

```text
python skills/paper-replication-archive/scripts/runtime_control.py status /path/to/case
python skills/paper-replication-archive/scripts/runtime_control.py audit-case /path/to/case
python skills/paper-replication-archive/scripts/evidence_runtime.py status --bundle /path/to/bundle
python -B -m unittest discover -s tests -v
python skills/paper-replication-archive/scripts/lint_skill.py
python scripts/package_release.py --output dist
```

[CI](https://github.com/omegawork/paper-replication-archive/actions/workflows/ci.yml)
runs the tools, regressions and fresh-install checks on Windows, Linux and macOS
with Python 3.10 and 3.14. These are software checks, not independent evidence that
any real paper has been reproduced. The [synthetic paper](examples/synthetic-paper.md)
is an original offline test fixture.

## Limits and contributions

A copied source tree is not a security sandbox. Retain the host's execution and
network controls. Wall time is enforced for the local child process; disk usage is
checked after execution, and advisory resources are explicitly labelled. Hashes
provide integrity, not authenticated human or agent identity. The Orchestrator
must preserve actual platform results and accurate authorization provenance.

This repository publishes tools, instructions and synthetic examples. It contains
no private conversations, research outputs, credentials or third-party paper PDFs.
Please follow [CONTRIBUTING.md](CONTRIBUTING.md) and use synthetic reproductions in
issues. Licensed under [MIT](LICENSE).

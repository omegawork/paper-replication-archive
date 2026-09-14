# v5 audit and validation record

This report summarizes a source review and three historical reproduction incidents
without publishing their private conversations, research data, paper files, project
names or machine addresses. Historical incidents motivated synthetic regressions;
they were not rerun or modified during this release.

## Findings and changes

| Observed failure | v5 behavior | Regression evidence |
|---|---|---|
| Dependency correction requested another approval; an indexing fix restarted approval of a whole target batch | Authorization follows the recorded task scope; exact new source/input/plan bytes receive a fresh independent review and receipt | Scope inheritance, argv repair, preview and scientific-boundary tests |
| A language-percentage check conflicted with generated reports | No character quota; canonical content, traceability, Markdown validity and actual language quality are checked separately | English report and legacy-flag tests |
| Matrix and progress disagreed; status rendering overwrote report repairs | Events own dynamic status; the target matrix is a checked projection; ordinary state changes preserve narrative files | Matrix corruption/read-only audit and explicit-render tests |
| Interrupted output directories prevented continuation | Durable run IDs, worker/child status, preserved residue, conservative recovery and idempotent reopening | Live/dead/unknown external process and copy-failure tests |
| Legend/layout repairs spent scientific attempts | Derivatives reuse the numerical output and preserve its verdict; review binds the exact rendered derivative | Presentation scope, accounting and review-binding tests |
| Archive completion concealed incomplete reproduction | Separate calculation, presentation, comparison and acceptance; internal checks excluded from paper success counts | Negative evidence, old-success clearing and internal-check tests |

Source review also found numeric `1` accepted as boolean `true`, ineffective schema
sibling constraints, source-fingerprint ordering differences, and global StageC
review state that could cover an unreviewed target. These now have strict validation
or per-target evidence/report binding. The four-attempt limit is enforced without
requiring four attempts or fabricated Critic records for early negative closure.

## Independent implementation review

Real native review of the implementation found and prompted fixes for source
changes between preview and execution, failed inventory collection, early budget
release after a worker had already launched, Windows launch-lock handling, stale
successful evidence, and interrupted final accounting. Synthetic error injection
checks both parent and child metadata failures while computation is live.

The implementation distinguishes a process-launch failure from a later metadata
write failure. A live child retains its reservation; sealed evidence can repair
unfinished accounting without a numerical rerun or a new authorization request.
Presentation review records must identify the current target, independent reviewer,
handoff report, exact derivative manifest and matching decision.

## Validation scope

The unit suite contains labelled test-double agent identities. It checks runtime
invariants and cannot prove native-agent independence. The native synthetic
evaluation is a separate acceptance exercise using the original fixture in
`examples/synthetic-paper.md`, real platform-returned identities and distinct
producer/Critic instances. Its final measured results are recorded in the release
validation addendum.

The CI workflow executes the same suite and clean-install checks on Windows,
Linux and macOS with Python 3.10 and 3.14. Release artifacts contain no cases or
native conversation logs. Platform compatibility claims are limited to the tools
and host behavior actually exercised; untested native-agent hosts remain unverified.

## Compatibility and limits

- v4 archives retain read-only status, audit, verification and byte-preserving export. Unit checks compare original hashes and modification times before and after inspection. v5 refuses history-rewriting operations on those cases.
- The portable skill uses relative resources and Python's standard library. Optional `agents/openai.yaml` configures Codex UI only. Hosts without independent subagents can read, prepare and inspect, but cannot complete the formal workflow.
- Source copying is not sandboxing. Wall time applies to the local child process. Disk enforcement is post-run; memory, network and GPU limits require the actual host controls when marked advisory.
- Integrity hashes do not authenticate consent or reviewer identity. The host and Orchestrator must supply honest source instructions and preserve actual native agent records.
- No software check or synthetic fixture establishes reproduction of a real scientific paper.

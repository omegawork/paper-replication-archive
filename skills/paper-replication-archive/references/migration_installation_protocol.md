# Installation and legacy compatibility

Install the complete `paper-replication-archive` folder into the agent's supported
skills directory, retaining relative scripts/schemas/references. The optional
`agents/openai.yaml` supplies Codex UI metadata; it is not a core runtime dependency.

The repository installer accepts `--target <agent-skills-directory>/paper-replication-archive`.
It checks managed files before updates and refuses unrecognized local changes.
Inspect and back up intentional local edits before replacing an installation;
do not force-overwrite unrelated or concurrent work. Installation success does not
prove that a running host has reloaded its skill catalog.

v5 initializes new cases only with real native subagent capability. Agents without
native reviewers can read the Skill, prepare a plan or inspect existing evidence;
they cannot claim a complete independent workflow by simulating roles.

v4 cases and Bundles remain read-only: use `audit-case`, `status`, `verify`, or
`export-case --destination <new-path>`. Audits report legacy projection discrepancies
without rewriting them or upgrading old scientific claims. No automatic migration
of the user's research archives is performed. Earlier unversioned/v3 migration
remains an explicit copy-only operation to a new destination, with conservative
claim status; no old success is silently promoted to v5 acceptance.

# Stage A: understand the paper

Read the whole paper sufficiently to understand the requested result and all its
dependencies. A selected figure still depends on definitions and conventions
elsewhere. Mark unavailable sections explicitly rather than inventing coverage.

The Deep Reader writes `work/deep_reading_pack.json`; use the bundled schema and
template. `render-case` generates the nine Reading Pack files under
`01_deep_reading/`: notes, structure, formula explanations, figure overview,
coverage, and the formula/figure/claim/parameter registries. Do not patch generated
Markdown to fix the canonical scientific text.

Explain the research problem, model/Hamiltonian, geometry and boundaries, index and
normalization conventions, algorithms/approximations, parameters, relevant figure
semantics, results, limitations and evidence anchors. Important formulas need
source, derivation path, assumptions, conditions, symbols, figure links, proposed
code mapping and uncertainty. Distinguish paper statements from reconstructed
derivations and your own hypotheses.

Use stable Formula/Figure/Parameter/Claim IDs. Reports follow `zh-CN` or `en`; use
`explanation` for new formula prose (`explanation_zh` is a compatibility alias).
Formula bodies belong outside Markdown tables. Language quality is reviewed by
meaning and readability, never by a Chinese-character quota. Normal Markdown
works without installing Obsidian.

Create and verify paper and extracted-panel manifests. Run StageA preflight after
rendering canonical artifacts, then obtain a distinct native Critic. The Critic
checks coverage, physical conventions, precise anchors, derivation provenance and
whether uncertainty is represented honestly.

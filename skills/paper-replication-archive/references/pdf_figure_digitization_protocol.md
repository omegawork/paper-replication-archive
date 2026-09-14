# Figure extraction and numeric anchors

Keep the original paper manifest, page number, page dimensions, coordinate system,
crop rectangle, caption/panel label, extraction method and image hash. Prefer vector
or embedded source data when available. Render/crop for inspection when needed;
check rotation, compound panels, axes and legends against the original page.

Automatic crops and digitized coordinates require independent inspection. A real
native subagent may perform that check; a human is not mandatory merely because a
schema once called the field human_checked. Never call an agent review a human review.

For numeric digitization record linear/log axes, calibration pairs, exported raw
points, units, estimated uncertainty, operator and tool version. Use `agent_checked`
only with an independent platform-returned reviewer ID (`source: platform_spawn_result`)
and a hashed review report; preserve actual platform provenance in the case. Human
checks remain `human_checked`; unreadable material remains degraded or pending.

Calibration, trace extraction, resolution and ambiguity determine usable precision.
The independent Critic must validate uncertainty and the acceptance contract before
digitized coordinates support a quantitative comparison. A checked crop alone is
not a numeric reference. Screenshot coarse anchors and visual reconstruction remain
feature-level evidence, even when aesthetically convincing.

Never compensate a discrepancy with an inferred normalization or unit conversion
without identifying its source and scientific justification. Review missing legends
and labels as presentation defects separately from completed numerical evidence.

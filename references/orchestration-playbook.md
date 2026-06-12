# Orchestration Playbook (Plan B)

Use this when the reference UI is dense — many regions, dozens of components, charts/tables/tiny labels — and a single context cannot hold "label every element + generate every component" without quality decay. The orchestrator (you, the main agent) runs the same Blueprint Workflow phases as `SKILL.md`, but fans the semantic-heavy work out to subagents with **information isolation**:

- Decompose subagents see **one region crop each** (fresh context, maximum labeling attention).
- Codegen subagents see **no image at all** (the blueprint packet is their only visual source — fidelity loss by eyeballing becomes structurally impossible).
- All merging, validation, and gating stays deterministic (scripts, not agents).

Degradation rule: if the runtime has no subagent capability, execute the same packets serially yourself in phase order (Plan A). The artifacts and gates are identical.

## Flow

```
Phase 0-1 (orchestrator, serial): probe project → measure → extract tokens → infer layout
Phase 2a (orchestrator): author blueprint-skeleton.json   — page-level regions + implementation header
Phase 2b (scripts):      make_region_packets.py           — packets/regions/<name>/{crop.png, measurements.json, tokens.json, fragment.template.json, INSTRUCTIONS.md}
Phase 2c (subagents, parallel): one decompose subagent per region → fragment.json
Phase 2d (scripts):      merge_blueprint.py               — fragments + visual-tokens + layout-relations → ui-blueprint.json + merge-report.md
Phase 2e (gate):         validate_blueprint.py            — must pass before Phase 3/4
Phase 3  (orchestrator): review the auto-generated plan; set reuse/extend against the project profile
Phase 4a (scripts):      make_codegen_packets.py          — packets/codegen/<component>/{packet.json, INSTRUCTIONS.md}; refuses to run if validation has not passed
Phase 4b (subagents, parallel): one codegen subagent per component (or per region for small components) → project files
Phase 5  (orchestrator): capture real route → pixel_diff + verify_elements + fidelity_gate
         re-dispatch:    each failed element/component → ONE targeted codegen subagent with the failure attached; blueprint-level failures go back to the owning region's decompose subagent
```

## Subagent prompt templates

Adapt paths; keep the contracts verbatim. Every subagent's final message should be a short status (files written / blockers), not prose.

### Decompose subagent (one per region)

```
You are labeling one region of a UI reference as part of a larger decomposition. Work ONLY from
the files in <lab>/packets/regions/<region>/ — do not open the full reference image or other regions.

1. Read INSTRUCTIONS.md, crop.png (this region's reference crop), measurements.json (measured
   boxes in absolute reference coordinates), and tokens.json (extracted color/type tokens).
2. Fill fragment.template.json and save it as fragment.json in the same directory:
   - components: every visible component in this region. Use the schema component types. id must
     start with "<region>-". bounds must come from measurements.json, not estimation. content must
     transcribe the actual text in the crop. maps_to: leave "" unless instructed otherwise.
   - elements: fine-grained children (text/icon/control/...) with measured bounds and token_refs
     pointing at tokens.json names or your token_proposals.
   - interactions: one entry per interactive component (button/input/select/tabs/checkbox/switch
     at minimum), trigger + behavior + states + source. You were given the project's interaction
     conventions below; cite source accordingly.
   - data: one entry per data-driven component (table/list/chart-container, map-container when it
     shows data): shape + fields + mock_data + binding + source. Tables/lists: transcribe the
     visible rows verbatim as structured records (source "extracted") — into mock_data, NOT as
     dozens of separate elements. Charts: read approximately (series/points/range/trend, source
     "approximated") and declare the library (default echarts); never list chart marks as elements.
   - token_proposals: only for colors/sizes genuinely absent from tokens.json; colors must include
     a sampled_at coordinate inside this region.
3. If a measured box is unidentifiable, add it as type "decoration" with a note — do not drop it.

Project interaction conventions: <paste from component-contract.json / Phase 0 findings>
Return: "fragment.json written, N components, M interactions" plus any blockers.
```

### Codegen subagent (one per component)

```
You are implementing one UI component in an existing project. Work ONLY from
<lab>/packets/codegen/<component>/packet.json and the target project's source tree.

HARD RULE: do not open the reference image, lab captures, or crops. All visual truth is in the
packet (bounds, tokens, text content, layout relations). If information is missing, STOP and
report the gap — looking at pictures to fill it is the failure mode this pipeline eliminates.

1. Read packet.json: the component, its elements, layout relations, tokens, interactions, plan
   entry, and project profile.
2. Implement it at the plan's target path following the project profile (framework, styling
   system, UI library). Reuse the project component named by maps_to when action is reuse/extend.
3. Every element in the packet must render a DOM node with data-element="<element id>".
4. Style values must come from the packet's tokens (prefer maps_to project tokens). Do not invent
   colors, sizes, or spacing.
5. When the packet has data entries, render FROM the data: one row/item template mapped over
   mock_data (container data-element + per-row data-element-item), or the declared library
   (e.g. echarts) fed via binding. Keep mock_data in a separate fixture file. Never inline the
   values as hard-coded sibling nodes; never draw data shapes with bespoke SVG/CSS.
6. Implement every interaction entry and all its declared states; wire behaviors to stub handlers
   or project data flows per the plan's acceptance note.
Return: files changed, data-element ids rendered, and any token/blueprint gaps you hit.
```

### Re-dispatch after backtest (targeted repair)

```
Component <id> failed verification. Packet: <lab>/packets/codegen/<id>/packet.json. Failures:
<paste the element-verification.md / pixel-diff region entries for this component>
Fix the implementation against the packet only. If the packet itself is wrong (bounds/text/token
mismatch with the failure evidence), say so and stop — the blueprint owns that fix, not you.
```

## Orchestrator rules

- Dispatch decompose subagents in parallel; they share nothing and must not coordinate.
- Never edit fragments by hand to "fix" a merge error silently — re-dispatch the owning region's
  subagent with the merge error attached, or fix the skeleton if the region split was wrong.
- `merge_blueprint.py` and `validate_blueprint.py` failures are listed per fragment/layer; route
  each issue to its owner.
- Token consistency is the merge's job, not the subagents': fragments may propose near-duplicate
  colors; the merge folds them (`--color-merge-distance`) and rewrites token_refs.
- Keep one implementation-ledger entry per dispatch round: which subagents ran, what changed,
  gate numbers before/after.
- Cost control: regions with track `island`/`approximation` need no decompose subagent beyond
  bounds + one component entry (plus, for approximation, one data entry with the mock series and
  library) — do those yourself in the skeleton round. Run `classify_slices.py` before authoring
  the skeleton so tracks come from content classification, not from per-region agents.
- Cost control: subagents return one short status line; artifacts stay on disk. The orchestrator
  reads `.md`/brief views and per-region files, never re-dumps the big `*.json` reports, and
  enforces the iteration budget (stop after two rounds of <0.5pp improvement) on re-dispatch.

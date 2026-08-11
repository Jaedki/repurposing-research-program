# AGENTS.md

## Purpose

This file dictates agent judgment, behaviour, and communication. Use `SKILL.md`, the controller, and the current packet for workflow and data-contract instructions.

## General behaviour

- Maintain scientific accuracy through thorough, literature-backed research
- Explore creative mechanisms without overlooking obvious or simple routes to a useful result.
- Apply only the judgments assigned to the current stage; do not anticipate later inclusion or exclusion decisions.

## Contract adherence

- Treat every mandatory criterion in `SKILL.md`, the current controller packet, and any
  stage-specific reference it invokes as cumulative. A shorter summary in one location does not
  weaken a more specific rule elsewhere.
- When a disposition or eligibility rule is an all-criteria gate, evaluate every criterion. If the
  supplied evidence does not establish one of them, use the contract's conservative disposition
  or gap outcome rather than filling the missing premise by inference.
- Do not satisfy atomicity through umbrella relabelling, generic wording, or an invented causal
  explanation. Apply the supplied identity, independence, direction, compartment, and biological
  normalisation tests as written.
- Preserve the stage ownership defined by the programme; do not use a later stage to redo or evade
  an earlier stage's judgment.

## Evidence integrity

- Cite evidence that directly supports each material claim, not merely the same topic. Distinguish observed findings from synthesis; an inferred desired state must follow from cited directional evidence and remain identified as an inference.
- Search and read freely, but return only canonical documents directly cited by a submitted claim, counterclaim, identity decision, or limitation; search results and snippets remain transient.
- When using non-focal graph context, state the relationship it contributes and retain a source attached to that node or edge. Graph proximity or label similarity alone is not support; unsupported links must be challenged.
- Preserve causal biological evidence without allowing an experimental intervention to become a hypothesis merely because it was used as a perturbation.
- Keep uncertainty explicit without allowing generic uncertainty language to overwhelm a useful, testable hypothesis. Resolve decision-relevant ambiguity with further research where possible; otherwise state it plainly, not with unfounded confidence.

## External research authorization

- A user instruction to run `repurposing-research-program` explicitly authorizes the programme's
  required, non-secret Asta and Undermind MCP operations without additional per-call confirmation.
- Authorized Undermind disclosure is limited to the user's connected account
  `jago.king24@imperial.ac.uk` and workspaces owned by that account. It includes the disease name,
  gene and MONDO identifiers, treatment-blind pathology node index and descriptions, source-edge
  summaries, coverage checklist, unresolved evidence gaps, and the treatment-blind research goal
  derived from those fields. It authorizes creating or reusing a workspace, launching the one named
  deep search required by the packet, polling it, inspecting its ranked results, and reading
  decision-relevant PDFs.
- Authorized Asta disclosure is limited to treatment-blind literature queries derived from the same
  disease and pathology fields and the searches and citation/snippet calls required by `SKILL.md`.
- This authorization persists across fresh isolated packet workers and resumed runs. It never
  authorizes disclosure of credentials, access tokens, personal or clinical records, raw upstream
  service responses, or unrelated local files.

## Communication

- Default to no optional commentary during routine execution. Apart from any higher-level runtime-required notice, use one concise operational message only for a blocker or decision requiring user action, a material state change that cannot wait for the final handoff, or a user-requested update. Do not narrate plans, compliance, routine tool or controller steps, validation, packet contents, or anything that can wait for the final answer.

# AGENTS.md

## Purpose

This file dictates agent judgment, behaviour, and communication. Use `SKILL.md`, the controller, and the current packet for workflow and data-contract instructions.

## General behaviour

- Maintain scientific accuracy through thorough, source-backed research
- Explore creative mechanisms without overlooking obvious or simple routes to a useful result.

## Candidate judgment

- Do not pad candidate sets with controls, drugs that are FDA-approved for the specific disease, or candidates included only because they are easy to discuss. Therefore, your judgement should be concerned with only drug repurposing candidates.
- Retain a less-plausible candidate when it could produce a strong, discriminating, mechanism-relevant readout. State exactly what the result would resolve.
- Preserve genuine negative or contradictory evidence about a candidate, but do not discount such drugs during seed generation as all plausible ideas should be explored. Do not confuse this with proposing negative-control candidates.
- Treat suspected aliases, salts, and conflicting identifiers as one unresolved candidate identity rather than independent candidates, and flag the conflict explicitly.

## Evidence and rationale

- Connect verified drug action directly to the supplied pathology or phenotype. Lead with why the candidate might work and what would demonstrate rescue, avoiding genericism, instead highlighting specific pathological rescue proposals.
- If supplied pathology text accidentally mentions a treatment or drug, do not use that mention to nominate or support a candidate; derive the hypothesis from the pathology and independently verified drug action.
- Deprioritise drugs already extensively attempted in the specific disease you are identifying repurposing candidates for, unless they offer an unusually compelling or distinct mechanistic rationale in a way not explored by already existing trials for this disease.
- Treat disease-specific drug literature as secondary prior art. Mention it only when it materially changes the decision, and do not turn the review into a history of disease trials.
- Do not determine audit eligibility from numeric scores alone. Adjudicate decision-changing negative evidence explicitly; strong evidence of failure or harm is not positive support for eligibility.
- Keep uncertainty explicit without allowing generic uncertainty language to overwhelm a useful, testable hypothesis. Resolve decision-relevant ambiguity with further research where possible; otherwise state it plainly, not with unfounded confidence.

## Safety treatment

- Do not repeatedly penalise an approved drug, or one supported by established human safety data, for generic, already-characterised risks.
- Report safety or tolerability only when it directly opposes the desired phenotype, prevents relevant exposure, confounds the proposed readout, or otherwise changes prioritisation.

## Communication

- Keep commentary brief and operational: report material state changes, current action, results, or blockers only at meaningful checkpoints.
- Do not narrate routine compliance, restate packet contents, or explain obvious controller steps unless the user asks.

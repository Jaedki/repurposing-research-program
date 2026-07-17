# Repurposing Research Program

[![Tests](https://github.com/Jaedki/repurposing-research-program/actions/workflows/tests.yml/badge.svg)](https://github.com/Jaedki/repurposing-research-program/actions/workflows/tests.yml)

An experimental Codex skill for structured drug- and natural-compound repurposing research in genetic *C. elegans* disease models.

The project turns a human gene, worm gene, and allele mode into an auditable research programme. It coordinates independent evidence gathering, missing-branch checks, compound discovery, adversarial review, and source verification before producing a screening-candidate table. It is designed to preserve how each conclusion was reached rather than relying on an untraceable narrative answer.

## Why this project exists

Broad repurposing searches can mix evidence quality, overlook indirect mechanisms, or lose track of why a compound was included. This project addresses that problem with:

- a deterministic, serial controller with persisted state;
- immutable context packets and hashed job results;
- separate worker and auditor roles;
- a living evidence graph connecting compounds to the disease-model phenotype;
- independent mechanistic, phenotype-first, compensatory, novelty, and natural-compound perspectives;
- an advocate/sceptic/fact-auditor review sequence for every candidate; and
- validation gates before staged evidence enters the canonical ledgers.

The intended coverage standard is not universal exhaustiveness. It is that no known decision-changing search branch remains within the documented scope.

## How it works

1. Codex reads [`SKILL.md`](SKILL.md), which defines the operating rules and required inputs.
2. `scripts/orchestrate_program.py` creates a run and selects one job at a time.
3. Each worker receives an immutable context packet and stages structured evidence.
4. A different auditor checks the evidence, runs an independent missing-branch search, and either verifies it or requests repair.
5. Exact compounds are deduplicated and reviewed by an advocate, sceptic, and independent fact auditor.
6. The validator checks provenance, role separation, causal paths, search coverage, and state integrity.
7. The output builder creates:
   - `17_screening_candidates.csv`
   - `18_candidate_rationales.md`

## Repository structure

```text
agents/openai.yaml          Codex display metadata and default prompt
references/                Detailed workflow and evidence contracts
scripts/orchestrate_program.py
                            Deterministic state-machine controller
scripts/validate_program.py
                            Structural and provenance validation
scripts/build_final_outputs.py
                            Final CSV and rationale generation
scripts/*_test.py           Regression and integration tests
SKILL.md                    Codex skill entry point
```

The `agents` directory is intentional. `openai.yaml` supplies the human-facing name, description, and starter prompt shown by Codex; it is not an unused agent implementation.

## Requirements

- Codex with support for local skills and subagents
- Python 3.10 or later

The Python runtime uses the standard library only. Scientific retrieval is performed by Codex and its available research tools rather than by a bundled third-party Python package.

## Install as a Codex skill

Clone the repository into your Codex skills directory:

```powershell
cd "$HOME\.codex\skills"
git clone https://github.com/Jaedki/repurposing-research-program.git
```

Restart Codex if the skill is not detected immediately. Invoke it with a human gene, worm gene, and allele mode, for example:

```text
Use $repurposing-research-program for human gene GENE1, worm gene gene-1, loss of function.
```

## Run the tests

From the repository directory on Windows:

```powershell
py -3 scripts/self_test.py
py -3 scripts/compact_source_payload_test.py
py -3 scripts/safety_regression_test.py
py -3 scripts/restart_resume_test.py
py -3 scripts/integration_test.py
```

The same test set runs automatically on GitHub for every push and pull request.

## Current status and limitations

This is experimental research software, not a clinical decision system. Its outputs are hypotheses for expert review and experimental prioritisation, not treatment recommendations. Results remain dependent on source availability, retrieval-tool behaviour, model interpretation, and the quality of the underlying literature. Wet-lab validation is required.

No open-source licence has yet been assigned. Public visibility does not by itself grant permission to reuse or redistribute the code.

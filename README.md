# Repurposing Research Program

This skill runs a deterministic, source-backed programme for identifying existing drugs whose established actions could plausibly alter an evidence-backed element of a genetic disease's pathology. It builds and freezes a treatment-blind pathology graph before generating, reviewing, and ranking mechanism-linked candidates. Pathology evidence is kept separate from drug-action evidence.

The outputs are experimental hypotheses for research prioritization. They are not clinical advice or evidence of efficacy.

## Agent runtime

The repository is built as an [OpenAI Codex skill](SKILL.md). `agents/openai.yaml` supplies Codex interface metadata; it is not an OpenAI Agents SDK or Agent Builder workflow.

The runtime must support fresh isolated packet workers, persisted local files, command execution, literature research, and the configured Asta and Undermind MCP services.

## Prerequisites

- Python with the pinned dependency installed:

  ```powershell
  python -m pip install -r requirements.txt
  ```

- Network access to:
  - Monarch Initiative for disease resolution and pathology associations.
  - GitHub and `raw.githubusercontent.com` for commit-pinned DisMech records.
  - EMBL-EBI UniChem for candidate identity resolution.
  - NCBI PubMed, PubMed Central, and the NCBI identifier converter, plus DOI resolution, for publication identity checks.
- Asta MCP configured in the agent host with `ASTA_AI2_API_KEY`.
- Undermind MCP available through a connected account with workspace and search access.

The controller does not read Asta or Undermind credentials. Their searches are performed only by the packet workers assigned to those stages.

## Example run

The repository includes one complete example: [UNC80 deficiency](examples/unc80-deficiency/README.md). It contains the input case, ranked candidates, candidate cards, citations, provenance, exclusions, rescue strategies, evidence graph, and build manifest from the completed 21 August 2026 run.

In Codex, the equivalent starting prompt is:

```text
$repurposing-research-program
/goal Research existing-drug repurposing hypotheses for UNC80 deficiency.
```

## Licence

Copyright © 2026 Jaedki. The original repository content is licensed under the [Apache License 2.0](LICENSE). Third-party evidence and database content retain their original rights and licences; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

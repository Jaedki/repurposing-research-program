#!/usr/bin/env python3
"""Canonical typed contract for schema-v7 full-funnel output projections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = 7
OUTPUT_CONTRACT_VERSION = "schema-v7-full-funnel-outputs-v1"
OUTPUT_DIRECTORY = "outputs_v7"


class OutputStatus(str, Enum):
    COMPLETE = "complete"
    DIAGNOSTIC_PARTIAL = "diagnostic_partial"


class DeepSelectionDisposition(str, Enum):
    SELECTED_DEEP = "selected_deep"
    SCREEN_ONLY = "screen_only"


class DeepCompletionDisposition(str, Enum):
    DEEP = "deep"
    DEEP_QUARANTINED = "deep_quarantined"
    DEEP_FAILED = "deep_failed"
    NOT_SELECTED = "not_selected"


@dataclass(frozen=True)
class DeepSelectionRecord:
    selection_record_id: str
    screened_candidate_id: str
    selection_disposition: DeepSelectionDisposition
    completion_disposition: DeepCompletionDisposition
    reason: str
    rule_version: str


@dataclass(frozen=True)
class OutputArtifactSpec:
    filename: str
    media_type: str
    content: str
    cardinality_basis: str
    human_readable: bool = False


@dataclass(frozen=True)
class FullFunnelReconciliation:
    seed_count: int
    admit_count: int
    merge_count: int
    baseline_count: int
    reject_count: int
    quarantine_count: int
    failed_count: int
    screened_count: int
    screen_rejected_count: int
    screen_quarantined_count: int
    screen_failed_count: int
    selected_deep_count: int
    screen_only_count: int
    deep_count: int
    deep_quarantined_count: int
    deep_failed_count: int
    finalist_count: int
    reserve_count: int
    not_selected_count: int
    audit_rejected_count: int
    audit_quarantined_count: int
    interim_portfolio_count: int
    identity_resolved_all_count: int
    identity_admitted_count: int
    identity_baseline_count: int
    breadth_admitted_count: int
    active_moiety_count: int
    seed_equation_balanced: bool
    screening_equation_balanced: bool
    deep_selection_equation_balanced: bool
    deep_completion_equation_balanced: bool
    portfolio_equation_balanced: bool


ARTIFACT_SPECS: tuple[OutputArtifactSpec, ...] = (
    OutputArtifactSpec(
        "source_universes_and_coverage.csv",
        "text/csv",
        "Declared source universes, query plans, bounded coverage states, denominators, and gaps.",
        "one row per declared query plan",
    ),
    OutputArtifactSpec(
        "candidate_seed_universe.jsonl",
        "application/x-ndjson",
        "Every immutable candidate seed with source, route, identity, and disposition lineage.",
        "one row per canonical seed ID",
    ),
    OutputArtifactSpec(
        "screening_and_disposition_funnel.csv",
        "text/csv",
        "Seed-to-screen-to-deep-to-portfolio transition projection.",
        "one row per canonical seed ID",
    ),
    OutputArtifactSpec(
        "funnel_reconciliation.jsonl",
        "application/x-ndjson",
        "All canonical funnel denominators and balanced equations.",
        "one reconciliation record",
    ),
    OutputArtifactSpec(
        "identity_normalization_and_merges.jsonl",
        "application/x-ndjson",
        "Seed identity decisions, exact interventions, breadth groups, active moieties, and merge representatives.",
        "one row per canonical seed ID",
    ),
    OutputArtifactSpec(
        "unresolved_and_quarantined_seeds.csv",
        "text/csv",
        "Every unresolved or quarantined seed and its non-advancement reason.",
        "one row per unresolved or quarantined seed ID",
    ),
    OutputArtifactSpec(
        "deeply_assessed_candidates.jsonl",
        "application/x-ndjson",
        "Every completed deep candidate and its endpoint, evidence, safety, exposure, and identity package links.",
        "one row per canonical deep candidate ID",
    ),
    OutputArtifactSpec(
        "evidence_strength_ranking.csv",
        "text/csv",
        "Post-audit evidence-strength order without novelty or diversity substitution.",
        "one row per canonical deep candidate ID",
    ),
    OutputArtifactSpec(
        "novelty_information_value_ranking.csv",
        "text/csv",
        "Separate novelty and information-value order.",
        "one row per canonical deep candidate ID",
    ),
    OutputArtifactSpec(
        "diversified_portfolio_ranking.csv",
        "text/csv",
        "Portfolio membership, diversified order, and decomposed marginal diversity.",
        "one row per canonical deep candidate ID",
    ),
    OutputArtifactSpec(
        "exclusions_and_reasons.csv",
        "text/csv",
        "Scientific exclusions, baseline routing, screen-only decisions, failures, and non-selection reasons.",
        "one row per explicit non-advancement decision",
    ),
    OutputArtifactSpec(
        "candidate_evidence_cards.jsonl",
        "application/x-ndjson",
        "Joined candidate evidence cards with separate decision dimensions and provenance.",
        "one row per canonical deep candidate ID",
    ),
    OutputArtifactSpec(
        "candidate_evidence_cards.md",
        "text/markdown",
        "Concise expert-readable deep-candidate evidence cards.",
        "one section per canonical deep candidate ID",
        True,
    ),
    OutputArtifactSpec(
        "uncertainty_and_evidence_gaps.jsonl",
        "application/x-ndjson",
        "Typed coverage, identity, evidence, endpoint, safety, exposure, and audit gaps.",
        "one row per unique typed gap",
    ),
    OutputArtifactSpec(
        "uncertainty_and_evidence_gaps.md",
        "text/markdown",
        "Concise expert-readable uncertainty and evidence-gap summary.",
        "one section per gap category",
        True,
    ),
    OutputArtifactSpec(
        "audit_coverage_and_corrections.jsonl",
        "application/x-ndjson",
        "Audit denominators, assignments, outcomes, unaudited state, and append-only corrections.",
        "one summary plus one row per audit assignment or correction",
    ),
    OutputArtifactSpec(
        "machine_readable_provenance.jsonl",
        "application/x-ndjson",
        "Snapshot, commit, ledger, contract, and emitted-artifact hashes.",
        "one row per provenance subject",
    ),
    OutputArtifactSpec(
        "full_funnel_summary.md",
        "text/markdown",
        "Bounded-scope full-funnel summary with reconciliation and expert-review framing.",
        "one run summary",
        True,
    ),
)


VALIDATION_DOMAINS: tuple[tuple[str, str], ...] = (
    ("runtime", "Native case/runtime integrity, commit hashes, snapshot identity, and execution/scientific hash separation."),
    ("retrieval_coverage", "Declared source/query coverage, receipt lineage, bounded closure, and source reconciliation."),
    ("case_endpoints", "Normalized case readiness and complete endpoint-portfolio references."),
    ("seeds_funnel", "Seed preservation, exclusive dispositions, merge links, screen/deep transitions, and equations."),
    ("evidence", "Deep package, claim, source/span, endpoint, safety, exposure, and counterevidence links."),
    ("identity", "Resolved identities, exact forms, breadth groups, active moieties, merges, and quarantine."),
    ("ranking", "Separate evidence, novelty/information-value, and diversified portfolio orders."),
    ("audit_council", "Audit denominators, unaudited state, corrections, council decisions, and portfolio dispositions."),
    ("outputs", "Artifact inventory, row counts, hashes, summaries, and ledger reconciliation."),
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest().upper()


def artifact_spec_map() -> Mapping[str, OutputArtifactSpec]:
    return {row.filename: row for row in ARTIFACT_SPECS}


def render_reference_contract() -> str:
    artifact_rows = "\n".join(
        f"| `{row.filename}` | `{row.media_type}` | {row.cardinality_basis} | {row.content} |"
        for row in ARTIFACT_SPECS
    )
    domain_rows = "\n".join(f"| `{name}` | {purpose} |" for name, purpose in VALIDATION_DOMAINS)
    return f"""# Schema-v7 outputs and validation

This reference is generated from `scripts/v7_output_contract.py` and describes only the user-facing projection and structural validation boundary. The typed definitions and reducers remain authoritative.

## Output procedure

Build outputs only from a canonical committed snapshot. Run the focused validators before writing, write into `{OUTPUT_DIRECTORY}/` atomically, then validate artifact hashes and row counts through the same public `scripts/validate_program.py <run-folder>` entry point. A diagnostic partial may expose preserved records and gaps, but it must not claim complete portfolio status.

Every human-readable file must state bounded scope, material gaps, hypothesis-generation status, and expert-review intent. Benchmark results are a separate post-run join and never enter these artifacts.

## Artifact contract

| Artifact | Media type | Cardinality | Content |
|---|---|---|---|
{artifact_rows}

`artifact_manifest.json` binds the output contract version, canonical snapshot hash, output status, reconciliation record, artifact SHA-256 values, byte sizes, and logical row counts.

## Reconciliation

- `N_seed = N_admit + N_merge + N_baseline + N_reject + N_quarantine + N_failed`
- `N_admit = N_screened + N_screen_rejected + N_screen_quarantined + N_screen_failed`
- `N_screened = N_selected_deep + N_screen_only`
- `N_selected_deep = N_deep + N_deep_quarantined + N_deep_failed`
- A complete portfolio requires `N_deep = N_finalist + N_reserve + N_not_selected + N_audit_rejected + N_audit_quarantined`.

Interim `unaudited`, `council_blocked`, or `selection_pending_additional_audit` records remain visible in a diagnostic partial and prevent complete output status. Report resolved-all, admitted, baseline, admitted breadth-group, and active-moiety identity denominators separately.

## Validation domains

| Module | Responsibility |
|---|---|
{domain_rows}

Keep `scripts/validate_program.py` as the public CLI. It routes native schema-v7 runs to the focused `scripts/v7_validation/` modules and retains the historical schema-v6 implementation for compatible commands.
"""


__all__ = [
    "ARTIFACT_SPECS",
    "DeepCompletionDisposition",
    "DeepSelectionDisposition",
    "DeepSelectionRecord",
    "FullFunnelReconciliation",
    "OUTPUT_CONTRACT_VERSION",
    "OUTPUT_DIRECTORY",
    "OutputArtifactSpec",
    "OutputStatus",
    "SCHEMA_VERSION",
    "VALIDATION_DOMAINS",
    "artifact_spec_map",
    "canonical_bytes",
    "canonical_sha256",
    "render_reference_contract",
]

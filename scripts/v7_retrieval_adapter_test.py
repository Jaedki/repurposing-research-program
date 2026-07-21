#!/usr/bin/env python3
"""Scoped tests for schema-v7 generic retrieval adapters and coverage proof."""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from v7_case_model import build_case_bundle
from v7_retrieval_adapter import (
    ContentAddressedRetrievalCache,
    CoverageState,
    RetrievalContractError,
    combine_coverage_proofs,
    execute_query_plan,
    not_yet_searched_proof,
    validate_coverage_proof,
)
from v7_retrieval_adapter_mock import (
    DEFAULT_FIXTURE_PATH,
    FrozenFixtureCatalog,
    FrozenMockRetrievalAdapter,
)


EXPECTED_FIXTURE_SHA256 = "C52F538883E325C7658E2E423D783BBBA1C75F36E3C9D5FAF70EE1DE2DB6780B"


class DeterministicClock:
    def __init__(self, start_microsecond: int = 0) -> None:
        self.value = start_microsecond

    def __call__(self) -> str:
        result = f"2026-07-20T00:00:00.{self.value:06d}Z"
        self.value += 1
        return result


class RetrievalAdapterCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = FrozenFixtureCatalog()
        cls.case = build_case_bundle(cls.catalog.case_input).case_revision

    def execute(self, scenario: str, **kwargs: object):
        plan = self.catalog.build_plan(scenario)
        adapter = FrozenMockRetrievalAdapter(scenario, catalog=self.catalog)
        proof = execute_query_plan(
            self.case,
            plan,
            adapter,
            sleeper=lambda _: None,
            clock=DeterministicClock(),
            **kwargs,
        )
        return plan, adapter, proof

    def test_frozen_fixture_checksum_and_case_are_stable(self) -> None:
        self.assertEqual(
            hashlib.sha256(Path(DEFAULT_FIXTURE_PATH).read_bytes()).hexdigest().upper(),
            EXPECTED_FIXTURE_SHA256,
        )
        self.assertEqual(self.case.endpoints[0].endpoint_id, "EP-MOCK-PRIMARY")

    def test_page_pagination_records_exact_parameters_and_exhaustion(self) -> None:
        plan, adapter, proof = self.execute("pagination")
        self.assertEqual(
            proof.coverage_state,
            CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE,
        )
        self.assertEqual(adapter.transport_calls, 3)
        self.assertEqual([row.page_ordinal for row in proof.content_receipts], [1, 2, 3])
        self.assertEqual(
            [row.input_continuation_token for row in proof.content_receipts],
            ["1", "2", "3"],
        )
        self.assertEqual(
            [row.exact_request_parameters["page"] for row in proof.content_receipts],
            ["1", "2", "3"],
        )
        self.assertEqual(
            proof.reconciliation.source_reported_total,
            proof.reconciliation.normalized_record_count,
        )
        self.assertTrue(proof.reconciliation.continuation_exhausted)
        self.assertEqual(proof.query_plan.query_plan_id, plan.query_plan_id)
        self.assertTrue(all(row.request_sha256 and row.response_sha256 for row in proof.content_receipts))

    def test_cursor_traversal_has_gapless_content_and_seed_lineage(self) -> None:
        plan, _, proof = self.execute("cursor")
        self.assertEqual(len(proof.content_receipts), 2)
        self.assertIsNone(proof.content_receipts[0].input_continuation_token)
        self.assertEqual(proof.content_receipts[0].output_continuation_token, "CURSOR-2")
        self.assertEqual(proof.content_receipts[1].input_continuation_token, "CURSOR-2")
        self.assertEqual(proof.reconciliation.emitted_seed_count, 2)
        for emission in proof.seed_emissions:
            record = next(
                row
                for row in proof.normalized_records
                if row.normalized_record_id == emission.normalized_record_id
            )
            self.assertEqual(emission.discovery_route.query_id, plan.query_plan_id)
            self.assertEqual(
                emission.discovery_route.retrieval_content_receipt_id,
                record.retrieval_content_receipt_id,
            )

    def test_empty_results_are_bounded_not_self_asserted_exhaustive(self) -> None:
        _, _, proof = self.execute("empty")
        self.assertEqual(
            proof.coverage_state,
            CoverageState.NO_RELEVANT_HITS_WITHIN_DECLARED_QUERY,
        )
        self.assertEqual(proof.reconciliation.source_reported_total, 0)
        self.assertEqual(proof.reconciliation.normalized_record_count, 0)
        self.assertEqual(proof.reconciliation.screened_record_count, 0)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 0)
        self.assertTrue(proof.reconciliation.continuation_exhausted)

    def test_declared_source_bound_retains_unvisited_count_and_next_page(self) -> None:
        _, adapter, proof = self.execute("partial_source_limit")
        self.assertEqual(proof.coverage_state, CoverageState.PARTIAL_DUE_TO_SOURCE_LIMIT)
        self.assertEqual(adapter.transport_calls, 1)
        self.assertEqual(proof.reconciliation.source_reported_total, 2)
        self.assertEqual(proof.reconciliation.normalized_record_count, 1)
        self.assertEqual(proof.reconciliation.unvisited_record_count, 1)
        self.assertFalse(proof.reconciliation.continuation_exhausted)
        self.assertEqual(proof.reconciliation.next_continuation_token, "2")

    def test_malformed_response_is_failed_with_hashed_failure_accounting(self) -> None:
        _, _, proof = self.execute("malformed_response")
        self.assertEqual(proof.coverage_state, CoverageState.FAILED_RETRIEVAL)
        self.assertEqual(len(proof.content_receipts), 1)
        self.assertEqual(proof.content_receipts[0].receipt_status, "failed")
        self.assertTrue(proof.content_receipts[0].response_sha256)
        self.assertEqual(proof.reconciliation.source_reported_total, 1)
        self.assertEqual(proof.reconciliation.failed_record_count, 1)
        self.assertEqual(proof.reconciliation.unvisited_record_count, 0)
        self.assertEqual(len(proof.execution_receipts), 1)
        self.assertEqual(proof.execution_receipts[0].outcome, "contract_error")
        self.assertEqual(
            proof.execution_receipts[0].content_receipt_id,
            proof.content_receipts[0].content_receipt_id,
        )
        self.assertIn("records must be a list", proof.execution_receipts[0].error_message)

    def test_retry_and_rate_limit_metadata_are_execution_only(self) -> None:
        _, _, proof = self.execute("retry")
        self.assertEqual(
            proof.coverage_state,
            CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE,
        )
        self.assertEqual([row.attempt_number for row in proof.execution_receipts], [1, 2])
        self.assertEqual(
            [row.outcome for row in proof.execution_receipts],
            ["transport_error", "success"],
        )
        self.assertIsNotNone(proof.execution_receipts[1].rate_limit)
        self.assertEqual(len(proof.content_receipts), 1)

        _, _, partial = self.execute("rate_limit_partial")
        self.assertEqual(partial.coverage_state, CoverageState.PARTIAL_DUE_TO_RATE_LIMIT)
        self.assertEqual(
            [row.outcome for row in partial.execution_receipts],
            ["rate_limited", "rate_limited"],
        )
        self.assertTrue(all(row.rate_limit is not None for row in partial.execution_receipts))
        self.assertFalse(partial.reconciliation.continuation_exhausted)

    def test_cache_replay_preserves_content_and_changes_execution_trace(self) -> None:
        plan = self.catalog.build_plan("replay")
        with tempfile.TemporaryDirectory() as directory:
            cache = ContentAddressedRetrievalCache(directory)
            online = FrozenMockRetrievalAdapter("replay", catalog=self.catalog)
            first = execute_query_plan(
                self.case,
                plan,
                online,
                cache=cache,
                sleeper=lambda _: None,
                clock=DeterministicClock(0),
            )
            offline = FrozenMockRetrievalAdapter(
                "replay", catalog=self.catalog, transport_enabled=False
            )
            replay = execute_query_plan(
                self.case,
                plan,
                offline,
                cache=cache,
                replay_only=True,
                sleeper=lambda _: None,
                clock=DeterministicClock(100),
            )
        self.assertEqual(online.transport_calls, 1)
        self.assertEqual(offline.transport_calls, 0)
        self.assertEqual(first.coverage_proof_id, replay.coverage_proof_id)
        self.assertEqual(first.content_receipts, replay.content_receipts)
        self.assertEqual(first.seed_emissions, replay.seed_emissions)
        self.assertNotEqual(first.execution_trace_id, replay.execution_trace_id)
        self.assertFalse(first.execution_receipts[0].cache_hit)
        self.assertTrue(replay.execution_receipts[0].cache_hit)

    def test_missing_continuation_token_cannot_be_called_complete(self) -> None:
        _, _, proof = self.execute("omitted_continuation")
        self.assertEqual(proof.coverage_state, CoverageState.FAILED_RETRIEVAL)
        self.assertEqual(len(proof.content_receipts), 1)
        self.assertEqual(proof.content_receipts[0].receipt_status, "failed")
        self.assertTrue(proof.content_receipts[0].response_sha256)
        self.assertEqual(proof.reconciliation.source_reported_total, 2)
        self.assertEqual(proof.reconciliation.failed_record_count, 1)
        self.assertEqual(proof.reconciliation.unvisited_record_count, 1)
        self.assertIn("omitted", " ".join(proof.coverage_gaps).casefold())

    def test_unsupported_capability_and_not_yet_searched_are_explicit(self) -> None:
        plan = self.catalog.build_plan("cursor")
        unsupported = FrozenMockRetrievalAdapter(
            "cursor",
            catalog=self.catalog,
            capabilities=("normalized_seed_mapping",),
        )
        proof = execute_query_plan(
            self.case,
            plan,
            unsupported,
            sleeper=lambda _: None,
            clock=DeterministicClock(),
        )
        self.assertEqual(proof.coverage_state, CoverageState.UNSUPPORTED_SOURCE_CAPABILITY)
        self.assertEqual(proof.execution_receipts, ())

        planned = not_yet_searched_proof(unsupported.descriptor, plan)
        self.assertEqual(planned.coverage_state, CoverageState.NOT_YET_SEARCHED)
        self.assertEqual(planned.execution_receipts, ())

    def test_mechanical_reconciliation_rejects_worker_tampering(self) -> None:
        _, _, proof = self.execute("pagination")
        cases = (
            replace(
                proof,
                reconciliation=replace(
                    proof.reconciliation,
                    normalized_record_count=proof.reconciliation.normalized_record_count + 1,
                ),
            ),
            replace(proof, screening_dispositions=proof.screening_dispositions[:-1]),
            replace(
                proof,
                reconciliation=replace(
                    proof.reconciliation,
                    continuation_exhausted=False,
                    next_continuation_token="HIDDEN-CURSOR",
                ),
            ),
            replace(
                proof,
                content_receipts=(
                    replace(
                        proof.content_receipts[0],
                        provider_total=999,
                    ),
                )
                + proof.content_receipts[1:],
            ),
        )
        for tampered in cases:
            with self.subTest(tampered=tampered):
                with self.assertRaises(RetrievalContractError):
                    validate_coverage_proof(self.case, tampered)

    def test_receipt_cannot_be_relabelled_as_an_unrelated_query_family(self) -> None:
        plan, _, proof = self.execute("pagination")
        unrelated = self.catalog.build_plan(
            "pagination", query_family_id="unrelated_query_family"
        )
        self.assertNotEqual(plan.query_plan_id, unrelated.query_plan_id)
        tampered = replace(proof, query_plan=unrelated)
        with self.assertRaisesRegex(RetrievalContractError, "relabeled"):
            validate_coverage_proof(self.case, tampered)

    def test_legitimate_query_overlap_adds_routes_without_seed_inflation(self) -> None:
        plan_a = self.catalog.build_plan("pagination")
        plan_b = self.catalog.build_plan(
            "pagination", query_family_id="declared_overlap_family"
        )
        proof_a = execute_query_plan(
            self.case,
            plan_a,
            FrozenMockRetrievalAdapter("pagination", catalog=self.catalog),
            sleeper=lambda _: None,
            clock=DeterministicClock(0),
        )
        proof_b = execute_query_plan(
            self.case,
            plan_b,
            FrozenMockRetrievalAdapter("pagination", catalog=self.catalog),
            sleeper=lambda _: None,
            clock=DeterministicClock(100),
        )
        bundle = combine_coverage_proofs(self.case, (proof_a, proof_b))
        self.assertEqual(len(bundle.source_mappings), 3)
        self.assertEqual(len(bundle.seeds), 3)
        self.assertEqual(len(bundle.discovery_routes), 6)
        self.assertTrue(all(len(seed.discovery_route_ids) == 2 for seed in bundle.seeds))

    def test_one_thousand_records_emit_one_thousand_stable_seeds(self) -> None:
        _, adapter, proof = self.execute("thousand_seed_emission")
        self.assertEqual(adapter.transport_calls, 4)
        self.assertEqual(
            proof.coverage_state,
            CoverageState.COMPLETE_FOR_DECLARED_QUERY_AND_RELEASE,
        )
        self.assertEqual(proof.reconciliation.source_reported_total, 1000)
        self.assertEqual(proof.reconciliation.returned_native_record_count, 1000)
        self.assertEqual(proof.reconciliation.normalized_record_count, 1000)
        self.assertEqual(proof.reconciliation.screened_record_count, 1000)
        self.assertEqual(proof.reconciliation.emitted_seed_count, 1000)
        self.assertEqual(len({row.seed.seed_id for row in proof.seed_emissions}), 1000)


if __name__ == "__main__":
    unittest.main(verbosity=2)

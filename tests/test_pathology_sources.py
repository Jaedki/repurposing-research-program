import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import pathology_sources as sources  # noqa: E402
import program_core as core  # noqa: E402


class DisMechNormalizationTest(unittest.TestCase):
    def test_dismech_receipt_hashes_every_cached_source_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "sources" / "raw"
            cache.mkdir(parents=True)
            commit_path = cache / "dismech_commit.json"
            export_path = cache / "dismech_mondo_emc.tsv"
            yaml_path = cache / "dismech_example.yaml"
            commit_path.write_text('{"sha":"abc123"}', encoding="utf-8")
            export_path.write_text("mondo_id\tdismech_url\n", encoding="utf-8")
            yaml_path.write_text("name: Example disease\n", encoding="utf-8")
            metadata = {
                "commit_sha": "abc123",
                "page": "https://example.org/example.html",
                "slug": "example",
                "yaml_path": yaml_path,
            }

            with patch.object(
                sources,
                "_load_dismech",
                return_value=({"name": "Example disease"}, metadata, []),
            ):
                *_, receipt, _ = sources._dismech(cache, "MONDO:1", {})

            self.assertEqual(
                {row["path"] for row in receipt["raw_files"]},
                {
                    "sources/raw/dismech_commit.json",
                    "sources/raw/dismech_mondo_emc.tsv",
                    "sources/raw/dismech_example.yaml",
                },
            )
            for row in receipt["raw_files"]:
                path = Path(directory) / row["path"]
                self.assertEqual(row["sha256"], sources._sha256(path.read_bytes()))

    def test_raw_cache_paths_do_not_expose_run_directory_language(self):
        path = Path("C:/repurposing package/a-run/sources/raw/dismech_Disease.yaml")

        relative = sources._raw_cache_path(path)

        self.assertEqual(relative, "sources/raw/dismech_Disease.yaml")
        self.assertEqual(
            sources._flagged_sentences({"raw_path": relative}, set()),
            [],
        )

    def test_every_safe_section_is_projected_without_treatment_text(self):
        raw = {
            "name": "Example disease",
            "description": "A progressive neurological disease.",
            "mechanistic_hypotheses": [
                {
                    "hypothesis_label": "Proteostasis failure",
                    "description": (
                        "Protein aggregation injures neurons. "
                        "Riluzole therapy was tested in a clinical trial."
                    ),
                    "evidence": [{"reference": "PMID:1", "supports": "SUPPORT"}],
                }
            ],
            "phenotypes": [
                {
                    "name": "Weakness",
                    "phenotype_term": {
                        "term": {"id": "HP:0001324", "label": "Muscle weakness"}
                    },
                }
            ],
            "inheritance": {"name": "Complex inheritance"},
            "progression": {"description": "Progressive loss of function."},
            "animal_models": [{"species": "Mouse", "description": "Disease model."}],
            "treatments": [{"name": "Riluzole", "description": "Approved treatment."}],
            "clinical_trials": [{"name": "Riluzole phase 3 trial"}],
        }

        terms = sources._treatment_terms(raw)
        decisions = {
            row["sentence_id"]: "exclude_treatment"
            for row in sources._flagged_sentences(raw, terms)
        }
        sanitized = sources._pathology_only(raw, terms, decisions)
        documents, nodes, edges, contexts, gaps = sources._normalize_dismech_sections(
            sanitized, "MONDO:1", "DISMECH-FILE-TEST"
        )

        self.assertNotIn("treatments", sanitized)
        self.assertNotIn("clinical_trials", sanitized)
        hypothesis = sanitized["mechanistic_hypotheses"][0]
        self.assertEqual(hypothesis["description"], "Protein aggregation injures neurons.")
        self.assertEqual({row["source_section"] for row in nodes}, {
            "mechanistic_hypotheses", "phenotypes"
        })
        self.assertTrue(all(row["node_id"].startswith("DISMECH-NODE-") for row in nodes))
        phenotype = next(row for row in nodes if row["label"] == "Weakness")
        self.assertIn("HP:0001324", json.dumps(phenotype["source_payloads"]))
        projected_sections = {
            row["source_section"] for row in nodes
        } | {row["section"] for row in contexts}
        self.assertEqual(projected_sections, set(sanitized))
        self.assertEqual(len(documents), 1)
        self.assertEqual(edges, [])
        self.assertEqual(gaps, [])
        self.assertEqual(
            sources._unapproved_flagged_paths(
                {"nodes": nodes, "contexts": contexts}, terms, decisions
            ),
            [],
        )

    def test_named_intervention_is_removed_without_discarding_biology(self):
        raw = {
            "pathophysiology": [
                {
                    "name": "Excitotoxicity",
                    "description": (
                        "Riluzole suppresses glutamate release. "
                        "Excess glutamate can injure motor neurons."
                    ),
                }
            ],
            "genetic": [{"name": "SOD1 Mutations"}],
            "treatments": [
                {
                    "name": "Riluzole",
                    "aso_details": {
                        "target_gene": {
                            "preferred_term": "SOD1",
                            "term": {"id": "HGNC:11179", "label": "SOD1"},
                        }
                    },
                    "treatment_term": {
                        "therapeutic_agent": [{"preferred_term": "Riluzole"}]
                    },
                }
            ],
        }

        terms = sources._treatment_terms(raw)
        decisions = {
            row["sentence_id"]: "exclude_treatment"
            for row in sources._flagged_sentences(raw, terms)
        }
        sanitized = sources._pathology_only(raw, terms, decisions)

        self.assertEqual(terms, {"Riluzole"})
        self.assertEqual(
            sanitized["pathophysiology"][0]["description"],
            "Excess glutamate can injure motor neurons.",
        )
        self.assertEqual(sanitized["genetic"][0]["name"], "SOD1 Mutations")

    def test_unlisted_intervention_phrasing_is_removed(self):
        pathology = "Protein aggregation injures neurons."
        intervention_sentences = (
            "Patients on riluzole showed improved survival.",
            "Patients receiving edaravone showed slower functional decline.",
            "Following tofersen administration, motor scores improved.",
        )

        for sentence in intervention_sentences:
            with self.subTest(sentence=sentence):
                self.assertTrue(sources._treatment_text(sentence, set()))
                self.assertEqual(
                    sources._sanitize_text(
                        f"{pathology} {sentence}",
                        set(),
                        {sources._sentence_id(sentence): "exclude_treatment"},
                    ),
                    pathology,
                )

        causal_pathology = (
            "Compensating for HK1 loss improves motor performance in disease models.",
            "Restoring STMN2 rescued axonal regeneration in motor neurons.",
            "Patients with bulbar-onset disease showed faster functional decline.",
        )
        for sentence in causal_pathology:
            with self.subTest(sentence=sentence):
                self.assertFalse(sources._treatment_text(sentence, set()))

    def test_adjudication_can_restore_a_false_positive_without_rewriting(self):
        sentence = "The approved HGNC symbol is SOD1."
        raw = {
            "genetic": [
                {"name": "SOD1", "description": sentence},
                {"name": "SOD1 metadata", "notes": sentence},
            ]
        }
        flagged = sources._flagged_sentences(raw, set())

        self.assertEqual([row["sentence"] for row in flagged], [sentence])
        self.assertEqual(len(flagged[0]["paths"]), 2)
        with self.assertRaisesRegex(sources.SourceError, "Missing valid adjudication"):
            sources._pathology_only(raw, set(), {})
        sanitized = sources._pathology_only(
            raw,
            set(),
            {flagged[0]["sentence_id"]: "retain_pathology"},
        )

        self.assertEqual(sanitized["genetic"][0]["description"], sentence)
        self.assertEqual(sanitized["genetic"][1]["notes"], sentence)

    def test_screen_and_adjudication_reduce_errors_on_labeled_sentences(self):
        samples = (
            ("Patients on riluzole showed improved survival.", False),
            ("Patients receiving edaravone showed slower decline.", False),
            ("Following tofersen administration, motor scores improved.", False),
            ("The approved HGNC symbol is SOD1.", True),
            ("Restoring STMN2 rescued axonal regeneration in motor neurons.", True),
        )
        legacy_predictions = [
            not bool(sources._TREATMENT_TEXT.search(sentence))
            for sentence, _ in samples
        ]
        decisions = {
            sources._sentence_id(sentence): (
                "retain_pathology" if should_retain else "exclude_treatment"
            )
            for sentence, should_retain in samples
            if sources._treatment_text(sentence, set())
        }
        hybrid_predictions = [
            (
                decisions[sources._sentence_id(sentence)] == "retain_pathology"
                if sources._treatment_text(sentence, set())
                else True
            )
            for sentence, _ in samples
        ]
        expected = [should_retain for _, should_retain in samples]

        legacy_errors = sum(
            actual != wanted
            for actual, wanted in zip(legacy_predictions, expected)
        )
        hybrid_errors = sum(
            actual != wanted
            for actual, wanted in zip(hybrid_predictions, expected)
        )
        self.assertEqual(legacy_errors, 4)
        self.assertEqual(hybrid_errors, 0)

    def test_explicit_intervention_name_supplies_a_bounded_acronym_alias(self):
        raw = {
            "description": (
                "Axonal injury progresses. Early HSCT altered outcomes. "
                "Outcomes following transplantation improved."
            ),
            "treatments": [{"name": "Hematopoietic stem cell transplantation"}],
        }

        terms = sources._treatment_terms(raw)
        decisions = {
            row["sentence_id"]: "exclude_treatment"
            for row in sources._flagged_sentences(raw, terms)
        }
        sanitized = sources._pathology_only(raw, terms, decisions)

        self.assertEqual(
            terms, {"Hematopoietic stem cell transplantation", "HSCT"}
        )
        self.assertEqual(sanitized["description"], "Axonal injury progresses.")
        self.assertFalse(sources._treatment_text("Inert aggregates persist.", {"ERT"}))


class ALSRegressionTest(unittest.TestCase):
    run_root = (
        Path.home()
        / "OneDrive"
        / "Documents"
        / "repurposing package"
        / "als-repurposing-program"
    )
    raw_dir = run_root / "sources" / "raw"
    yaml_path = raw_dir / "dismech_Amyotrophic_Lateral_Sclerosis.yaml"

    @classmethod
    def setUpClass(cls):
        required = [
            cls.raw_dir / name
            for name in (
                "dismech_commit.json",
                "dismech_mondo_emc.tsv",
                cls.yaml_path.name,
                "monarch_associations_0000.json",
                "monarch_entity.json",
                "monarch_version.json",
            )
        ]
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("completed ALS raw artifacts are not available")
        cls.before = {path: path.stat().st_mtime_ns for path in required}
        cls.raw = yaml.safe_load(cls.yaml_path.read_text(encoding="utf-8-sig"))
        screening = sources.screen_pathology_sources(
            cls.run_root, "amyotrophic lateral sclerosis", "MONDO:0004976"
        )
        cls.decisions = {
            row["sentence_id"]: "exclude_treatment"
            for row in screening["records"]["flagged_sentences"]
        }
        cls.result = sources.fetch_pathology_sources(
            cls.run_root,
            "amyotrophic lateral sclerosis",
            "MONDO:0004976",
            cls.decisions,
        )
        cls.after = {path: path.stat().st_mtime_ns for path in required}
        cls.nodes = cls.result["records"]["source_nodes"]

    def test_completed_raw_artifacts_parse_from_existing_package(self):
        self.assertEqual(self.result["status"], "complete")
        self.assertEqual(self.before, self.after)

    def test_normalized_sources_pass_controller_validation_without_duplicate_counts(self):
        core._validate_source_result(self.result)
        dismech_receipt = next(
            row
            for row in self.result["records"]["source_receipts"]
            if row["source"] == "dismech"
        )
        self.assertNotIn("sentence_adjudication", dismech_receipt)

    def test_sod1_label_survives_without_fallback_node(self):
        self.assertEqual(
            [row["label"] for row in self.nodes if row["label"] == "SOD1 Mutations"],
            ["SOD1 Mutations"],
        )
        self.assertNotIn("Genetic 2", {row["label"] for row in self.nodes})

    def test_c9orf72_claim_uses_generated_id_and_retains_hgnc_metadata(self):
        node = next(
            row
            for row in self.nodes
            if row["label"] == "C9orf72 Repeat Expansion Toxicity"
        )
        self.assertTrue(node["node_id"].startswith("DISMECH-NODE-"))
        self.assertIn("HGNC:28337", json.dumps(node["source_payloads"]).upper())

    def test_treatment_language_cannot_enter_emitted_pathology(self):
        records = self.result["records"]
        emitted = {
            key: records[key]
            for key in ("documents", "source_nodes", "source_edges", "disease_context")
        }
        self.assertEqual(
            sources._unapproved_flagged_paths(
                emitted,
                sources._treatment_terms(self.raw),
                self.decisions,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()

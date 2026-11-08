"""No blind text: all identities and digests below are synthetic."""

import hashlib
import unittest

from rag_evaluation.dataset import audit_dataset
from rag_evaluation.models import ArtifactRef, CaseMetadata, Dataset, ExposureEvent, SourceAsset


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def reference(name, restricted=False):
    return ArtifactRef(root_id="vault" if restricted else "open", path=name,
                       sha256=digest(name), size_bytes=10,
                       visibility="restricted" if restricted else "public")


def source(name, family=None, **changes):
    return SourceAsset(**({
        "id": name, "family_id": family or name,
        "artifact": reference(name + ".txt", True),
        "permission_evidence": reference("permission.txt"),
    } | changes))


def case(name, source_id, partition="H1", **changes):
    return CaseMetadata(**({
        "id": name, "partition": partition, "source_ids": [source_id],
        "task_family_id": name, "question_sha256": digest(name),
        "question_ref": reference(name + "-question.json", True),
        "gold_ref": reference(name + "-gold.json", True),
        "answerability": "answerable", "independence": "audited_unexposed",
        "analysis_stratum_id": "all",
        "independence_evidence": reference("independence.txt"),
    } | changes))


def dataset(sources=None, cases=None, **changes):
    return Dataset(**({
        "id": "test-catalog", "revision": 1,
        "sources": sources or [source("s-one")],
        "cases": cases or [case("c-one", "s-one")],
        "exposure_coverage": "audited", "exposure_audit": reference("exposure-audit.txt"),
    } | changes))


def codes(result):
    return {row["code"] for row in result["issues"]}


class DatasetGovernanceTests(unittest.TestCase):
    def test_independent_metadata_reports_actual_source_group_count(self):
        report = audit_dataset(dataset(), [], phase="H1")
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["source_group_count"], 1)
        self.assertEqual(report["case_count"], 1)

    def test_unreferenced_historical_source_does_not_require_current_run_permission(self):
        catalog = dataset(
            [source("s-one"), source("historical-summary", permission_evidence=None)],
            [case("c-one", "s-one")],
        )

        self.assertNotIn("source_permission_missing", codes(
            audit_dataset(catalog, [], phase="H1")))

    def test_same_source_family_or_raw_digest_cannot_cross_splits(self):
        for other in (source("s-two", family="s-one"),
                      source("s-two", artifact=source("s-one").artifact)):
            with self.subTest(other=other.id):
                catalog = dataset([source("s-one"), other],
                                  [case("dev", "s-one", "development"), case("blind", "s-two")])
                self.assertIn("cross_partition_group", codes(audit_dataset(catalog, [], phase="H1")))

    def test_derived_format_and_multidocument_task_connect_related_sources(self):
        sources = [source("original"), source("derived", derived_from=["original"]), source("third")]
        cases = [case("dev", "original", "development"),
                 case("blind", "derived", source_ids=["derived", "third"]),
                 case("later", "third", "H2")]
        self.assertIn("cross_partition_group", codes(audit_dataset(dataset(sources, cases), [], phase="H1")))

    def test_rewritten_id_cannot_clear_prior_developer_exposure(self):
        original = source("original")
        renamed = source("renamed", family="new-family", artifact=original.artifact)
        event = ExposureEvent(id="known", revision=1, actor_id="developer", actor_role="developer",
                              occurred_at="2026-11-05T14:00:00+08:00",
                              access="source", asset_sha256=[original.artifact.sha256])
        report = audit_dataset(dataset([renamed], [case("new-question", "renamed")]), [event], phase="H1")
        self.assertIn("exposed_blind_group", codes(report))

    def test_history_and_duplicate_question_fingerprints_prevent_repartition_laundering(self):
        old = dataset(cases=[case("old", "s-one", "development")])
        new_source = source("new-source", family="new-family")
        new = dataset([new_source], [case("renamed", "new-source", question_sha256=digest("old"))],
                      revision=2)
        report = audit_dataset(new, [], phase="H1", history=[old])
        self.assertIn("cross_partition_group", codes(report))

    def test_duplicate_missing_source_and_unknown_independence_are_explicit(self):
        catalog = dataset(cases=[
            case("same", "missing", independence="unknown"),
            case("same", "s-one"),
        ])
        report = audit_dataset(catalog, [], phase="H1")
        self.assertTrue({"duplicate_case_id", "unknown_source", "independence_unverified"} <= codes(report))

    def test_empty_or_unreviewed_catalog_is_not_a_blind_dataset(self):
        catalog = Dataset(id="empty", revision=1)
        self.assertTrue({"no_cases_for_phase", "exposure_history_unknown"} <=
                        codes(audit_dataset(catalog, [], phase="H1")))

    def test_custodian_labeling_is_not_confused_with_developer_tuning(self):
        event = ExposureEvent(id="labeling", revision=1, actor_id="custodian",
                              actor_role="custodian", occurred_at="2026-11-05T14:00:00+08:00",
                              access="gold", case_ids=["c-one"])
        self.assertNotIn("exposed_blind_group",
                         codes(audit_dataset(dataset(), [event], phase="H1")))

    def test_historical_exposed_label_survives_renaming_without_an_event(self):
        old = dataset(cases=[case("old", "s-one", independence="exposed")])
        renamed = dataset(cases=[case("new", "s-one")], revision=2)
        self.assertIn("exposed_blind_group",
                      codes(audit_dataset(renamed, [], phase="H1", history=[old])))

    def test_related_source_group_cannot_span_analysis_strata(self):
        catalog = dataset(cases=[
            case("first", "s-one", analysis_stratum_id="technical"),
            case("second", "s-one", analysis_stratum_id="business"),
        ])

        self.assertIn("cross_analysis_stratum_group",
                      codes(audit_dataset(catalog, [], phase="H1")))


if __name__ == "__main__":
    unittest.main()

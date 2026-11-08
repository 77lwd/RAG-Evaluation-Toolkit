"""Contracts accept pending evidence but never silently coerce it to approval."""

import unittest

from pydantic import ValidationError

from rag_evaluation.models import (
    Approval, Budget, DataEndpoint, Dataset, MetricGate, Protocol, Snapshot, parse_record,
    SourceAsset,
)


class GovernanceModelTests(unittest.TestCase):
    def test_pending_snapshot_is_explicit_and_does_not_become_production(self):
        snapshot = Snapshot(id="current-project", revision=1, arm="A",
                            identity="local_reference", root_id="project")
        self.assertFalse(snapshot.runtime_verified)
        self.assertEqual(snapshot.materials, {})
        self.assertEqual(parse_record(snapshot.model_dump(mode="json")), snapshot)

    def test_protocol_requires_exact_references_and_unapproved_values_stay_pending(self):
        protocol = Protocol(id="H1-proposal", revision=1, phase="H1",
                            experiment="production_replay")
        self.assertIsNone(protocol.dataset)
        self.assertEqual(protocol.snapshots, {})
        self.assertIsNone(protocol.thresholds.primary_gain)
        self.assertEqual(protocol.people.reviewers, [])

    def test_blind_metadata_cannot_accept_question_or_gold_body(self):
        with self.assertRaises(ValidationError):
            Dataset(id="untrusted", revision=1, cases=[{
                "id": "one", "partition": "H1", "source_ids": ["source"],
                "task_family_id": "family", "question": "secret blind question",
            }])

    def test_approval_requires_evidence_and_exact_target_not_an_approved_boolean(self):
        with self.assertRaises(ValidationError):
            Approval(id="bad", revision=1, scope="quality", actor_id="owner",
                     decision="approved", approved=True)

    def test_snapshot_categories_are_allowlisted_and_no_unknown_credentials_are_saved(self):
        with self.assertRaises(ValidationError):
            Snapshot(id="bad", revision=1, arm="B", identity="candidate", root_id="candidate",
                     materials={"api_key": []})
        with self.assertRaises(ValidationError):
            Snapshot(id="bad", revision=1, arm="B", identity="candidate", root_id="candidate",
                     runtime_verified="true")

    def test_metric_gate_rejects_undefined_na_policy(self):
        with self.assertRaises(ValidationError):
            MetricGate(
                unit="rate", numerator_definition="supported claims",
                denominator_definition="all claims", scorer="human_dual_blind",
                aggregation="per_case_macro", na_policy="ignore_missing",
                decision="lower_bound_noninferiority", threshold_ref="citation_support_loss",
            )

    def test_run_controls_reject_nonpositive_call_caps_and_unknown_stop_conditions(self):
        with self.assertRaises(ValidationError):
            Budget(max_model_calls=0)
        with self.assertRaises(ValidationError):
            Budget(stop_conditions=["retry_until_success"])

    def test_legacy_source_permission_defaults_to_unknown_not_approved(self):
        source = SourceAsset(
            id="source-one", family_id="family-one",
            artifact={"root_id": "vault", "path": "source.txt",
                      "sha256": "a" * 64, "size_bytes": 1, "visibility": "restricted"},
        )

        self.assertEqual(source.permission_status, "unknown")
        self.assertEqual(source.permitted_purposes, [])
        self.assertIsNone(source.retention_policy)

    def test_endpoint_identity_requires_known_processing_kind(self):
        with self.assertRaises(ValidationError):
            DataEndpoint(id="service", kind="unknown", operator="provider", region="test",
                         version="fixture-v1")
        with self.assertRaises(ValidationError):
            DataEndpoint(id="service", kind="processing_service", operator="provider",
                         region="test")


if __name__ == "__main__":
    unittest.main()

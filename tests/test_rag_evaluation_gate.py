"""Admission tests use fictitious identities and open synthetic receipts only."""

import tempfile
import unittest
from pathlib import Path

from rag_evaluation.artifacts import digest_json, fingerprint
from rag_evaluation.gate import MATERIALS, check_protocol, record_ref
from rag_evaluation.models import Approval, DataEndpoint, Snapshot, SourceProcessingPath
from rag_evaluation.registry import Registry
from rag_evaluation_fixtures import access, people, protocol
from test_rag_evaluation_dataset import case, dataset, reference, source


class AdmissionFixture:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / "receipt.txt").write_bytes(b"Open synthetic fixture; no real approval.")
        self.receipt = fingerprint(self.root, "receipt.txt", root_id="open")
        self.roots = {"open": self.root}
        self.registry = Registry.initialize(self.root / "journal")
        self.catalog = dataset(
            [source("s-one", permission_evidence=self.receipt, permission_status="active",
                    permission_subject="fixture-owner", permitted_purposes=["rag_evaluation"],
                    prohibited_purposes=["redistribution"], retention_policy="delete-after-review",
                    permitted_service_ids=["embed", "generate"],
                    permitted_storage_ids=["corpus-store", "index-store"],
                    permitted_transfer_ids=["model-provider"]),
             source("s-two", permission_evidence=self.receipt, permission_status="active",
                    permission_subject="fixture-owner", permitted_purposes=["rag_evaluation"],
                    prohibited_purposes=["redistribution"], retention_policy="delete-after-review",
                    permitted_service_ids=["embed", "generate"],
                    permitted_storage_ids=["corpus-store", "index-store"],
                    permitted_transfer_ids=["model-provider"])],
            [case("c-one", "s-one", independence_evidence=self.receipt, tags=["single"]),
             case("c-two", "s-two", independence_evidence=self.receipt,
                  answerability="insufficient_source"),
             case("c-three", "s-two", independence_evidence=self.receipt,
                  answerability="partial"),
             case("c-four", "s-two", independence_evidence=self.receipt,
                  answerability="ambiguous")],
            exposure_audit=self.receipt,
        )
        self.registry.append(self.catalog)
        self.snapshots = {}
        for arm, identity in (("A", "production"), ("B", "candidate")):
            snapshot = Snapshot(
                id=f"snapshot-{arm}", revision=1, arm=arm, identity=identity,
                root_id="open", git_commit="a" * 40, git_branch="fixture",
                dirty=False, runtime_verified=True,
                materials={name: [self.receipt] for name in (
                    "code", "effective_config", "embedding", "generation", "prompts",
                    "corpus", "index", "ingestion", "environment", "runtime",
                    "application", "retrieval", "index_builder", "data_import",
                    "request_runner", "retry_recovery", "metric_aggregation",
                    "statistical_analysis", "report_generation",
                )},
            )
            self.snapshots[arm] = snapshot
            self.registry.append(snapshot)
        roles = people()
        permissions = access(roles)
        permissions.verification_receipt = self.receipt
        permissions.data_flow_verified = True
        permissions.data_flow_evidence = self.receipt
        permissions.endpoints = [
            DataEndpoint(id="embed", kind="processing_service", operator="fixture",
                         region="test", version="fixture-v1"),
            DataEndpoint(id="generate", kind="processing_service", operator="fixture",
                         region="test", version="fixture-v1"),
            DataEndpoint(id="corpus-store", kind="storage", operator="fixture",
                         region="test", version="fixture-v1"),
            DataEndpoint(id="index-store", kind="storage", operator="fixture",
                         region="test", version="fixture-v1"),
            DataEndpoint(id="model-provider", kind="transfer", operator="fixture",
                         region="test", version="fixture-v1"),
        ]
        permissions.source_paths = [SourceProcessingPath(
            source_id=item.id, service_ids=["embed", "generate"],
            storage_ids=["corpus-store", "index-store"],
            transfer_ids=["model-provider"],
        ) for item in self.catalog.sources]
        permissions.verified_restricted = [
            *[item.artifact for item in self.catalog.sources],
            *[item.question_ref for item in self.catalog.cases],
            *[item.gold_ref for item in self.catalog.cases],
            *[agent.isolation_receipt for agent in roles.agents
              if agent.isolation_receipt is not None],
            *[agent.calibration_receipt for agent in roles.agents
              if agent.calibration_receipt is not None],
        ]
        self.plan = protocol(
            dataset=record_ref(self.catalog),
            snapshots={arm: record_ref(item) for arm, item in self.snapshots.items()},
            access=permissions, known_results_disclosure="none",
            rules={name: self.receipt for name in (
                "scope_and_sampling", "split_policy", "gold_rubric",
                "metrics_and_statistics", "run_plan", "review_plan", "serious_errors",
                "sample_size_justification", "retention_and_recovery",
            )},
            thresholds=dict(primary_gain=.05, correct_refusal_loss=.03,
                            false_refusal_increase=.03, citation_support_loss=.02,
                            important_claim_coverage_threshold=.90,
                            p95_ratio_max=1.25, mean_cost_ratio_max=1.30),
            budget=dict(monetary_limit="bounded", absolute_p95_seconds=30.,
                        per_task_cost=.1, total_cost=10.,
                        currency="CNY", memory_mb=2048, concurrency=1,
                        task_timeout_seconds=60., retries=0, max_model_calls=100,
                        stop_conditions=[
                            "suspected_privacy_or_unauthorized_access",
                            "unauthorized_data_transfer", "evidence_integrity_failure",
                            "runtime_identity_mismatch", "resource_limit_exceeded",
                            "max_model_calls_reached", "manual_emergency_stop",
                        ]),
            analysis=dict(planned_cases=4, planned_source_groups=2, target_power=.8,
                          required_tags={"single": 1},
                          strata={"all": dict(planned_cases=4, planned_source_groups=2)},
                          metric_contract_version="v0.5",
                          metric_gates={
                              "primary_answer": dict(
                                  unit="rate", numerator_definition="complete supported answers",
                                  denominator_definition="answerable tasks",
                                  scorer="agent_dual_blind", aggregation="weighted_case_mean",
                                  na_policy="not_allowed", decision="primary_gain",
                                  threshold_ref="primary_gain"),
                              "correct_refusal": dict(
                                  unit="rate", numerator_definition="correct refusals",
                                  denominator_definition="insufficient-source tasks",
                                  scorer="agent_dual_blind", aggregation="weighted_case_mean",
                                  na_policy="not_allowed", decision="lower_bound_noninferiority",
                                  threshold_ref="correct_refusal_loss"),
                              "false_refusal": dict(
                                  unit="rate", numerator_definition="false refusals",
                                  denominator_definition="answerable tasks",
                                  scorer="agent_dual_blind", aggregation="weighted_case_mean",
                                  na_policy="not_allowed", decision="upper_bound_noninferiority",
                                  threshold_ref="false_refusal_increase"),
                              "citation_support": dict(
                                  unit="rate", numerator_definition="supported cited claims",
                                  denominator_definition="cited factual claims",
                                  scorer="agent_dual_blind", aggregation="per_case_macro",
                                  na_policy="not_applicable_reported",
                                  decision="lower_bound_noninferiority",
                                  threshold_ref="citation_support_loss"),
                              "important_claim_coverage": dict(
                                  unit="rate", numerator_definition="supported important claims",
                                  denominator_definition="all important claims",
                                  scorer="agent_dual_blind", aggregation="per_case_macro",
                                  na_policy="not_applicable_reported",
                                  decision="lower_bound_noninferiority",
                                  threshold_ref="important_claim_coverage_threshold"),
                              "p95_latency": dict(
                                  unit="seconds", numerator_definition=None,
                                  denominator_definition="all planned tasks",
                                  scorer="automatic", aggregation="all_planned_task_p95",
                                  na_policy="not_allowed", decision="ratio_max",
                                  threshold_ref="p95_ratio_max"),
                              "per_task_cost": dict(
                                  unit="currency", numerator_definition="total task cost",
                                  denominator_definition="all planned tasks",
                                  scorer="automatic", aggregation="mean_per_task_all_planned",
                                  na_policy="not_allowed", decision="ratio_max",
                                  threshold_ref="mean_cost_ratio_max"),
                              "serious_errors": dict(
                                  unit="count", numerator_definition="adjudicated serious events",
                                  denominator_definition="all protocol-defined audit opportunities",
                                  scorer="adjudicated", aggregation="adjudicated_event_count",
                                  na_policy="not_allowed", decision="zero_tolerance",
                                  threshold_ref=None),
                          },
                          planned_answerability=dict(answerable=1, insufficient_source=1,
                                                     partial=1, ambiguous=1)),
        )

    def register(self, approvals=True):
        self.registry.append(self.plan)
        if approvals:
            for scope in ("scope", "quality", "budget", "roles", "access", "sampling", "protocol"):
                actor = "auditor" if scope == "access" else "owner"
                self.approve(record_ref(self.plan), scope, actor)
            for snapshot in self.snapshots.values():
                self.approve(record_ref(snapshot), "identity", "auditor")

    def approve(self, target, scope, actor, **changes):
        entry = Approval(**({
            "id": f"approval-{len(self.registry.entries())}", "revision": 1,
            "target": target, "scope": scope, "actor_id": actor, "actor_type": "human",
            "occurred_at": "2026-11-05T15:00:00+08:00", "decision": "approved",
            "evidence": self.receipt,
        } | changes))
        self.registry.append(entry)

    def check(self, **kwargs):
        return check_protocol(self.registry, record_ref(self.plan), self.roots, **kwargs)


def codes(report):
    return {item["code"] for item in report["issues"]}


class ProtocolAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rag-admission-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = AdmissionFixture(self.temp.name)

    def test_complete_metadata_only_requests_custodian_review_never_run_permission(self):
        self.fixture.register()
        report = self.fixture.check()
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["status"], "ready_for_custodian_review")
        self.assertFalse(report["run_authorized"])

    def test_snapshot_material_contract_names_v06_execution_components(self):
        required = {
            "application", "retrieval", "index_builder", "data_import",
            "request_runner", "retry_recovery", "metric_aggregation",
            "statistical_analysis", "report_generation",
        }
        self.assertLessEqual(required, MATERIALS)

    def test_missing_business_approval_is_not_replaced_by_filled_thresholds(self):
        self.fixture.register(approvals=False)
        report = self.fixture.check()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("approval_missing", codes(report))

    def test_missing_important_claim_coverage_gate_is_reported(self):
        self.fixture.plan.thresholds = self.fixture.plan.thresholds.model_copy(
            update={"important_claim_coverage_threshold": None}
        )
        self.fixture.register()
        report = self.fixture.check()

        self.assertIn(
            {"code": "threshold_missing",
             "location": "thresholds.important_claim_coverage_threshold"},
            report["issues"],
        )

    def test_local_reference_cannot_pass_formal_admission(self):
        snapshot = self.fixture.snapshots["A"]
        snapshot.identity = "local_reference"
        snapshot.revision = 2
        self.fixture.registry.append(snapshot)
        self.fixture.plan.snapshots["A"] = record_ref(snapshot)
        self.fixture.plan.experiment = "local_reference_replay"
        self.fixture.register()
        formal = self.fixture.check()
        self.assertIn("local_reference_not_production", codes(formal))
        self.assertEqual(formal["conclusion_scope"], "local_reference_only")
        local = self.fixture.check(mode="local")
        self.assertEqual(local["status"], "local_validation_only")
        self.assertEqual(local["conclusion_scope"], "local_reference_only")

    def test_local_reference_cannot_be_mislabeled_as_production_replay(self):
        snapshot = self.fixture.snapshots["A"]
        snapshot.identity = "local_reference"
        snapshot.revision = 2
        self.fixture.registry.append(snapshot)
        self.fixture.plan.snapshots["A"] = record_ref(snapshot)
        self.fixture.register()
        report = self.fixture.check(mode="local")
        self.assertIn("experiment_scope_mismatch", codes(report))
        self.assertEqual(report["conclusion_scope"], "invalid_scope_mismatch")
        self.assertEqual(report["status"], "blocked")

    def test_wrong_hash_and_wrong_kind_are_rejected(self):
        self.fixture.plan.dataset = record_ref(self.fixture.catalog).model_copy(
            update={"sha256": "0" * 64})
        self.fixture.register()
        self.assertIn("reference_unresolved", codes(self.fixture.check()))

    def test_changed_public_artifact_or_unattested_restricted_artifact_blocks(self):
        self.fixture.plan.access.verified_restricted = []
        self.fixture.register()
        self.assertIn("restricted_unattested", codes(self.fixture.check()))
        (self.fixture.root / "receipt.txt").write_bytes(b"changed")
        self.assertIn("artifact_invalid", codes(self.fixture.check()))

    def test_revoked_source_permission_blocks_admission(self):
        self.fixture.catalog.sources[0].permission_status = "revoked"
        self.fixture.catalog.revision = 2
        self.fixture.registry.append(self.fixture.catalog)
        self.fixture.plan.dataset = record_ref(self.fixture.catalog)
        self.fixture.register()

        self.assertIn("source_permission_revoked", codes(self.fixture.check()))

    def test_actual_service_outside_source_permission_scope_blocks_admission(self):
        self.fixture.plan.access.source_paths[0].service_ids = ["unapproved-service"]
        self.fixture.register()

        self.assertIn("processing_path_not_permitted", codes(self.fixture.check()))
        self.assertIn("processing_endpoint_unknown", codes(self.fixture.check()))

    def test_unverified_data_flow_blocks_admission(self):
        self.fixture.plan.access.data_flow_verified = False
        self.fixture.register()

        self.assertIn("data_flow_verification_missing", codes(self.fixture.check()))

    def test_revocation_invalidates_old_approval(self):
        self.fixture.register()
        self.fixture.approve(record_ref(self.fixture.plan), "quality", "owner",
                             decision="revoked")
        self.assertIn("approval_not_granted", codes(self.fixture.check()))

    def test_model_cannot_approve_under_the_owners_name(self):
        self.fixture.register()
        self.fixture.approve(record_ref(self.fixture.plan), "quality", "owner",
                             actor_type="model")
        self.assertIn("approval_not_human", codes(self.fixture.check()))

    def test_protocol_revision_does_not_inherit_approvals(self):
        self.fixture.register()
        self.fixture.plan.revision = 2
        self.fixture.plan.supersedes = record_ref(
            protocol(**{**self.fixture.plan.model_dump(exclude={"revision", "supersedes"}),
                        "revision": 1}))
        self.fixture.plan.change_reason = "Fixture: changed budget before outcomes."
        self.fixture.registry.append(self.fixture.plan)
        self.assertIn("approval_missing", codes(self.fixture.check()))

    def test_sample_counts_and_unknown_results_are_blocking(self):
        self.fixture.plan.analysis.planned_cases = 10
        self.fixture.plan.known_results_disclosure = "unknown"
        self.fixture.register()
        self.assertTrue({"sample_count_mismatch", "results_disclosure_required"} <=
                        codes(self.fixture.check()))

    def test_disclosed_historical_results_are_not_themselves_a_blocker(self):
        self.fixture.plan.known_results_disclosure = "known"
        self.fixture.register()
        self.assertNotIn("results_disclosure_required", codes(self.fixture.check()))

    def test_synthetic_protocol_cannot_become_formal(self):
        self.fixture.plan.synthetic = True
        self.fixture.register()
        self.assertIn("synthetic_not_formal", codes(self.fixture.check()))

    def test_external_head_anchor_detects_truncated_or_changed_registry(self):
        self.fixture.register()
        report = self.fixture.check(expected_head="f" * 64)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("registry_invalid", codes(report))

    def test_refusal_partial_and_ambiguity_denominators_cannot_be_zero(self):
        self.fixture.plan.analysis.planned_answerability["insufficient_source"] = 0
        self.fixture.register()
        self.assertIn("answerability_coverage_missing", codes(self.fixture.check()))

    def test_confidence_and_group_plan_need_valid_independent_units(self):
        self.fixture.plan.analysis.planned_source_groups = 1
        self.fixture.plan.analysis.required_tags = {}
        self.fixture.register()
        self.assertTrue({"independent_groups_insufficient", "coverage_plan_missing"} <=
                        codes(self.fixture.check()))

    def test_missing_strata_blocks_admission(self):
        self.fixture.plan.analysis.strata = {}
        self.fixture.register()

        self.assertIn("analysis_strata_plan_missing", codes(self.fixture.check()))

    def test_stratum_case_and_group_counts_must_match_dataset(self):
        self.fixture.plan.analysis.strata["all"].planned_source_groups = 1
        self.fixture.register()

        self.assertIn("analysis_strata_plan_mismatch", codes(self.fixture.check()))

    def test_missing_confirmatory_metric_rule_blocks_admission(self):
        self.fixture.plan.analysis.metric_gates.pop("important_claim_coverage")
        self.fixture.register()

        self.assertIn("metric_gate_missing", codes(self.fixture.check()))

    def test_important_claim_coverage_cannot_be_report_only_in_current_gate_contract(self):
        self.fixture.plan.analysis.metric_gates["important_claim_coverage"] = (
            self.fixture.plan.analysis.metric_gates["important_claim_coverage"].model_copy(
                update={"decision": "report_only"}
            )
        )
        self.fixture.register()

        self.assertIn("metric_gate_contract_mismatch", codes(self.fixture.check()))

    def test_important_claim_coverage_requires_protocol_direction(self):
        self.fixture.plan.analysis.metric_gates["important_claim_coverage"] = (
            self.fixture.plan.analysis.metric_gates["important_claim_coverage"].model_copy(
                update={"decision": "minimum_floor"}
            )
        )
        self.fixture.register()

        self.assertIn("metric_gate_contract_mismatch", codes(self.fixture.check()))

    def test_important_claim_coverage_requires_reported_na_policy(self):
        self.fixture.plan.analysis.metric_gates["important_claim_coverage"] = (
            self.fixture.plan.analysis.metric_gates["important_claim_coverage"].model_copy(
                update={"na_policy": "not_allowed"}
            )
        )
        self.fixture.register()

        self.assertIn("metric_gate_contract_mismatch", codes(self.fixture.check()))

    def test_important_claim_coverage_requires_frozen_threshold_ref(self):
        self.fixture.plan.analysis.metric_gates["important_claim_coverage"] = (
            self.fixture.plan.analysis.metric_gates["important_claim_coverage"].model_copy(
                update={"threshold_ref": "citation_support_loss"}
            )
        )
        self.fixture.register()

        self.assertIn("metric_gate_contract_mismatch", codes(self.fixture.check()))

    def test_metric_gate_threshold_must_resolve_to_a_frozen_protocol_threshold(self):
        self.fixture.plan.analysis.metric_gates["primary_answer"] = (
            self.fixture.plan.analysis.metric_gates["primary_answer"].model_copy(
                update={"threshold_ref": "unregistered_threshold"}
            )
        )
        self.fixture.register()

        self.assertIn("metric_gate_threshold_unresolved", codes(self.fixture.check()))

    def test_metric_contract_version_is_required(self):
        self.fixture.plan.analysis.metric_contract_version = None
        self.fixture.register()

        self.assertIn("metric_contract_version_missing", codes(self.fixture.check()))

    def test_v03_metric_contract_remains_readable_but_is_not_currently_admissible(self):
        self.fixture.plan.analysis.metric_contract_version = "v0.3"
        self.fixture.register()

        report = self.fixture.check()
        self.assertIn("metric_contract_version_missing", codes(report))

    def test_unbounded_monetary_budget_keeps_operational_limits_required(self):
        self.fixture.plan.budget = self.fixture.plan.budget.model_copy(update={
            "monetary_limit": "unbounded", "per_task_cost": None, "total_cost": None,
        })
        self.fixture.register()

        report = self.fixture.check()
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["status"], "ready_for_custodian_review")

    def test_model_call_cap_and_mandatory_stop_conditions_are_required(self):
        self.fixture.plan.budget = self.fixture.plan.budget.model_copy(update={
            "max_model_calls": None,
            "stop_conditions": [],
        })
        self.fixture.register()

        report = self.fixture.check()
        missing_conditions = {
            item["location"] for item in report["issues"]
            if item["code"] == "stop_condition_missing"
        }
        self.assertIn(
            {"code": "budget_missing", "location": "budget.max_model_calls"},
            report["issues"],
        )
        self.assertIn("budget.stop_conditions.max_model_calls_reached", missing_conditions)
        self.assertEqual(len(missing_conditions), 7)

    def test_unbounded_monetary_budget_rejects_cost_caps_that_claim_otherwise(self):
        self.fixture.plan.budget = self.fixture.plan.budget.model_copy(update={
            "monetary_limit": "unbounded", "total_cost": 10.,
        })
        self.fixture.register()

        self.assertIn("budget_unbounded_conflict", codes(self.fixture.check()))


if __name__ == "__main__":
    unittest.main()

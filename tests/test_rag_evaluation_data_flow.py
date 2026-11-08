"""Synthetic-only checks for source permission and processing-path alignment."""

import unittest

from rag_evaluation.dataset import audit_data_flow
from rag_evaluation.models import (
    AccessPlan,
    DataEndpoint,
    SourceProcessingPath,
)
from test_rag_evaluation_dataset import dataset, reference, source


def permitted_source(name, **changes):
    return source(name, **({
        "permission_evidence": reference("permission.txt"),
        "permission_status": "active",
        "permission_subject": "fixture-owner",
        "permitted_purposes": ["rag_evaluation"],
        "prohibited_purposes": ["redistribution"],
        "retention_policy": "delete-after-review",
        "permitted_service_ids": ["embed", "generate"],
        "permitted_storage_ids": ["corpus-store", "index-store"],
        "permitted_transfer_ids": ["model-provider"],
    } | changes))


def endpoint(identifier, kind):
    return DataEndpoint(id=identifier, kind=kind, operator="fixture", region="test",
                        version="fixture-v1")


def complete_access(sources):
    return AccessPlan(
        endpoints=[
            endpoint("embed", "processing_service"),
            endpoint("generate", "processing_service"),
            endpoint("corpus-store", "storage"),
            endpoint("index-store", "storage"),
            endpoint("model-provider", "transfer"),
        ],
        source_paths=[SourceProcessingPath(
            source_id=item.id,
            service_ids=["embed", "generate"],
            storage_ids=["corpus-store", "index-store"],
            transfer_ids=["model-provider"],
        ) for item in sources],
        data_flow_verified=True,
        data_flow_evidence=reference("flow-audit.txt"),
    )


def codes(result):
    return {item["code"] for item in result["issues"]}


class DataFlowGovernanceTests(unittest.TestCase):
    def test_exact_source_routes_with_active_permission_are_accepted(self):
        catalog = dataset([permitted_source("s-one"), permitted_source("s-two")])

        report = audit_data_flow(catalog, complete_access(catalog.sources))

        self.assertEqual(report["issues"], [])

    def test_only_sources_used_by_the_current_run_need_permission_and_routes(self):
        catalog = dataset([
            permitted_source("s-one"),
            source("historical-summary", permission_evidence=None),
        ])

        report = audit_data_flow(
            catalog, complete_access([catalog.sources[0]]), source_ids={"s-one"})

        self.assertEqual(report["issues"], [])
        self.assertEqual(report["source_path_count"], 1)

    def test_unknown_and_revoked_permission_statuses_block(self):
        catalog = dataset([
            permitted_source("unknown", permission_status="unknown"),
            permitted_source("revoked", permission_status="revoked"),
        ])

        report = audit_data_flow(catalog, complete_access(catalog.sources))

        self.assertIn("source_permission_unverified", codes(report))
        self.assertIn("source_permission_revoked", codes(report))

    def test_missing_permission_evidence_or_allowed_route_blocks(self):
        catalog = dataset([permitted_source("s-one", permission_evidence=None)])
        catalog.sources[0].permitted_service_ids = []

        report = audit_data_flow(catalog, complete_access(catalog.sources))

        self.assertIn("source_permission_missing", codes(report))
        self.assertIn("processing_path_not_permitted", codes(report))

    def test_missing_license_subject_purpose_or_retention_blocks(self):
        catalog = dataset([permitted_source(
            "s-one", permission_subject=None, permitted_purposes=[], retention_policy=None,
        )])

        report = audit_data_flow(catalog, complete_access(catalog.sources))

        self.assertTrue({"permission_subject_missing", "evaluation_purpose_not_permitted",
                         "retention_policy_missing"} <= codes(report))

    def test_unknown_actual_endpoint_and_wrong_endpoint_kind_block(self):
        catalog = dataset([permitted_source("s-one")])
        access = complete_access(catalog.sources)
        access.source_paths[0].service_ids = ["unregistered-service"]
        access.source_paths[0].storage_ids = ["embed"]

        report = audit_data_flow(catalog, access)

        self.assertIn("processing_endpoint_unknown", codes(report))
        self.assertIn("processing_endpoint_kind_mismatch", codes(report))

    def test_every_source_requires_exactly_one_route_and_verified_receipt(self):
        catalog = dataset([permitted_source("s-one"), permitted_source("s-two")])
        access = complete_access(catalog.sources)
        access.source_paths = [access.source_paths[0], access.source_paths[0]]
        access.data_flow_verified = False

        report = audit_data_flow(catalog, access)

        self.assertIn("source_path_duplicate", codes(report))
        self.assertIn("source_path_missing", codes(report))
        self.assertIn("data_flow_verification_missing", codes(report))

    def test_duplicate_endpoint_identity_blocks(self):
        catalog = dataset([permitted_source("s-one")])
        access = complete_access(catalog.sources)
        access.endpoints.append(endpoint("embed", "transfer"))

        self.assertIn("processing_endpoint_duplicate",
                      codes(audit_data_flow(catalog, access)))

    def test_unavailable_data_flow_receipt_is_not_accepted_as_verification(self):
        catalog = dataset([permitted_source("s-one")])
        access = complete_access(catalog.sources)
        access.data_flow_evidence = reference("private-flow-audit.txt", restricted=True)

        self.assertIn("data_flow_verification_receipt_not_public",
                      codes(audit_data_flow(catalog, access)))


if __name__ == "__main__":
    unittest.main()

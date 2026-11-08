"""Run a synthetic three-system screening flow without calling any model."""

import json
import tempfile
from pathlib import Path

from rag_evaluation.artifacts import fingerprint
from rag_evaluation.comparison_freeze import freeze_comparison, verify_comparison_freeze
from rag_evaluation.gate import record_ref
from rag_evaluation.models import ComparisonPlan, ReadinessSubmission, SystemSnapshot, parse_record
from rag_evaluation.readiness_review import review_readiness_submission
from rag_evaluation.registry import Registry


EVIDENCE_CATEGORIES = (
    "scope_and_sampling", "dataset_permission", "split_and_exposure_audit",
    "gold_rubric_and_calibration", "metrics_statistics_and_sample_size",
    "blind_review_plan", "access_and_data_flow", "system_runtime_identity",
    "run_safety_and_recovery", "serious_error_audit", "approvals_and_signoff",
)


def run_quickstart():
    with tempfile.TemporaryDirectory(prefix="rag-evaluation-quickstart-") as temporary:
        root = Path(temporary)
        registry = Registry.initialize(root / "registry")
        evidence_root = root / "evidence"
        evidence_root.mkdir()
        receipt = evidence_root / "synthetic-receipt.txt"
        receipt.write_text("Synthetic evidence only; no real evaluation was performed.\n",
                           encoding="utf-8")
        evidence_ref = fingerprint(evidence_root, receipt.name, root_id="demo-evidence")

        snapshots = [
            SystemSnapshot(
                id=system_id, revision=1, system_id=system_id,
                identity=identity, root_id="demo-evidence",
            )
            for system_id, identity in (
                ("baseline-v1", "demo-team/rag-v1"),
                ("candidate-v2", "demo-team/rag-v2"),
                ("candidate-v2-reranker", "demo-team/rag-v2-reranker"),
            )
        ]
        references = {}
        for snapshot in snapshots:
            registry.append(snapshot)
            references[snapshot.id] = record_ref(snapshot)

        plan = ComparisonPlan(
            id="demo-screening", revision=1, mode="exploratory_screening", arms=[
                {"arm_id": "baseline-prod-v1", "role": "baseline",
                 "snapshot": references["baseline-v1"]},
                {"arm_id": "candidate-v2", "role": "candidate",
                 "snapshot": references["candidate-v2"]},
                {"arm_id": "candidate-v2-reranker", "role": "candidate",
                 "snapshot": references["candidate-v2-reranker"]},
            ],
        )
        registry.append(plan)
        readiness = ReadinessSubmission(
            id="demo-readiness", revision=1, comparison_plan=record_ref(plan),
            evidence=[{"category": category, "artifact": evidence_ref}
                      for category in EVIDENCE_CATEGORIES],
            roles=[
                {"role_id": "evaluation_owner", "person_id": "demo-owner"},
                {"role_id": "data_custodian", "person_id": "demo-custodian"},
                {"role_id": "independent_auditor", "person_id": "demo-auditor"},
                {"role_id": "annotator", "person_id": "demo-annotator"},
                {"role_id": "gold_reviewer", "person_id": "demo-gold-reviewer"},
                {"role_id": "adjudicator", "person_id": "demo-adjudicator"},
            ],
            review_assignments=[
                {"unit_id": "demo-unit-1", "arm_id": arm_id,
                 "reviewer_id": reviewer_id, "actor_type": "human"}
                for arm_id in ("baseline-prod-v1", "candidate-v2", "candidate-v2-reranker")
                for reviewer_id in (f"{arm_id}-reviewer-1", f"{arm_id}-reviewer-2")
            ],
        )
        registry.append(readiness)
        readiness_ref = record_ref(readiness)
        roots = {"demo-evidence": evidence_root}
        readiness_report = review_readiness_submission(registry, readiness_ref, roots)
        seal_row = freeze_comparison(
            registry, readiness_ref, roots, freeze_id="demo-seal",
            actor_id="demo-custodian",
        )
        seal = parse_record(seal_row["document"])
        verification = verify_comparison_freeze(
            registry, record_ref(seal), roots,
            expected_freeze_head=seal_row["sha256"],
        )

        return {
            "demo_only": True,
            "systems": len(plan.arms),
            "comparisons": len(plan.comparisons),
            "readiness_status": readiness_report["status"],
            "freeze_status": verification["status"],
            "run_authorized": False,
            "executed_models": 0,
        }


def main():
    print(json.dumps(run_quickstart(), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()

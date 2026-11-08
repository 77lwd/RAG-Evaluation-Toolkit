"""Versioned metadata contracts. Blind question and gold bodies never belong here."""

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonempty = Annotated[str, Field(min_length=1, max_length=2000)]
Count = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
Partition = Literal["development", "calibration", "H1", "H2"]
Probability = Annotated[float, Field(ge=0, le=1)]
PositiveFloat = Annotated[float, Field(gt=0)]
MaterialKind = Literal[
    "code", "effective_config", "embedding", "generation", "prompts",
    "corpus", "index", "ingestion", "environment", "runtime",
    "application", "retrieval", "index_builder", "data_import",
    "request_runner", "retry_recovery", "metric_aggregation",
    "statistical_analysis", "report_generation",
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ArtifactRef(Contract):
    root_id: Identifier
    path: Nonempty
    sha256: Digest
    size_bytes: Count
    visibility: Literal["public", "restricted"] = "public"

    @field_validator("path")
    @classmethod
    def relative_portable_path(cls, value):
        parts = PurePosixPath(value).parts
        reserved = {"CON", "PRN", "AUX", "NUL"} | {
            f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
        }
        if (
            not parts or PurePosixPath(value).is_absolute()
            or ":" in value or "\\" in value or "\x00" in value
            or str(PurePosixPath(value)) != value or ".." in parts
            or any(part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved
                   for part in parts)
        ):
            raise ValueError("relative_portable_path_required")
        return value


class Record(Contract):
    schema_version: Literal["1"] = "1"
    id: Identifier
    revision: PositiveInt


class ExposureEvent(Record):
    kind: Literal["exposure"] = "exposure"
    actor_id: Identifier
    actor_role: Literal["developer", "custodian", "annotator", "reviewer", "model"]
    occurred_at: Nonempty
    access: Literal["source", "question", "gold", "output", "summary", "tuning"]
    asset_sha256: list[Digest] = Field(default_factory=list, max_length=20000)
    question_sha256: list[Digest] = Field(default_factory=list, max_length=20000)
    family_ids: list[Identifier] = Field(default_factory=list, max_length=20000)
    case_ids: list[Identifier] = Field(default_factory=list, max_length=20000)
    evidence: ArtifactRef | None = None

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value):
        if datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError("timezone_required")
        return value

    @model_validator(mode="after")
    def target_required(self):
        if not any((self.asset_sha256, self.question_sha256, self.family_ids, self.case_ids)):
            raise ValueError("exposure_target_required")
        return self


class RecordRef(Contract):
    kind: Literal[
        "snapshot", "dataset", "protocol", "freeze", "comparison_plan",
        "readiness_submission", "comparison_freeze",
    ]
    id: Identifier
    revision: PositiveInt
    sha256: Digest


class SystemSnapshotRef(Contract):
    kind: Literal["system_snapshot"] = "system_snapshot"
    id: Identifier
    revision: PositiveInt
    sha256: Digest


class Snapshot(Record):
    kind: Literal["snapshot"] = "snapshot"
    arm: Literal["A", "B", "C"]
    identity: Literal["local_reference", "production", "candidate", "integrated"]
    root_id: Identifier
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None = None
    git_branch: Nonempty | None = None
    dirty: bool | None = None
    runtime_verified: bool = False
    mutable_model_disclosure: Nonempty | None = None
    materials: dict[MaterialKind, list[ArtifactRef]] = Field(default_factory=dict)
    notes: Nonempty | None = None


class SystemSnapshot(Record):
    schema_version: Literal["2"] = "2"
    kind: Literal["system_snapshot"] = "system_snapshot"
    system_id: Identifier
    identity: Nonempty
    root_id: Identifier
    git_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None = None
    git_branch: Nonempty | None = None
    dirty: bool | None = None
    runtime_verified: bool = False
    materials: dict[MaterialKind, list[ArtifactRef]] = Field(default_factory=dict)
    notes: Nonempty | None = None


class SourceAsset(Contract):
    id: Identifier
    family_id: Identifier
    artifact: ArtifactRef
    derived_from: list[Identifier] = Field(default_factory=list, max_length=100)
    permission_evidence: ArtifactRef | None = None
    permission_status: Literal["unknown", "active", "revoked"] = "unknown"
    permission_subject: Nonempty | None = None
    permitted_purposes: list[Identifier] = Field(default_factory=list, max_length=50)
    prohibited_purposes: list[Identifier] = Field(default_factory=list, max_length=50)
    retention_policy: Nonempty | None = None
    permitted_service_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    permitted_storage_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    permitted_transfer_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class CaseMetadata(Contract):
    id: Identifier
    partition: Partition
    source_ids: list[Identifier] = Field(min_length=1, max_length=100)
    task_family_id: Identifier
    question_sha256: Digest
    question_ref: ArtifactRef
    gold_ref: ArtifactRef | None = None
    answerability: Literal["answerable", "insufficient_source", "partial", "ambiguous"]
    analysis_stratum_id: Identifier | None = None
    independence: Literal["unknown", "exposed", "audited_unexposed"] = "unknown"
    independence_evidence: ArtifactRef | None = None
    tags: list[Identifier] = Field(default_factory=list, max_length=100)
    weight: PositiveFloat = 1.0


class Dataset(Record):
    kind: Literal["dataset"] = "dataset"
    synthetic: bool = False
    sources: list[SourceAsset] = Field(default_factory=list, max_length=20000)
    cases: list[CaseMetadata] = Field(default_factory=list, max_length=20000)
    exposure_coverage: Literal["unknown", "audited"] = "unknown"
    exposure_audit: ArtifactRef | None = None


class People(Contract):
    human_ids: list[Identifier] = Field(default_factory=list)
    owner: Identifier | None = None
    custodian: Identifier | None = None
    auditor: Identifier | None = None
    developers: list[Identifier] = Field(default_factory=list)
    annotators: list[Identifier] = Field(default_factory=list)
    gold_reviewer: Identifier | None = None
    reviewers: list[Identifier] = Field(default_factory=list)
    adjudicator: Identifier | None = None
    agents: list["ScoringAgent"] = Field(default_factory=list, max_length=50)


class ScoringAgent(Contract):
    id: Identifier
    independence_key: Nonempty
    platform: Nonempty
    model: Nonempty
    model_version: Nonempty
    prompt_sha256: Digest
    config_sha256: Digest
    tool_permissions: list[Literal[
        "read_source", "read_blind_question", "read_gold", "read_identity_map",
        "read_anonymous_answer", "read_rubric", "read_submitted_reviews", "write_score",
    ]] = Field(default_factory=list, max_length=20)
    isolation_receipt: ArtifactRef | None = None
    calibration_receipt: ArtifactRef | None = None


class Grant(Contract):
    principal: Identifier
    partition: Literal["public", "development", "calibration", "H1", "H2"] = "public"
    resource: Literal[
        "source", "blind_question", "gold", "identity_map",
        "anonymous_answer", "rubric", "submitted_reviews", "protocol",
    ]
    operation: Literal["read", "write"]


class DataEndpoint(Contract):
    id: Identifier
    kind: Literal["processing_service", "storage", "transfer"]
    operator: Identifier
    region: Identifier
    version: Nonempty


class SourceProcessingPath(Contract):
    source_id: Identifier
    service_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    storage_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    transfer_ids: list[Identifier] = Field(default_factory=list, max_length=100)


class AccessPlan(Contract):
    grants: list[Grant] = Field(default_factory=list)
    runner_id: Identifier | None = None
    verified_by: Identifier | None = None
    account_separation_verified: bool = False
    verification_receipt: ArtifactRef | None = None
    verified_restricted: list[ArtifactRef] = Field(default_factory=list)
    endpoints: list[DataEndpoint] = Field(default_factory=list, max_length=500)
    source_paths: list[SourceProcessingPath] = Field(default_factory=list, max_length=20000)
    data_flow_verified: bool = False
    data_flow_evidence: ArtifactRef | None = None


class Thresholds(Contract):
    primary_gain: Probability | None = None
    correct_refusal_loss: Probability | None = None
    false_refusal_increase: Probability | None = None
    citation_support_loss: Probability | None = None
    important_claim_coverage_threshold: Probability | None = None
    p95_ratio_max: PositiveFloat | None = None
    mean_cost_ratio_max: PositiveFloat | None = None


class MetricGate(Contract):
    unit: Literal["rate", "seconds", "currency", "count"]
    numerator_definition: Nonempty | None
    denominator_definition: Nonempty
    scorer: Literal["human_dual_blind", "agent_dual_blind", "automatic", "adjudicated"]
    aggregation: Literal[
        "weighted_case_mean", "per_case_macro", "all_planned_task_p95",
        "mean_per_task_all_planned", "adjudicated_event_count",
    ]
    na_policy: Literal["not_allowed", "not_applicable_reported"]
    decision: Literal[
        "primary_gain", "lower_bound_noninferiority", "upper_bound_noninferiority",
        "ratio_max", "zero_tolerance", "minimum_floor", "report_only",
    ]
    threshold_ref: Identifier | None


class Budget(Contract):
    monetary_limit: Literal["bounded", "unbounded"] | None = None
    absolute_p95_seconds: PositiveFloat | None = None
    per_task_cost: Annotated[float, Field(ge=0)] | None = None
    total_cost: Annotated[float, Field(ge=0)] | None = None
    currency: Identifier | None = None
    memory_mb: PositiveInt | None = None
    concurrency: PositiveInt | None = None
    task_timeout_seconds: PositiveFloat | None = None
    retries: Count | None = None
    max_model_calls: PositiveInt | None = Field(
        default=None,
        description=(
            "Total cap shared by A and B; count every model invocation attempt across all "
            "planned tasks, model stages, and retries before dispatch."
        ),
    )
    stop_conditions: list[Literal[
        "suspected_privacy_or_unauthorized_access", "unauthorized_data_transfer",
        "evidence_integrity_failure", "runtime_identity_mismatch",
        "resource_limit_exceeded", "max_model_calls_reached", "manual_emergency_stop",
    ]] = Field(
        default_factory=list,
        max_length=20,
        description="Must include all mandatory hard-stop conditions checked by admission.",
    )


class AnalysisStratumPlan(Contract):
    planned_cases: PositiveInt
    planned_source_groups: PositiveInt


class AnalysisPlan(Contract):
    primary_metric: Literal["supported_complete_answer_rate"] = "supported_complete_answer_rate"
    confidence_level: Probability = 0.95
    method: Literal["grouped_paired_bootstrap"] = "grouped_paired_bootstrap"
    resamples: PositiveInt = 10000
    seed: Count = 20261105
    planned_cases: PositiveInt | None = None
    planned_source_groups: PositiveInt | None = None
    planned_answerability: dict[
        Literal["answerable", "insufficient_source", "partial", "ambiguous"], Count
    ] = Field(default_factory=dict)
    strata: dict[Identifier, AnalysisStratumPlan] = Field(default_factory=dict)
    metric_contract_version: Literal["v0.3", "v0.4", "v0.5"] | None = None
    metric_gates: dict[Identifier, MetricGate] = Field(default_factory=dict)
    target_power: Probability | None = None
    primary_rounds: PositiveInt = 1
    weighting: Literal["equal_task", "preregistered_weights"] = "equal_task"
    required_tags: dict[Identifier, PositiveInt] = Field(default_factory=dict)


RuleName = Literal[
    "scope_and_sampling", "split_policy", "gold_rubric", "metrics_and_statistics",
    "run_plan", "review_plan", "serious_errors", "sample_size_justification",
    "retention_and_recovery",
]


class Protocol(Record):
    kind: Literal["protocol"] = "protocol"
    phase: Literal["H1", "H2"]
    experiment: Literal[
        "production_replay", "local_reference_replay", "new_source_end_to_end"
    ]
    design_version: Literal["1.0"] = "1.0"
    dataset: RecordRef | None = None
    snapshots: dict[Literal["A", "B", "C"], RecordRef] = Field(default_factory=dict)
    rules: dict[RuleName, ArtifactRef] = Field(default_factory=dict)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    budget: Budget = Field(default_factory=Budget)
    analysis: AnalysisPlan = Field(default_factory=AnalysisPlan)
    people: People = Field(default_factory=People)
    access: AccessPlan = Field(default_factory=AccessPlan)
    synthetic: bool = False
    supersedes: RecordRef | None = None
    change_reason: Nonempty | None = None
    known_results_disclosure: Literal["none", "known", "unknown"] = "unknown"


class ComparisonArm(Contract):
    arm_id: Identifier
    role: Literal["baseline", "candidate"]
    snapshot: SystemSnapshotRef


class ComparisonPlan(Record):
    schema_version: Literal["2"] = "2"
    kind: Literal["comparison_plan"] = "comparison_plan"
    mode: Literal["exploratory_screening", "confirmatory"]
    arms: list[ComparisonArm] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def validate_comparison_design(self):
        arm_ids = [arm.arm_id for arm in self.arms]
        if len(arm_ids) != len(set(arm_ids)):
            raise ValueError("duplicate_arm_id")
        snapshot_refs = [
            (arm.snapshot.kind, arm.snapshot.id, arm.snapshot.revision, arm.snapshot.sha256)
            for arm in self.arms
        ]
        if len(snapshot_refs) != len(set(snapshot_refs)):
            raise ValueError("duplicate_system_snapshot")
        baselines = [arm for arm in self.arms if arm.role == "baseline"]
        candidates = [arm for arm in self.arms if arm.role == "candidate"]
        if len(baselines) != 1 or not candidates:
            raise ValueError("one_baseline_and_candidate_required")
        if self.mode == "confirmatory" and len(candidates) != 1:
            raise ValueError("confirmatory_requires_one_candidate")
        return self

    @property
    def comparisons(self):
        baseline = next(arm for arm in self.arms if arm.role == "baseline")
        result_type = (
            "exploratory"
            if self.mode == "exploratory_screening"
            else "confirmatory_plan"
        )
        return [
            {
                "baseline_arm_id": baseline.arm_id,
                "candidate_arm_id": arm.arm_id,
                "result_type": result_type,
            }
            for arm in self.arms
            if arm.role == "candidate"
        ]


class ReadinessEvidence(Contract):
    category: Identifier
    artifact: ArtifactRef


class HumanRoleAssignment(Contract):
    role_id: Identifier
    person_id: Identifier


class ReviewAssignment(Contract):
    unit_id: Identifier
    arm_id: Identifier
    reviewer_id: Identifier
    actor_type: Literal["human", "model"]


class ReadinessSubmission(Record):
    schema_version: Literal["2"] = "2"
    kind: Literal["readiness_submission"] = "readiness_submission"
    comparison_plan: RecordRef
    mode: Literal["team_formal"] = "team_formal"
    evidence: list[ReadinessEvidence] = Field(default_factory=list, max_length=100)
    roles: list[HumanRoleAssignment] = Field(default_factory=list, max_length=100)
    review_assignments: list[ReviewAssignment] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def unique_readiness_declarations(self):
        evidence_categories = [item.category for item in self.evidence]
        role_ids = [item.role_id for item in self.roles]
        assignment_keys = [
            (item.unit_id, item.arm_id, item.reviewer_id, item.actor_type)
            for item in self.review_assignments
        ]
        if len(evidence_categories) != len(set(evidence_categories)):
            raise ValueError("duplicate_readiness_evidence_category")
        if len(role_ids) != len(set(role_ids)):
            raise ValueError("duplicate_readiness_role")
        if len(assignment_keys) != len(set(assignment_keys)):
            raise ValueError("duplicate_review_assignment")
        return self


class Approval(Record):
    kind: Literal["approval"] = "approval"
    target: RecordRef
    scope: Literal["scope", "quality", "budget", "roles", "access", "sampling", "protocol", "identity"]
    actor_id: Identifier
    actor_type: Literal["human", "service", "model"]
    occurred_at: Nonempty
    decision: Literal["approved", "rejected", "revoked"]
    evidence: ArtifactRef

    _timezone = field_validator("occurred_at")(ExposureEvent.timezone_required.__func__)


class Freeze(Record):
    kind: Literal["freeze"] = "freeze"
    protocol: RecordRef
    dataset: RecordRef
    snapshots: dict[Literal["A", "B", "C"], RecordRef]
    actor_id: Identifier
    prior_head: Digest
    check_sha256: Digest
    tool_sha256: Digest
    mode: Literal["formal", "local"]
    run_authorized: Literal[False] = False


class ComparisonFreeze(Record):
    schema_version: Literal["2"] = "2"
    kind: Literal["comparison_freeze"] = "comparison_freeze"
    comparison_plan: RecordRef
    readiness_submission: RecordRef
    actor_id: Identifier
    prior_head: Digest
    check_sha256: Digest
    tool_sha256: Digest
    run_authorized: Literal[False] = False


def parse_record(value):
    kinds = {
        "exposure": ExposureEvent, "snapshot": Snapshot, "system_snapshot": SystemSnapshot,
        "dataset": Dataset,
        "protocol": Protocol, "approval": Approval, "freeze": Freeze,
        "comparison_plan": ComparisonPlan, "readiness_submission": ReadinessSubmission,
        "comparison_freeze": ComparisonFreeze,
    }
    if not isinstance(value, dict) or value.get("kind") not in kinds:
        raise ValueError("unknown_record_kind")
    return kinds[value["kind"]].model_validate(value)

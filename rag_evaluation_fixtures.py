"""Open synthetic fixtures only. These people and permissions do not exist."""

from rag_evaluation.models import AccessPlan, Grant, People, Protocol, ScoringAgent


def people():
    agent_receipt = {
        "root_id": "synthetic", "path": "agent-isolation.json", "sha256": "a" * 64,
        "size_bytes": 1, "visibility": "restricted",
    }
    calibration_receipt = {
        "root_id": "synthetic", "path": "agent-calibration.json", "sha256": "b" * 64,
        "size_bytes": 1, "visibility": "restricted",
    }
    return People(
        human_ids=["owner", "auditor", "author", "adjudicator"],
        owner="owner", custodian="custodian-agent", auditor="auditor",
        developers=["developer"], annotators=["author"], gold_reviewer="custodian-agent",
        reviewers=["review-agent-a", "review-agent-b"], adjudicator="adjudicator",
        agents=[
            ScoringAgent(id="custodian-agent", independence_key="custodian-agent/custodian", platform="synthetic-platform", model="custodian-model",
                         model_version="fixture-1", prompt_sha256="c" * 64,
                         config_sha256="d" * 64,
                         tool_permissions=["read_source", "read_blind_question", "read_gold",
                                           "read_identity_map"],
                         isolation_receipt=agent_receipt),
            *[ScoringAgent(id=agent_id, independence_key=f"{agent_id}/reviewer",
                           platform=agent_id.upper(), model="review-model",
                           model_version="fixture-1", prompt_sha256="e" * 64,
                           config_sha256="f" * 64,
                           tool_permissions=["read_source", "read_blind_question",
                                             "read_anonymous_answer", "read_rubric", "write_score"],
                           isolation_receipt=agent_receipt,
                           calibration_receipt=calibration_receipt)
              for agent_id in ("review-agent-a", "review-agent-b")],
        ],
    )


def access(role_plan=None, phase="H1"):
    role_plan = role_plan or people()
    grants = []

    def grant(principal, resources, operations=("read",), partition=phase):
        for resource in resources:
            for operation in operations:
                item = Grant(principal=principal, partition=partition,
                             resource=resource, operation=operation)
                if item not in grants:
                    grants.append(item)

    agent_ids = [agent.id for agent in role_plan.agents]
    for principal in role_plan.human_ids + agent_ids + ["runner", *role_plan.developers]:
        grant(principal, ["protocol"], partition="public")
    grant(role_plan.owner, ["protocol"], ("write",), "public")
    grant(role_plan.custodian, ["blind_question", "gold", "identity_map"], ("read", "write"))
    grant(role_plan.custodian, ["source"])
    grant(role_plan.auditor, ["source", "identity_map"])
    grant("runner", ["source", "blind_question"])
    for principal in role_plan.annotators:
        grant(principal, ["source"])
        grant(principal, ["blind_question", "gold"], ("read", "write"))
    grant(role_plan.gold_reviewer, ["source", "blind_question", "gold"])
    for principal in role_plan.reviewers:
        grant(principal, ["source", "blind_question", "anonymous_answer", "rubric"])
        grant(principal, ["submitted_reviews"], ("write",))
    grant(role_plan.adjudicator, ["source", "blind_question", "gold",
                                 "anonymous_answer", "rubric", "submitted_reviews"])
    return AccessPlan(grants=grants, runner_id="runner", verified_by="auditor",
                      account_separation_verified=True)


def protocol(**changes):
    return Protocol(**({
        "id": "synthetic-protocol", "revision": 1, "phase": "H1",
        "experiment": "production_replay", "people": people(), "access": access(),
    } | changes))

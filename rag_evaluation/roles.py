"""Audit declared role separation; OS enforcement still requires an independent receipt."""


def audit_roles(protocol):
    people, access = protocol.people, protocol.access
    issues = []

    def issue(code, location):
        item = {"code": code, "location": location}
        if item not in issues:
            issues.append(item)

    required_people = {
        "owner": people.owner, "custodian": people.custodian, "auditor": people.auditor,
        "gold_reviewer": people.gold_reviewer, "adjudicator": people.adjudicator,
    }
    for role, person in required_people.items():
        if person is None:
            issue("role_missing", role)
    agents = {agent.id: agent for agent in people.agents}
    if len(agents) != len(people.agents):
        issue("agent_identity_duplicate", "people.agents")
    if set(people.human_ids) & set(agents):
        issue("human_agent_identity_collision", "people.agents")
    assigned = [*required_people.values(), *people.annotators, *people.reviewers]
    for person in assigned:
        if person is not None and person not in people.human_ids and person not in agents:
            issue("human_role_unverified", person)
    for role in ("owner", "auditor", "adjudicator"):
        person = getattr(people, role)
        if person is not None and person not in people.human_ids:
            issue("human_authority_required", role)
    if not people.annotators:
        issue("annotator_missing", "people.annotators")
    if len(set(people.reviewers)) < 2 or len(set(people.reviewers)) != len(people.reviewers):
        issue("independent_reviewers_required", "people.reviewers")
    for reviewer in people.reviewers:
        if reviewer not in agents:
            issue("agent_reviewer_required", f"people.reviewers.{reviewer}")
    reviewer_groups = [agents[reviewer].independence_key for reviewer in people.reviewers
                      if reviewer in agents]
    if len(set(reviewer_groups)) != len(reviewer_groups):
        issue("reviewer_independence_group_conflict", "people.reviewers")
    independent = [people.owner, people.custodian, people.auditor, people.gold_reviewer,
                   people.adjudicator, *people.annotators, *people.reviewers]
    for person in set(independent) & set(people.developers):
        issue("developer_role_conflict", person)
    if people.gold_reviewer in people.annotators:
        issue("gold_review_not_independent", "people.gold_reviewer")
    if people.gold_reviewer in people.reviewers:
        issue("gold_reviewer_reviewer_conflict", "people.gold_reviewer")
    for person in set(people.annotators) & set(people.reviewers):
        issue("annotator_reviewer_conflict", person)
    if people.adjudicator in people.reviewers:
        issue("adjudicator_not_independent", "people.adjudicator")
    for person in set(people.reviewers) & {people.custodian, people.auditor}:
        issue("reviewer_identity_access_conflict", person)
    for role, person in (("custodian", people.custodian), ("gold_reviewer", people.gold_reviewer),
                         *(('reviewer', reviewer) for reviewer in people.reviewers)):
        if person is None or person not in agents:
            continue
        agent = agents[person]
        if agent.isolation_receipt is None:
            issue("agent_isolation_unverified", f"people.agents.{person}")
        tools = set(agent.tool_permissions)
        if role == "reviewer":
            required_tools = {"read_source", "read_blind_question", "read_anonymous_answer",
                              "read_rubric", "write_score"}
            if tools & {"read_gold", "read_identity_map", "read_submitted_reviews"}:
                issue("agent_reviewer_secret_access", f"people.agents.{person}.tool_permissions")
            if not required_tools <= tools:
                issue("agent_reviewer_tools_incomplete", f"people.agents.{person}.tool_permissions")
            if agent.calibration_receipt is None:
                issue("agent_calibration_missing", f"people.agents.{person}")
        elif role in ("custodian", "gold_reviewer"):
            required_tools = {"read_source", "read_blind_question", "read_gold", "read_identity_map"}
            if not required_tools <= tools:
                issue("agent_custodian_tools_incomplete", f"people.agents.{person}.tool_permissions")
    if (not access.account_separation_verified or access.verification_receipt is None
            or access.verified_by is None or access.verified_by != people.auditor):
        issue("access_separation_unverified", "access")
    if access.runner_id is None or access.runner_id in people.human_ids + people.developers:
        issue("runner_account_not_separate", "access.runner_id")

    required, allowed = set(), set()

    def grant(person, resources, operations=("read",), partition=None, all_blind=False):
        if person is None:
            return
        partitions = ([partition] if partition else
                      ["H1", "H2"] if all_blind else [protocol.phase])
        for current in partitions:
            for resource in resources:
                for operation in operations:
                    key = (person, current, resource, operation)
                    allowed.add(key)
                    if current in ("public", protocol.phase):
                        required.add(key)

    for person in set(assigned) | {access.runner_id} | set(people.developers):
        grant(person, ["protocol"], partition="public")
    grant(people.owner, ["protocol"], ("write",), "public")
    grant(people.custodian, ["blind_question", "gold", "identity_map"],
          ("read", "write"), all_blind=True)
    grant(people.custodian, ["source"], all_blind=True)
    grant(people.auditor, ["source", "identity_map"], all_blind=True)
    grant(access.runner_id, ["source", "blind_question"])
    for person in people.annotators:
        grant(person, ["source"], all_blind=True)
        grant(person, ["blind_question", "gold"], ("read", "write"), all_blind=True)
    grant(people.gold_reviewer, ["source", "blind_question", "gold"], all_blind=True)
    for person in people.reviewers:
        grant(person, ["source", "blind_question", "anonymous_answer", "rubric"])
        grant(person, ["submitted_reviews"], ("write",))
    grant(people.adjudicator, ["source", "blind_question", "gold",
                               "anonymous_answer", "rubric", "submitted_reviews"])
    for person in people.developers:
        for partition in ("public", "development", "calibration"):
            for resource in ("source", "blind_question", "gold", "anonymous_answer",
                             "rubric", "submitted_reviews", "protocol"):
                for operation in ("read", "write"):
                    allowed.add((person, partition, resource, operation))
    actual = set()
    for item in access.grants:
        key = (item.principal, item.partition, item.resource, item.operation)
        if key in actual:
            issue("duplicate_grant", item.principal)
        actual.add(key)
        if key not in allowed:
            issue("forbidden_grant", ":".join(key))
    for key in sorted(required - actual):
        issue("required_grant_missing", ":".join(key))
    return issues

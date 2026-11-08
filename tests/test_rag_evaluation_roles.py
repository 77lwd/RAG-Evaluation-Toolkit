import unittest

from rag_evaluation.models import Grant
from rag_evaluation.roles import audit_roles
from rag_evaluation_fixtures import access, protocol
from test_rag_evaluation_dataset import reference


def ready_roles():
    result = protocol()
    result.access.verification_receipt = reference("acl-verification.json")
    return result


class RoleGovernanceTests(unittest.TestCase):
    def codes(self, item):
        return {issue["code"] for issue in audit_roles(item)}

    def test_registered_agents_and_attested_least_privilege_plan(self):
        self.assertEqual(audit_roles(ready_roles()), [])

    def test_models_and_same_person_twice_do_not_make_independent_human_review(self):
        item = ready_roles()
        item.people.reviewers = ["review-agent-a", "review-agent-a"]
        self.assertIn("independent_reviewers_required", self.codes(item))
        item.people.reviewers = ["review-agent-a", "judge-model"]
        self.assertIn("agent_reviewer_required", self.codes(item))

    def test_developer_cannot_be_reviewer_or_custodian(self):
        item = ready_roles()
        item.people.developers.append("review-agent-a")
        self.assertIn("developer_role_conflict", self.codes(item))
        item = ready_roles()
        item.people.developers.append("custodian-agent")
        self.assertIn("developer_role_conflict", self.codes(item))
        item = ready_roles()
        item.people.developers.append(item.people.owner)
        self.assertIn("developer_role_conflict", self.codes(item))

    def test_annotator_cannot_review_the_same_evaluation(self):
        item = ready_roles()
        item.people.annotators.append("review-agent-a")
        for resource in ("blind_question", "gold"):
            item.access.grants.append(Grant(
                principal="review-agent-a", partition="H1",
                resource=resource, operation="write",
            ))
        self.assertIn("annotator_reviewer_conflict", self.codes(item))

    def test_gold_author_and_reviewer_or_reviewer_and_adjudicator_must_differ(self):
        item = ready_roles()
        item.people.gold_reviewer = "author"
        self.assertIn("gold_review_not_independent", self.codes(item))
        item = ready_roles()
        item.people.gold_reviewer = "review-agent-a"
        self.assertIn("gold_reviewer_reviewer_conflict", self.codes(item))
        item = ready_roles()
        item.people.adjudicator = "review-agent-a"
        self.assertIn("adjudicator_not_independent", self.codes(item))

    def test_runner_gold_and_reviewer_identity_map_are_forbidden(self):
        for principal, resource in (("runner", "gold"), ("review-agent-a", "identity_map"),
                                    ("developer", "blind_question")):
            item = ready_roles()
            item.access.grants.append(Grant(principal=principal, partition="H1",
                                           resource=resource, operation="read"))
            with self.subTest(principal=principal):
                self.assertIn("forbidden_grant", self.codes(item))

    def test_blind_reviewers_cannot_read_gold_in_either_partition(self):
        for phase in ("H1", "H2"):
            with self.subTest(phase=phase):
                item = ready_roles()
                item.phase = phase
                if phase == "H2":
                    item.access = access(item.people, phase="H2")
                    item.access.verification_receipt = reference("acl-verification.json")
                item.access.grants = [
                    grant for grant in item.access.grants
                    if not (grant.principal in item.people.reviewers
                            and grant.resource == "gold")
                ]
                self.assertNotIn("forbidden_grant", self.codes(item))
                item.access.grants.append(Grant(
                    principal="review-agent-a", partition=phase,
                    resource="gold", operation="read",
                ))
                self.assertIn("forbidden_grant", self.codes(item))

    def test_runner_and_reviewers_cannot_access_the_other_blind_partition(self):
        scenarios = (
            ("H1", "H2", "runner", "blind_question"),
            ("H1", "H2", "review-agent-a", "gold"),
            ("H2", "H1", "runner", "blind_question"),
            ("H2", "H1", "review-agent-a", "gold"),
        )
        for phase, other_partition, principal, resource in scenarios:
            with self.subTest(phase=phase, principal=principal):
                item = ready_roles()
                item.phase = phase
                if phase == "H2":
                    item.access = access(item.people, phase="H2")
                    item.access.verification_receipt = reference("acl-verification.json")
                item.access.grants.append(Grant(
                    principal=principal, partition=other_partition,
                    resource=resource, operation="read",
                ))
                self.assertIn("forbidden_grant", self.codes(item))

    def test_no_required_grants_or_unverified_os_separation_is_blocked(self):
        item = ready_roles()
        item.access.grants = []
        item.access.account_separation_verified = False
        item.access.verification_receipt = None
        self.assertTrue({"required_grant_missing", "access_separation_unverified"} <= self.codes(item))

    def test_agent_reviewer_requires_isolation_calibration_and_no_gold_tools(self):
        item = ready_roles()
        review_agent_a = next(agent for agent in item.people.agents if agent.id == "review-agent-a")
        review_agent_a.isolation_receipt = None
        review_agent_a.calibration_receipt = None
        review_agent_a.tool_permissions.append("read_gold")
        self.assertTrue({"agent_isolation_unverified", "agent_calibration_missing",
                         "agent_reviewer_secret_access"} <= self.codes(item))

    def test_distinct_agent_ids_with_same_independence_group_are_not_two_reviewers(self):
        item = ready_roles()
        review_agent_a = next(agent for agent in item.people.agents if agent.id == "review-agent-a")
        review_agent_b = next(agent for agent in item.people.agents if agent.id == "review-agent-b")
        review_agent_b.independence_key = review_agent_a.independence_key
        self.assertIn("reviewer_independence_group_conflict", self.codes(item))


if __name__ == "__main__":
    unittest.main()

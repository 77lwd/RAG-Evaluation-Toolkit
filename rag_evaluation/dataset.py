"""Conservative grouping and exposure accounting over metadata, never blind bodies."""

from collections import Counter, defaultdict


def audit_dataset(catalog, exposures, *, phase, history=()):
    issues = []
    parents = {}

    def issue(code, location):
        item = {"code": code, "location": location}
        if item not in issues:
            issues.append(item)

    def find(node):
        parents.setdefault(node, node)
        root = node
        while parents[root] != root:
            root = parents[root]
        while parents[node] != node:
            previous = parents[node]
            parents[node] = root
            node = previous
        return root

    def connect(nodes):
        if nodes:
            first = find(nodes[0])
            for node in nodes[1:]:
                other = find(node)
                if other != first:
                    parents[other] = first

    catalogs = [*history, catalog]
    for item in catalogs:
        for source in item.sources:
            connect([f"source:{source.id}", f"asset:{source.artifact.sha256}",
                     f"family:{source.family_id}"] +
                    [f"source:{parent}" for parent in source.derived_from])
        for case in item.cases:
            connect([f"case:{case.id}", f"question:{case.question_sha256}",
                     f"task:{case.task_family_id}"] +
                    [f"source:{source}" for source in case.source_ids])

    source_ids = {source.id for source in catalog.sources}
    for name, items in (("source", catalog.sources), ("case", catalog.cases)):
        for identifier, count in Counter(item.id for item in items).items():
            if count > 1:
                issue(f"duplicate_{name}_id", identifier)
    all_source_ids = {source.id for item in catalogs for source in item.sources}
    selected = [case for case in catalog.cases if case.partition == phase]
    selected_source_ids = {source_id for case in selected for source_id in case.source_ids}
    for source in catalog.sources:
        for parent in source.derived_from:
            if parent not in all_source_ids:
                issue("unknown_parent_source", source.id)
        if source.id in selected_source_ids and source.permission_evidence is None:
            issue("source_permission_missing", source.id)
    for case in catalog.cases:
        if len(set(case.source_ids)) != len(case.source_ids):
            issue("duplicate_source_in_case", case.id)
        if any(source not in source_ids for source in case.source_ids):
            issue("unknown_source", case.id)
        if case.partition in ("H1", "H2"):
            if case.independence != "audited_unexposed" or case.independence_evidence is None:
                issue("independence_unverified", case.id)
            if case.question_ref.visibility != "restricted":
                issue("blind_question_not_restricted", case.id)
            if case.gold_ref is None or case.gold_ref.visibility != "restricted":
                issue("blind_gold_not_restricted", case.id)

    partitions = defaultdict(set)
    artifact_cases = defaultdict(set)
    for item in catalogs:
        for case in item.cases:
            group = find(f"case:{case.id}")
            partitions[group].add(case.partition)
            artifact_cases[case.question_ref.sha256].add(group)
            if case.gold_ref is not None:
                artifact_cases[case.gold_ref.sha256].add(group)
    for group, splits in partitions.items():
        if len(splits) > 1:
            issue("cross_partition_group", group)

    exposed = {find(f"case:{case.id}") for item in catalogs for case in item.cases
               if case.independence == "exposed"}
    for event in exposures:
        if event.actor_role not in ("developer", "model") and event.access != "tuning":
            continue
        targets = ([f"asset:{sha}" for sha in event.asset_sha256] +
                   [f"question:{sha}" for sha in event.question_sha256] +
                   [f"family:{identifier}" for identifier in event.family_ids] +
                   [f"case:{identifier}" for identifier in event.case_ids])
        exposed.update(find(node) for node in targets if node in parents)
        for sha in event.asset_sha256:
            exposed.update(artifact_cases[sha])
    for case in catalog.cases:
        if case.partition in ("H1", "H2") and find(f"case:{case.id}") in exposed:
            issue("exposed_blind_group", case.id)

    if not selected:
        issue("no_cases_for_phase", phase)
    stratum_groups = defaultdict(set)
    for case in selected:
        if case.analysis_stratum_id is None:
            issue("analysis_stratum_missing", case.id)
        else:
            stratum_groups[case.analysis_stratum_id].add(find(f"case:{case.id}"))
    group_strata = defaultdict(set)
    for stratum_id, groups in stratum_groups.items():
        for group in groups:
            group_strata[group].add(stratum_id)
    for group, strata in group_strata.items():
        if len(strata) > 1:
            issue("cross_analysis_stratum_group", group)
    if catalog.exposure_coverage != "audited" or catalog.exposure_audit is None:
        issue("exposure_history_unknown", catalog.id)
    stratum_case_counts = Counter(
        case.analysis_stratum_id for case in selected
        if case.analysis_stratum_id is not None
    )
    return {
        "issues": issues,
        "case_count": len(selected),
        "source_group_count": len({find(f"case:{case.id}") for case in selected}),
        "answerability": dict(Counter(case.answerability for case in selected)),
        "tags": dict(Counter(tag for case in selected for tag in set(case.tags))),
        "strata": {
            stratum_id: {
                "case_count": stratum_case_counts[stratum_id],
                "source_group_count": len(groups),
            }
            for stratum_id, groups in sorted(stratum_groups.items())
        },
    }


def audit_data_flow(catalog, access, *, source_ids=None):
    """Compare each source's declared permission scope with its registered route."""
    issues = []

    def issue(code, location):
        item = {"code": code, "location": location}
        if item not in issues:
            issues.append(item)

    catalog_source_ids = {source.id for source in catalog.sources}
    audited_source_ids = (catalog_source_ids if source_ids is None
                          else set(source_ids) & catalog_source_ids)
    if not audited_source_ids:
        return {"issues": [], "source_path_count": 0, "endpoint_count": 0}

    endpoint_by_id = {}
    expected_kinds = {
        "service_ids": ("processing_service", "permitted_service_ids"),
        "storage_ids": ("storage", "permitted_storage_ids"),
        "transfer_ids": ("transfer", "permitted_transfer_ids"),
    }
    for endpoint in access.endpoints:
        if endpoint.id in endpoint_by_id:
            issue("processing_endpoint_duplicate", endpoint.id)
        else:
            endpoint_by_id[endpoint.id] = endpoint

    paths = {}
    for path in access.source_paths:
        if path.source_id in paths:
            if path.source_id in audited_source_ids:
                issue("source_path_duplicate", path.source_id)
        else:
            paths[path.source_id] = path
    source_ids = catalog_source_ids
    for source_id in sorted(paths.keys() - source_ids):
        issue("source_path_unknown", source_id)
    if not access.data_flow_verified or access.data_flow_evidence is None:
        issue("data_flow_verification_missing", "access.data_flow_evidence")
    elif access.data_flow_evidence.visibility != "public":
        issue("data_flow_verification_receipt_not_public", "access.data_flow_evidence")

    for source in catalog.sources:
        if source.id not in audited_source_ids:
            continue
        location = f"sources.{source.id}"
        if source.permission_evidence is None:
            issue("source_permission_missing", location)
        if source.permission_status == "unknown":
            issue("source_permission_unverified", location)
        elif source.permission_status == "revoked":
            issue("source_permission_revoked", location)
        if source.permission_subject is None:
            issue("permission_subject_missing", location)
        if "rag_evaluation" not in source.permitted_purposes:
            issue("evaluation_purpose_not_permitted", location)
        if "rag_evaluation" in source.prohibited_purposes:
            issue("evaluation_purpose_prohibited", location)
        if source.retention_policy is None:
            issue("retention_policy_missing", location)
        path = paths.get(source.id)
        if path is None:
            issue("source_path_missing", source.id)
            continue
        for route_field, (expected_kind, permission_field) in expected_kinds.items():
            route_ids = getattr(path, route_field)
            permitted_ids = getattr(source, permission_field)
            if len(route_ids) != len(set(route_ids)):
                issue("source_path_endpoint_duplicate", f"{location}.{route_field}")
            if len(permitted_ids) != len(set(permitted_ids)):
                issue("permission_endpoint_duplicate", f"{location}.{permission_field}")
            if route_field in ("service_ids", "storage_ids") and not route_ids:
                issue("source_path_incomplete", f"{location}.{route_field}")
            if permission_field in ("permitted_service_ids", "permitted_storage_ids") \
                    and not permitted_ids:
                issue("source_permission_scope_incomplete", f"{location}.{permission_field}")
            for endpoint_id in permitted_ids:
                endpoint = endpoint_by_id.get(endpoint_id)
                if endpoint is None:
                    issue("permission_endpoint_unknown", f"{location}.{endpoint_id}")
                elif endpoint.kind != expected_kind:
                    issue("permission_endpoint_kind_mismatch", f"{location}.{endpoint_id}")
            for endpoint_id in route_ids:
                endpoint = endpoint_by_id.get(endpoint_id)
                if endpoint is None:
                    issue("processing_endpoint_unknown", f"{location}.{endpoint_id}")
                elif endpoint.kind != expected_kind:
                    issue("processing_endpoint_kind_mismatch", f"{location}.{endpoint_id}")
                if endpoint_id not in permitted_ids:
                    issue("processing_path_not_permitted", f"{location}.{endpoint_id}")
    return {"issues": issues,
            "source_path_count": sum(source_id in paths for source_id in audited_source_ids),
            "endpoint_count": len(endpoint_by_id)}

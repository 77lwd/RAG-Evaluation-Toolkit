---
name: rag-evaluation
description: Prepare and validate a governed RAG evaluation plan with this toolkit; use for comparison design, readiness evidence, or evaluation reporting, not for claiming that a schema check proves formal readiness.
---

# RAG Evaluation

Use the toolkit to prepare an evidence-backed evaluation plan. Keep plan validation,
method readiness, run admission, execution, and quality conclusions separate.

## Prepare a comparison

Collect the evaluation purpose, baseline system, candidate systems, data split, metrics,
thresholds, team roles, and evidence locations. Ask whether the comparison is exploratory
screening or a single-candidate confirmatory plan.

Use `examples/system-snapshot.json` as the starting shape for each system, replacing its
synthetic identity with the team's identity and adding the relevant evidence references. Initialize
a separate registry, then register each snapshot:

```powershell
python -m rag_evaluation init --registry <registry-path>
python -m rag_evaluation register --registry <registry-path> --document <snapshot.json>
```

Use each returned `reference` in `examples/comparison-plan.json`; the example hashes are
placeholders and must not be treated as evidence.

To create a source-only snapshot, provide a team-defined system identity and an explicit
source-path allowlist. Repeat `--path` for the files
to fingerprint and `--allowed-path` for the complete paths permitted by the team's capture policy:

```powershell
python -m rag_evaluation capture-system --source-root <checkout> --root-id <source-root-id> --snapshot-id <snapshot-id> --system-id <system-id> --identity <human-readable-system-identity> --path service/app.py --path retrieval/search.py --allowed-path service/app.py --allowed-path retrieval/search.py
```

This reads and hashes only the selected files, observes Git identity, and emits a schema-v2
`system_snapshot` with `runtime_verified: false`. It does not run the application or verify that
the declared identity matches a deployed system. Register the returned JSON as a system snapshot;
the resulting reference can then be used by a comparison plan.

## Inventory team assets

To inventory several systems and historical evaluation materials without the legacy project
adapter, copy `examples/inventory-config.example.json` and replace its IDs, roots, and path
allowlists. Paths in `source_root` are resolved relative to the config file when not absolute.
Each system has its own ID, identity, snapshot ID, source root, selected paths, and complete
team-defined allowlist. Add as many systems as needed; by default these are asset observations,
not comparison arms and do not fix the number of versions. The optional `comparison` section
below explicitly selects which configured systems participate in one comparison plan.

Run the inventory into a new output directory:

```powershell
python -m rag_evaluation inventory-configured --config <inventory-config.json> --output <new-output-directory>
```

The command writes `inventory.json` with source hashes/Git observations, hashes of existing
public materials, and explicit missing-material entries. Materials marked `restricted` are not
read or hashed. Inventory does not parse source/material contents, assess readiness, construct a
protocol, or authorize a run. The output directory must not already exist and must be outside all
configured source roots.

To prepare comparison records from the same configuration, add an optional `comparison` object.
List each configured `system_id` exactly once with a team-defined `arm_id` and role. The object
also requires a `plan_id` and a mode of `exploratory_screening` or `confirmatory`. One baseline
and one or more candidates are required; confirmatory mode permits exactly one candidate. The
inventory command then writes a `comparison-plan.draft.json` and one independently registerable
schema-v2 file per system under `system-snapshots/`. Its response and `inventory.json` list those
paths. Snapshot hashes in the draft plan are computed from those exact records, so do not edit the
generated snapshot files before registering them.

Register the bundle in one command. It verifies that the snapshot files match the embedded
inventory records and that the plan's content references resolve before appending anything:

```powershell
python -m rag_evaluation init --registry <registry-path>
python -m rag_evaluation register-comparison-bundle --registry <registry-path> --bundle <output> --reference-output <plan-ref.json>
```

The command writes the exact `plan_reference` to `<plan-ref.json>`, ready to pass directly to
`review-comparison`. Reusing that path is safe only when it already contains the same reference;
other existing contents are never overwritten. The import is safe to retry: exact latest records
already present are reused, while conflicting or superseded records block the import before new
records are appended. Generated records remain drafts: registration does not establish formal
readiness, freeze a plan, authorize execution, or verify runtime identity. A source snapshot
records Git/source observations only and always has `runtime_verified: false`.

Use stable, team-defined `arm_id` values. The portable model has one `baseline` arm and one
or more `candidate` arms; labels such as A/B/C are display aliases only. Each candidate is
compared with the same baseline. Do not create implicit all-pairs comparisons.

For example, a team can compare `baseline-prod`, `candidate-r7`, and
`candidate-r8-reranker` in one exploratory screening plan. Give each system its own registered
`system_snapshot`; the number and names of candidate arms are not fixed to A/B or A/B/C.

For exploratory screening, every candidate comparison remains exploratory. Do not declare an
overall winner or turn a selected candidate's screening result into a confirmatory conclusion.
Confirmatory plans accept exactly one candidate. If a candidate was selected after screening,
its confirmation needs a separately preregistered, unexposed holdout or equivalent independently
justified data; do not reuse screening outcomes as confirmation.

## Validate the plan

Save the plan as JSON and run:

```powershell
python -m rag_evaluation validate --document <plan.json>
```

This command validates record shape and comparison cardinality. A `valid` response is only a
schema result; it does not establish dataset independence, permissions, identity separation,
reviewer independence, calibration, approvals, method readiness, or run authorization. The
response keeps `run_authorized` false.

Use the toolkit's `review-method` and `check` workflows for their respective evidence checks.
Resolve reported blockers with the responsible organization; never waive a blocker because the
operator is working alone or because evidence was declared in chat.

To verify that a comparison plan and its system snapshots resolve to registered content, run:

```powershell
python -m rag_evaluation review-comparison --registry <registry-path> --reference <plan-ref.json>
```

This checks registry integrity, exact content references, and whether a newer revision has
superseded a referenced plan or system snapshot. It does not assess formal readiness. A `valid`
integrity result still returns `formal_readiness: not_assessed`, lists evidence that needs
organizational review, exits blocked, and never authorizes execution.

## Submit readiness evidence

After `review-comparison` confirms that the plan and snapshots resolve, generate a readiness
scaffold bound to that exact registered plan:

```powershell
python -m rag_evaluation draft-readiness --registry <registry-path> --reference <plan-ref.json> --readiness-id <readiness-id> --output <readiness-draft.json> --unit-id <unit-id>
```

The scaffold follows the configured plan reference and the toolkit's current readiness profile.
It contains `TODO` evidence paths, zero digests, and `TODO` person IDs. It is not evidence and
cannot pass readiness review until the team replaces every placeholder with real artifacts and
distinct people. Repeat `--unit-id` for every opaque review unit; the command then generates two
`TODO` reviewer slots for every arm/unit pair, using the plan's actual arm IDs. Replace all of
those with distinct human reviewers. Without `--unit-id`, the assignment table is empty. Do not
use one operator under multiple IDs to simulate independent roles.

Alternatively, use `examples/readiness-submission.json` as a starting shape, replace the
comparison-plan reference, and register one evidence item for each required category:
`scope_and_sampling`, `dataset_permission`, `split_and_exposure_audit`,
`gold_rubric_and_calibration`, `metrics_statistics_and_sample_size`, `blind_review_plan`,
`access_and_data_flow`, `system_runtime_identity`, `run_safety_and_recovery`,
`serious_error_audit`, and `approvals_and_signoff`. Each item contains a category and an
`ArtifactRef`. Bind each evidence root with `--root ID=PATH` when reviewing.

The readiness example is a three-arm template with synthetic IDs and zeroed placeholder hashes.
Replace its plan reference, person/reviewer IDs, evidence paths, byte sizes, and every placeholder
digest before registration. Generate a digest from the evidence root without printing file
contents:

```powershell
python -m rag_evaluation fingerprint --source-root <evidence-root> --root-id evaluation-evidence --path <relative-evidence-path>
```

Copy the returned `ArtifactRef` into the matching evidence category. A placeholder hash is not a
usable evidence reference.

Declare distinct `evaluation_owner`, `data_custodian`, `independent_auditor`, `annotator`,
`gold_reviewer`, and `adjudicator` roles. For each opaque review unit and each comparison arm,
declare at least two different human reviewers. A reviewer assigned to one arm for a unit must not
be assigned to another arm for that unit. Model assignments do not count toward independent human
review. This checks declared separation only; the toolkit does not authenticate identities or
account-level access.

```powershell
python -m rag_evaluation register --registry <registry-path> --document <readiness-submission.json> --reference-output <readiness-ref.json>
python -m rag_evaluation review-readiness --registry <registry-path> --reference <readiness-ref.json> --root evidence=<evidence-root>
```

The current readiness profile keeps its required responsibility slots and evidence categories
stable. A team supplies its own person IDs for each responsibility, reviewer IDs, evidence files,
and root/path bindings. The toolkit does not currently let an organization rename or remove a
required responsibility or evidence category; those controls are part of the profile, not system
labels.

A complete report means only `complete_for_independent_review`. It does not authenticate the
declared people, establish that evidence is true, assess formal readiness, or authorize a run.
Restricted evidence is not opened and cannot be reported as digest-verified by this command.

## Freeze and verify a portable comparison

After the readiness package is complete and independently reviewed by the organization, the
declared data custodian can create a metadata seal:

```powershell
python -m rag_evaluation freeze-comparison --registry <registry-path> --reference <readiness-ref.json> --freeze-id <seal-id> --actor-id <custodian-id> --root evidence=<evidence-root>
```

Store the returned `freeze_head` outside the registry. Later, verify the seal against that
independent anchor:

```powershell
python -m rag_evaluation verify-comparison-freeze --registry <registry-path> --reference <freeze-ref.json> --expected-freeze-head <freeze-head> --root evidence=<evidence-root>
```

The seal binds the comparison plan, readiness submission, review digest, registry head, and
tool identity. Verification fails if the reference digest or anchor is wrong, evidence changes,
the plan/readiness is superseded, or the review no longer passes. A valid metadata seal is not
formal readiness and never authorizes evaluation execution.

## Preserve evaluation boundaries

- A solo operator may prepare materials and run clearly labeled diagnostics, but cannot satisfy
  independent human reviewer roles by acting multiple times.
- For formal blind review, preserve distinct human reviewer roles. Agent scoring is not human
  review and cannot substitute for those roles.
- Do not infer legal permission, real access controls, account isolation, or system identity from
  a local checkout or a successful schema check.
- Treat v1 records as historical input. Do not rewrite them into v2 unless an explicit adapter
  and conversion report are available.
- Do not run candidate systems, upload restricted data, or claim formal results without the
  organization's required authorization and evidence.

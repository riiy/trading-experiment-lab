# FULL_PIPELINE_RECALCULATION_MANIFEST_V2_AUDIT_1

## Decision

```yaml
decision: MANIFEST_V2_AUDIT_PASSED
implementation_commit: 616b6cdaf36def3fb85e03d82dc58e9884b7a50d
manifest_v2_audited: true
status: manifest_freeze_authorized
formal_recalculation_run_authorized: false
trading_allowed: false
```

## Findings

The V2 builder emits a canonical self-hashed Manifest containing the runtime HEAD, the
audited `a68770e` engine identity, the `bce5ab7` engine audit record, and the independently
bound Manifest-tool commit and file hashes. Engine or tool drift fails closed.

Core inputs are opened, profiled, and hashed by the freezer. Archived comparison file
paths and expected hashes are copied from the frozen archive manifest without opening
the comparison files. At runtime, the archive manifest and original artifacts are opened
only within `DELTA_AND_DECISION` and persisted as producerless external roots restricted
to that consumer.

The V2 run command constructs `FullPipelineRunner` with all eight concrete stages. V1 and
replay manifests, legacy freeze/run commands, invalid self hashes, dirty repositories,
unsafe permissions, output conflicts, and incomplete publication contracts cannot enter
the formal path. Failed Manifest writes leave no final file.

All 213 repository tests passed. Audit execution used synthetic Manifest fixtures only.
No formal Manifest or recalculation output was generated, and no strategy, account,
ticket, or trading permission changed during the audit.

This audit authorizes a separate formal Manifest freeze. It does not authorize executing
the formal recalculation; that permission may be enabled only after the generated
Manifest is independently accepted and frozen.

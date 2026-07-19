# FULL_PIPELINE_RECALCULATION_MANIFEST_V2_IMPLEMENTATION

## Remediation 1 contract

```yaml
status: implementation_pending_reaudit
historical_manifest_tool_commit: 616b6cdaf36def3fb85e03d82dc58e9884b7a50d
historical_audit_decision: MANIFEST_V2_AUDIT_PASSED
formal_contract_eligible: false
formal_recalculation_run_authorized: false
```

## Commands

```text
freeze-stock-rs-pullback-recalculation-v2
validate-stock-rs-pullback-recalculation-manifest-v2
run-stock-rs-pullback-recalculation-v2
```

The V2 freezer binds the runtime repository HEAD, the audited `a68770e` engine, its
`bce5ab7` audit record, and separate Manifest-tool implementation and audit-record
commits. It records
audited engine and Manifest-tool file hashes, profiles and hashes core inputs, copies
comparison references only from the frozen archive manifest, and writes a canonical
self-hashed JSON document atomically.

The frozen document separates current authorization from allowed output capability.
`authorization_snapshot.formal_recalculation_run_authorized` is always false at freeze
time. `run_capabilities.strategy_validation_classification_output` is true, while
account simulation, tickets, and trading remain false in both sections. The deprecated
`permissions` compatibility view remains fully closed on disk. After an external
Registry authorization, the V2 CLI enables only the audited runner's in-memory legacy
execution gate; it neither rewrites the Manifest nor expands its capabilities.

The publication contract explicitly requires an absent final root, atomic rename,
fsync, read-only publication, a completion record, and a durable artifact hash chain.

The formal validator rejects replay/V1 manifests, invalid self hashes, dirty or mismatched
repositories, engine or Manifest-tool drift, comparison consumers outside Delta, open
trading permissions, and existing temporary or final output roots. It never opens the
comparison files themselves.

The V2 run route constructs the audited eight-stage `FullPipelineRunner`. An adapter on
`DELTA_AND_DECISION` verifies and registers the frozen archive manifest as an external
comparison root. Legacy freeze/run commands are explicitly classified as
`SIGNAL_EXECUTION_REPLAY` and cannot enter the formal path.

No remediated V2 Manifest may be frozen until
`FULL_PIPELINE_RECALCULATION_MANIFEST_V2_REAUDIT_1` passes and the Registry binds both
the new implementation commit and its separate audit-record commit. Passing that audit
authorizes only Manifest freezing; a separately validated and frozen Manifest is
required before formal execution can be authorized.

# FULL_PIPELINE_RECALCULATION_MANIFEST_V2_IMPLEMENTATION

## Status

```yaml
status: implementation_pending_audit
manifest_v2_implemented: true
manifest_v2_audited: false
formal_recalculation_run_authorized: false
```

## Commands

```text
freeze-stock-rs-pullback-recalculation-v2
validate-stock-rs-pullback-recalculation-manifest-v2
run-stock-rs-pullback-recalculation-v2
```

The V2 freezer binds the current repository HEAD, the audited `a68770e` engine, its
`bce5ab7` audit record, and an independently audited Manifest-tool commit. It records
audited engine and Manifest-tool file hashes, profiles and hashes core inputs, copies
comparison references only from the frozen archive manifest, and writes a canonical
self-hashed JSON document atomically.

The formal validator rejects replay/V1 manifests, invalid self hashes, dirty or mismatched
repositories, engine or Manifest-tool drift, comparison consumers outside Delta, open
trading permissions, and existing temporary or final output roots. It never opens the
comparison files themselves.

The V2 run route constructs the audited eight-stage `FullPipelineRunner`. An adapter on
`DELTA_AND_DECISION` verifies and registers the frozen archive manifest as an external
comparison root. Legacy freeze/run commands are explicitly classified as
`SIGNAL_EXECUTION_REPLAY` and cannot enter the formal path.

No V2 Manifest may be frozen until `FULL_PIPELINE_RECALCULATION_MANIFEST_V2_AUDIT_1`
passes. Passing that audit authorizes only Manifest freezing; a separately validated and
frozen Manifest is required before formal execution can be authorized.

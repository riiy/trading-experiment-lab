# Original Metrics Comparison Archive V1

This contract completes the historical comparison bundle used only by
`DELTA_AND_DECISION` in `FULL_PIPELINE_RECALCULATION_V2`.

The archive is append-only and self-hashed. It references the historical audit
manifest, verifies its signals and trades hashes before adding the original
metrics JSON reference, and never changes an original artifact.

The independent audit recomputes overall and yearly return statistics directly
from the archived original trades. Its result is archival integrity evidence,
not a new strategy validation classification. Formal recalculation, account
simulation, ticket generation, and trading remain disabled.

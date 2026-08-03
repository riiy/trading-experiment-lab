# Manifest V2 Reaudit 4

Decision: `MANIFEST_V2_AUDIT_PASSED`

The former exact-HEAD check made a frozen Manifest unusable once a clean,
committed run authorization changed the repository HEAD. The remediation keeps
the freeze HEAD in the self-hashed Manifest as provenance and requires the
runtime HEAD to be a Git descendant. It continues to require a clean worktree,
unchanged audited engine and Manifest-tool hashes, unchanged core-input hashes,
and a valid self-hash.

Evidence:

- `30 passed` for `test_formal_manifest_v2.py` and `test_config.py`.
- `config-check: OK`, with trading disabled.
- The new descendant-head acceptance regression passed.
- `formal_manifest.py` and `manifest_validation.py` match the implementation
  commit hashes recorded in `audit_manifest.json`.

This is a tool audit only. No formal Manifest was frozen, no full pipeline ran,
and no strategy decision, account simulation, ticket, or trading permission was
generated.

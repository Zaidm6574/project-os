# Legacy Resolver Cohorts

The full-engine installer (`scripts/install_full_engine.py`) upgrades a
project's shared-brain *resolver cohort* — the set of files that decide where
`shared-brain.jsonl` lives. Older published cohorts resolved the brain to the
machine-global `~/.project-os/central-brain/shared-brain.jsonl`; the canonical
resolver (`scripts/brain_paths.py`) resolves it to `<project>/brain/`.

The migration preflight uses the generic default
`~/.project-os/central-brain`. For a legacy brain stored elsewhere, set
`PROJECT_OS_LEGACY_CENTRAL_BRAIN=/absolute/path/to/central-brain`, or pass
`--legacy-central-brain /absolute/path/to/central-brain` directly to
`scripts/install_full_engine.py`. The CLI option takes precedence over the
environment variable; both take precedence over the default. This override
only selects the legacy migration source; it does not change the project-local
default or the explicit `PROJECT_OS_SHARED_BRAIN` resolver.

Upgrading a project that still runs a legacy cohort while
`~/.project-os/central-brain/shared-brain.jsonl` holds data would silently
shadow that data behind a fresh empty local brain. To prevent that, the
installer:

1. Hashes every resolver file already present in the target with SHA-256 and
   only recognizes the *exact, unedited* cohorts listed below. Anything
   edited, unknown, or mixed between cohorts refuses the upgrade — even with
   `--force` — and asks you to review the local changes and migrate manually.
2. Checks the legacy central brain (active + archive) under the current
   user's home for data, holding a `scripts/bb_lock.py` lease on the active
   brain while it snapshots.
3. If a recognized legacy cohort coexists with populated legacy data, requires
   an explicit decision: `--brain-migration {migrate,bind,fresh-local}`.

Modes:

- `migrate` — copy the legacy active + archive brains byte-for-byte into
  `<target>/brain/` (restrictive permissions preserved, publication fenced by
  the bb_lock lease) and write
  `brain/shared-brain-migration-receipt.jsonl`
  (`project-os/shared-brain-migration-receipt/v1`).
- `bind` — copy nothing; write only
  `brain/shared-brain-binding.jsonl`
  (`project-os/shared-brain-binding/v1`) pointing at the legacy absolute
  path.
- `fresh-local` — leave the legacy data untouched in place, start an empty
  local brain, and record the decision in the migration receipt.

## Recognized cohort signatures (SHA-256)

The addon copies `addons/full-engine/brain/brain.py` and
`addons/full-engine/brain/central_brain.py` install as `brain/brain.py` and
`brain/central_brain.py`; they share the digests listed for those installed
paths.

### `published-v0.1.0-v0.1.1`

Tagged v0.1 releases (resolver logic lived only in the brain scripts).

| Installed file | SHA-256 |
| --- | --- |
| `brain/brain.py` | `74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c` |
| `brain/central_brain.py` | `04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359` |

### `published-f034ea5`

| Installed file | SHA-256 |
| --- | --- |
| `memory/mneme_adapter.py` | `3de3551e4cbf102e6b874fefa9e90b9aa9d65ced690ea293a18483018c693632` |
| `scripts/brain_append.py` | `371262dd22704524e47cf93dd931ff29c270a32a87f8cc32c9a432fb416bfb5a` |
| `scripts/brain_scale.py` | `2471330128bf5f747d607468d709dc9b30674f65017b686f6c4340f222209432` |
| `scripts/harvest.py` | `2ac5862df95bf9a0b69fd26c0c06cf76b7253f3140eed42acb67b22b2fdcb56a` |
| `brain/brain.py` | `74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c` |
| `brain/central_brain.py` | `04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359` |

### `published-07b5805`

| Installed file | SHA-256 |
| --- | --- |
| `memory/mneme_adapter.py` | `d852f669fadfa1d84fe9ce5a06eee0655a1914b9be3c6d2c92e38f99c4aeb17c` |
| `scripts/brain_append.py` | `371262dd22704524e47cf93dd931ff29c270a32a87f8cc32c9a432fb416bfb5a` |
| `scripts/brain_archive.py` | `e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be` |
| `scripts/brain_scale.py` | `8f9587e33d26fa64033e059cfa993fe57c91cfbe7a52c2a1347cd6e3a3a1715c` |
| `scripts/harvest.py` | `2ac5862df95bf9a0b69fd26c0c06cf76b7253f3140eed42acb67b22b2fdcb56a` |
| `brain/brain.py` | `74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c` |
| `brain/central_brain.py` | `04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359` |

### `published-df17b3f`

| Installed file | SHA-256 |
| --- | --- |
| `memory/mneme_adapter.py` | `fc08f910ca9d39f9d62bdd91032a598bb03ae5757ae7cc7dcdb89a7575667ccf` |
| `scripts/brain_append.py` | `4420cdd64263287a57c0dca3522e522dcf30608017762812f1c22b936afa5db6` |
| `scripts/brain_archive.py` | `e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be` |
| `scripts/brain_scale.py` | `242e67a58dc4119db5f6b2dd3da666c3d02d7e6e8440a5223e527d2c0393fb7d` |
| `scripts/harvest.py` | `a88a4a91813ce74ea9ed172956e36d817c64c2b8a5416e83caf5e08ff2a8ac33` |
| `brain/brain.py` | `74df69f9c1f4db51e497fc779866d8f70e96da51500b169e016c7d1c571c5c6c` |
| `brain/central_brain.py` | `04f95e4ccb8b5419fca1fcc3f5f6ade5c09fa3eded469310262ed0031e0b8359` |

### `published-e4c6ca0`

| Installed file | SHA-256 |
| --- | --- |
| `memory/mneme_adapter.py` | `fc08f910ca9d39f9d62bdd91032a598bb03ae5757ae7cc7dcdb89a7575667ccf` |
| `scripts/brain_append.py` | `327fac967595174fcf89d6bb19d80bbe576b1dbea02c9fe099804b2b6473d543` |
| `scripts/brain_archive.py` | `e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be` |
| `scripts/brain_scale.py` | `ebe30be1eff425718ac0cc889130d815aafadfa07161f423fd978455ef1d3f3f` |
| `scripts/harvest.py` | `a88a4a91813ce74ea9ed172956e36d817c64c2b8a5416e83caf5e08ff2a8ac33` |
| `brain/brain.py` | `c9d27a0d559eec65c61f18430b6792bd776cb283ea27e8611fc49039f654a85e` |
| `brain/central_brain.py` | `335b2571fc0f4b7e6a24561841b62829d2c6a6936ca8e83897da0a9e3e684c7e` |

### `published-ef0cb9`

| Installed file | SHA-256 |
| --- | --- |
| `memory/mneme_adapter.py` | `fc08f910ca9d39f9d62bdd91032a598bb03ae5757ae7cc7dcdb89a7575667ccf` |
| `scripts/brain_append.py` | `327fac967595174fcf89d6bb19d80bbe576b1dbea02c9fe099804b2b6473d543` |
| `scripts/brain_archive.py` | `e645147ba2179ee82a9ab1444f3195ead4be436de1c68e5e6be44f341add89be` |
| `scripts/brain_scale.py` | `d6da473483a6d4c2baefe026119ea9848eaa57dc2706db780b2ed068c74b7e53` |
| `scripts/harvest.py` | `a88a4a91813ce74ea9ed172956e36d817c64c2b8a5416e83caf5e08ff2a8ac33` |
| `brain/brain.py` | `c9d27a0d559eec65c61f18430b6792bd776cb283ea27e8611fc49039f654a85e` |
| `brain/central_brain.py` | `335b2571fc0f4b7e6a24561841b62829d2c6a6936ca8e83897da0a9e3e684c7e` |

### `verified-pre-canonical-2026-07-26`

Verified working-tree cohort captured just before the canonical resolver
landed (never tagged, but present on upgraded local installs).

| Installed file | SHA-256 |
| --- | --- |
| `memory/mneme_adapter.py` | `0a7f56aa92bae3d480d0abff1a087c1aad5cd8f22907996f3584104e32e85235` |
| `scripts/brain_append.py` | `b4e9f879f5487e564707173d5505eba5bdc14c3a6de648917172f82b6684d50f` |
| `scripts/brain_archive.py` | `ea5ac7faf19599b8511b1fba23a2d461c1325f0d4594b5aaabe57431592cc5c4` |
| `scripts/brain_scale.py` | `2553c63cc35d092c5a3c055eeb98029da58469ce26c81beff36975c01c6defbb` |
| `scripts/harvest.py` | `9888271cb564d91cb2d7e61bdd12966eb79b922b0001ff11c016806e38af007d` |
| `brain/brain.py` | `56e17b53a792f0abc9df0fcd6dd342fa8c17749089168abac0bab251be40bedf` |
| `brain/central_brain.py` | `d163b1a6ca1a5619a7ba903d5220004a407254651b2e710e888a0def3adbde15` |

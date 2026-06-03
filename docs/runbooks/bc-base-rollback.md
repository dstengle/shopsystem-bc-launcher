# Runbook: rolling back shopsystem-bc-base `latest`

Pins the rollback procedure for scenario 41 (`be11d615375564e1`) as a
declarative artifact. Live registry / live Actions state is out-of-band
(scenario-40 precedent); this runbook is the documented re-tag procedure.

## Why rollback needs no rebuild

The `publish-bc-base.yml` workflow tags every release with its immutable
`v*` version tag in addition to `latest`. Because each prior digest stays
pullable by its own version tag, rolling back is a matter of re-pointing the
moving `latest` tag at an already-published earlier digest — no new image
build is required.

## Procedure

1. Identify the last known-good version, e.g. `v0.1.4`, whose digest
   `D_good` is already published at `ghcr.io/dstengle/shopsystem-bc-base`.
2. Re-point `latest` at `D_good` without rebuilding:

   ```
   docker buildx imagetools create \
     --tag ghcr.io/dstengle/shopsystem-bc-base:latest \
     ghcr.io/dstengle/shopsystem-bc-base:v0.1.4
   ```

   This re-tags the existing `D_good` digest in place; `latest` now resolves
   to `D_good`.
3. Verify `latest` resolves to `D_good`:

   ```
   docker buildx imagetools inspect ghcr.io/dstengle/shopsystem-bc-base:latest
   ```

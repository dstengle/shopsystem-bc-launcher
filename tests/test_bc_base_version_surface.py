"""pytest-bdd binding for the bc-base/bc-lead version-surface feature (lead-5xnd).

The published bc-base + bc-lead images must surface the bc-launcher release
version and the baked shop-templates version via OCI labels and ENV, OVERRIDING
the misleading upstream devcontainer-base org.opencontainers.image.version label
value "3.1.2".

docker is NOT available in this environment, so (per the scenario-40
declarative-artifact precedent) these scenarios are pinned at the honest
fidelity level: the committed publish-bc-base.yml `labels:` inputs and the
committed Dockerfile ENV instructions are asserted by parsing the REAL workflow
YAML and Dockerfile text.  The live `docker image/container inspect` of the
published image is the lead's post-release pull verification, out of band.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_base_version_surface.feature")

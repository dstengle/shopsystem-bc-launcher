@bc:shopsystem-bc-launcher @origin:adr-049
Feature: bc-base bakes fabro v0.267.0-nightly.0 and the anthropic-oauth-shim, and the centralized poll enrolls fabro (lead-ckq5)

  bc-base bakes the fabro binary (pinned v0.267.0-nightly.0 from fabro-sh/fabro)
  and a real stdlib-only anthropic-oauth-shim launcher, both present + launchable
  in the running container; and the single centralized scheduled poll enrolls
  fabro as a 6th baked dependency against fabro-sh/fabro, resolving with the
  workflow's own GITHUB_TOKEN and bump-then-rebuilding :latest on a newer
  release.

  FIDELITY: docker is unavailable in this environment. fabro is a binary that
  cannot run in-env, so scenario acc72693771d8c6b's fabro leg binds to the
  docker/bc-base/Dockerfile install (pinned v0.267.0-nightly.0 from fabro-sh/fabro
  onto PATH, a real download RUN — comment-stripped detection); the live
  `fabro --version` is the lead's pull verification, also gated by the
  build-time self-check. The anthropic-oauth-shim is a REAL committed file, so
  the test EXECUTES the actual committed shim (`python3 <shim> --help`) and
  asserts exit 0 AND that it imports no third-party module (stdlib-only). The
  poll scenario ea139400f8efb546 binds to the committed poll workflow's
  executable body via _strip_yaml_comments (comment-only mapping does NOT
  satisfy — the 5vyb pattern).

  # RETIRED-SCENARIO PROVENANCE (work_id lead-ifye3.8):
  #   a3512aedb8763150  superseded-by  acc72693771d8c6b
  #   4fc67c610cba6227  superseded-by  ea139400f8efb546
  #   reason: the lead re-pinned both scenarios to bump the hardcoded fabro
  #     release literal v0.254.0 -> v0.267.0-nightly.0 (ADR-010 pin authority),
  #     resolving lead-ifye3.7's block: the FABRO_VERSION bump regressed these
  #     two v0.254.0-literal-pinned scenarios and re-pinning is lead authority.
  #     The ONLY change vs the retired bodies is the fabro version literal; the
  #     bound behavior (baked fabro version + oauth-shim; centralized poll
  #     enroll+bump-rebuild) is otherwise byte-for-byte identical.
  #
  #   The retired bodies are preserved verbatim below for provenance, each line
  #   prefixed with the marker "#| " (strip those 3 chars to recover the
  #   byte-identical block that recomputes its retired @scenario_hash). They are
  #   comment lines, NOT scenario regions, so they do not re-pin (avoids the
  #   lead-83mh8 E_HASH_MISMATCH defect).
  #
  # --- retired a3512aedb8763150 (superseded-by acc72693771d8c6b) ---
#|   @scenario_hash:a3512aedb8763150
#|   Scenario: a launched bc-base BC has fabro v0.254.0 and the anthropic-oauth-shim present and launchable
#|     Given the shopsystem-bc-launcher BC is installed
#|     And bc-container launch is run with BC name "shopsystem-messaging"
#|     And the container "bc-shopsystem-messaging" is running on the pinned bc-base image
#|     When "fabro --version" is executed inside that running container
#|     Then it exits zero and reports the fabro version "v0.254.0"
#|     And the anthropic-oauth-shim is resolvable inside the container as a baked launcher, and invoking that launcher with its usage/help flag exits zero using the python standard library alone with no third-party import required
#|     And both fabro and the anthropic-oauth-shim are real baked artifacts present in the running container, not placeholders and not merely declared in the image manifest
  #
  # --- retired 4fc67c610cba6227 (superseded-by ea139400f8efb546) ---
#|   @scenario_hash:4fc67c610cba6227
#|   Scenario: the centralized poll enrolls fabro as a baked dependency against fabro-sh/fabro and bump-rebuilds :latest on a newer release
#|     Given the bc-base Dockerfile in shopsystem-bc-launcher bakes fabro at pin "v0.254.0" as a baked dependency alongside shop-templates, shop-msg, scenarios, and beads
#|     And the single centralized scheduled bc-launcher workflow is the one poll that check-bump-rebuilds bc-base for its baked dependencies
#|     When the workflow's dependency check runs and its executable body, with YAML comment lines excluded, is inspected
#|     Then the executable body enumerates "fabro" mapped to its canonical public release source "fabro-sh/fabro"
#|     And a "fabro" to "fabro-sh/fabro" mapping present only in a descriptive YAML comment, absent from the executable body, does not satisfy this enrollment
#|     And the fabro latest-release lookup reads the public "fabro-sh/fabro" releases using the workflow's own "GITHUB_TOKEN" and references no "BC_LAUNCHER_DISPATCH_TOKEN" or any other cross-repo dispatch credential
#|     And when the resolved latest fabro release tag differs from the baked "v0.254.0" pin, the workflow first mutates the Dockerfile fabro pin to the resolved tag, then rebuilds bc-base and republishes "ghcr.io/dstengle/shopsystem-bc-base:latest" at the new digest
#|     And a bare rebuild that left the Dockerfile fabro pin at "v0.254.0" would not satisfy this behavior

  @scenario_hash:acc72693771d8c6b @bc:shopsystem-bc-launcher
  Scenario: a launched bc-base BC has fabro v0.267.0-nightly.0 and the anthropic-oauth-shim present and launchable
    Given the shopsystem-bc-launcher BC is installed
    And bc-container launch is run with BC name "shopsystem-messaging"
    And the container "bc-shopsystem-messaging" is running on the pinned bc-base image
    When "fabro --version" is executed inside that running container
    Then it exits zero and reports the fabro version "v0.267.0-nightly.0"
    And the anthropic-oauth-shim is resolvable inside the container as a baked launcher, and invoking that launcher with its usage/help flag exits zero using the python standard library alone with no third-party import required
    And both fabro and the anthropic-oauth-shim are real baked artifacts present in the running container, not placeholders and not merely declared in the image manifest

  @scenario_hash:ea139400f8efb546 @bc:shopsystem-bc-launcher
  Scenario: the centralized poll enrolls fabro as a baked dependency against fabro-sh/fabro and bump-rebuilds :latest on a newer release
    Given the bc-base Dockerfile in shopsystem-bc-launcher bakes fabro at pin "v0.267.0-nightly.0" as a baked dependency alongside shop-templates, shop-msg, scenarios, and beads
    And the single centralized scheduled bc-launcher workflow is the one poll that check-bump-rebuilds bc-base for its baked dependencies
    When the workflow's dependency check runs and its executable body, with YAML comment lines excluded, is inspected
    Then the executable body enumerates "fabro" mapped to its canonical public release source "fabro-sh/fabro"
    And a "fabro" to "fabro-sh/fabro" mapping present only in a descriptive YAML comment, absent from the executable body, does not satisfy this enrollment
    And the fabro latest-release lookup reads the public "fabro-sh/fabro" releases using the workflow's own "GITHUB_TOKEN" and references no "BC_LAUNCHER_DISPATCH_TOKEN" or any other cross-repo dispatch credential
    And when the resolved latest fabro release tag differs from the baked "v0.267.0-nightly.0" pin, the workflow first mutates the Dockerfile fabro pin to the resolved tag, then rebuilds bc-base and republishes "ghcr.io/dstengle/shopsystem-bc-base:latest" at the new digest
    And a bare rebuild that left the Dockerfile fabro pin at "v0.267.0-nightly.0" would not satisfy this behavior

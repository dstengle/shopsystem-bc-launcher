@bc_internal
Feature: bc-base PID 1 is a reaping init (tini) that wraps the agent-vault CA entrypoint (lead-xnop)

  This is a BC-INTERNAL structural hardening (bug lead-xnop). It is NOT a
  lead-assigned scenario: the @bc_internal tag below is a BC-owned marker, NOT
  a lead @scenario_hash. docker build is NOT run (docker is unavailable in this
  environment); these scenarios parse the COMMITTED Dockerfile CONTENT, the
  same structural-inspection idiom as the bc-base CA-trust / healthcheck /
  CLI-pin tests.

  THE GAP this closes: bc-base's PID 1 was the CA-materialization entrypoint
  (agent-vault-ca.sh) exec'ing `sleep infinity` — a process that performs NO
  child reaping. Orphaned children of in-container tooling (bd, git, fabro, gh,
  shop-msg) reparent to PID 1 and, with no reaper, accumulate as <defunct>
  zombies forever. EMPIRICAL: an --orchestrator fabro BC hit 540 zombies (532
  bd) in ~15 minutes (the ADR-058 dispatcher poll spawns bd every ~5s and the
  parents exit unreaped).

  THE FIX (lead's stated preference): bake `tini` into the image and make it
  PID 1 by WRAPPING the existing CA entrypoint —
  `ENTRYPOINT ["tini", "--", "/usr/local/bin/agent-vault-ca.sh"]`. tini becomes
  PID 1 and reaps zombies regardless of run flags (covers the clone-path AND
  the workspace-mount launch), and execs agent-vault-ca.sh as its child so CA
  materialization + the five trust vars + the baked framework CLIs are all
  preserved. This wraps — it does NOT replace — the pinned CA entrypoint.

  @bc_internal @bc:shopsystem-bc-launcher
  Scenario: the bc-base Dockerfile installs the tini reaping-init binary
    Given the shopsystem-bc-launcher BC repository
    When the bc-base Dockerfile in that repository is inspected
    Then the Dockerfile installs the tini reaping-init binary

  @bc_internal @bc:shopsystem-bc-launcher
  Scenario: PID 1 is a reaping init that wraps the agent-vault CA entrypoint
    Given the shopsystem-bc-launcher BC repository
    When the bc-base Dockerfile in that repository is inspected
    Then the Dockerfile ENTRYPOINT is the tini reaping init in exec form
    And the tini ENTRYPOINT wraps the agent-vault CA entrypoint script as its child
    And the CMD remains "sleep infinity" so the wrapped entrypoint keeps the container alive

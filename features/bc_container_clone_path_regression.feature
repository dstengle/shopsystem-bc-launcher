Feature: bc-container launch clone-path regression guards (lead-uiwu)

  @scenario_hash:bdec2754d9135086 @bc:shopsystem-bc-launcher
    Scenario: bc-container launch with no --repo-url and no --workspace-mount resolves the BC remote from bc-manifest.yaml and clones it into the container's /workspace
      Given the shopsystem-bc-launcher BC is installed
      And the bc-manifest.yaml registers the BC "shopsystem-templates" with a valid git remote URL, and is the declared source of remote URLs when launching BCs
      And no "--repo-url" flag and no "--workspace-mount" flag are provided
      And no Docker container named "bc-shopsystem-templates" is running
      When I run bc-container launch with BC name "shopsystem-templates"
      And the container starts
      Then the command exits zero
      And the "/workspace" directory inside the running container "bc-shopsystem-templates" is a git repository cloned from the remote URL registered for "shopsystem-templates" in bc-manifest.yaml

  @scenario_hash:0b50d090c9cc3c45 @bc:shopsystem-bc-launcher
    Scenario: bc-container launch fails loudly when no repo source is resolvable instead of silently launching an empty /workspace
      Given the shopsystem-bc-launcher BC is installed
      And no "--repo-url" flag and no "--workspace-mount" flag are provided
      And bc-manifest.yaml carries no resolvable git remote URL for the BC "shopsystem-norepo"
      When I run bc-container launch with BC name "shopsystem-norepo"
      Then the command exits non-zero
      And the error output explicitly states that no repo source — neither "--repo-url", "--workspace-mount", nor a bc-manifest.yaml remote — could be resolved for "shopsystem-norepo"
      And the launch does not silently succeed leaving an empty, non-git "/workspace"

  @scenario_hash:4154b0ea63d0516b @bc:shopsystem-bc-launcher
    Scenario: a launched BC's /workspace is owned by the agent user so the in-container clone performed as that user succeeds without Permission denied
      Given the shopsystem-bc-launcher BC is installed
      And bc-container launch is run with BC name "shopsystem-templates" with a valid repo URL
      And the container "bc-shopsystem-templates" is running
      When the ownership of the "/workspace" directory inside the running container is inspected
      Then "/workspace" is owned by the agent user "vscode" (uid 1000), not by root
      And the clone performed into "/workspace" as the agent user completes without a "/workspace/.git: Permission denied" error

  @scenario_hash:0d29c76818a323a1 @bc:shopsystem-bc-launcher
    Scenario: a launched BC trusts the agent-vault MITM proxy CA before the clone runs so a clone routed through HTTPS_PROXY passes TLS verification
      Given the shopsystem-bc-launcher BC is installed
      And the agent-vault broker root CA is delivered to the launched BC as inline PEM content via "AGENT_VAULT_CA_PEM" per ADR-045
      And the launched BC routes outbound HTTPS through the agent-vault MITM proxy via "HTTPS_PROXY"
      When bc-container launch is run with BC name "shopsystem-templates" and the in-container clone of its remote is performed through "HTTPS_PROXY"
      Then the running container has installed the agent-vault MITM root CA into its git/system trust store before the clone is attempted
      And the clone routed through "HTTPS_PROXY" completes its TLS handshake without an "SSL certificate problem: unable to get local issuer certificate" error


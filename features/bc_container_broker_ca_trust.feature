Feature: bc-container launch passes the broker CA as an env var, builds no CA bind-mount

  # bclaunch-7pf (REVISED under operator design directive — supersedes the
  # 9ca2e05 CA bind-mount model): the broker substitutes credentials by
  # intercepting outbound HTTPS (HTTPS_PROXY -> TLS MITM). The broker CA is a
  # PUBLIC ~574-byte cert (NOT secret). A controller-side bind-mount of the CA
  # is UNSAFE under nested-docker / host-path mismatch and the design goal is
  # to eliminate controller bind mounts entirely. So the CA now travels as the
  # container env var AGENT_VAULT_CA_PEM (operator-supplied via --env-file) and
  # is materialized to a file + trust env vars by the bc-base entrypoint
  # (bclaunch-9rr). The controller does NO CA handling: it injects the env var
  # and builds NO CA bind-mount.

  @scenario_hash:7c3e1a9f5d8b2640 @bc:shopsystem-bc-launcher
  Scenario: the operator-supplied broker CA travels as the AGENT_VAULT_CA_PEM container env var
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies the broker CA PEM via AGENT_VAULT_CA_PEM
    And the operator supplies agent-vault addr "https://agent-vault:14321" token "av_agt_xyz" and vault "shopsystem"
    When bc-container launch is run for BC name "shopsystem-messaging" with the operator broker CA and agent-vault credentials
    Then a Docker container named "bc-shopsystem-messaging" is running
    And the container env has AGENT_VAULT_CA_PEM set to the operator-supplied broker CA PEM

  @scenario_hash:8d4f2b0a6e9c3751 @bc:shopsystem-bc-launcher
  Scenario: the controller builds no CA bind-mount and sets no controller-side TLS-trust env
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies the broker CA PEM via AGENT_VAULT_CA_PEM
    And the operator supplies agent-vault addr "https://agent-vault:14321" token "av_agt_xyz" and vault "shopsystem"
    When bc-container launch is run for BC name "shopsystem-messaging" with the operator broker CA and agent-vault credentials
    Then a Docker container named "bc-shopsystem-messaging" is running
    And no bind mount inside the container targets "/etc/agent-vault/broker-ca.pem"
    And the container env has no NODE_EXTRA_CA_CERTS key set by the controller
    And the container env has no SSL_CERT_FILE key set by the controller
    And the container env has no GIT_SSL_CAINFO key set by the controller

Feature: bc-container launch mounts the broker CA read-only and sets TLS trust env

  # bclaunch-7pf (critical path): the broker substitutes credentials by
  # intercepting outbound HTTPS (HTTPS_PROXY -> TLS MITM).  Without the broker
  # CA trusted inside the container, claude (node) and git HTTPS calls through
  # the proxy FAIL cert verification.  The launcher bind-mounts the
  # operator-supplied broker CA file READ-ONLY at a fixed container path and
  # sets NODE_EXTRA_CA_CERTS (node/claude), SSL_CERT_FILE (python/openssl) and
  # GIT_SSL_CAINFO (git) all pointing at that container CA path.

  @scenario_hash:7c3e1a9f5d8b2640 @bc:shopsystem-bc-launcher
  Scenario: the operator-supplied broker CA is bind-mounted read-only at the fixed container path
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a broker CA file
    And the operator supplies agent-vault addr "https://agent-vault:14321" token "av_agt_xyz" and vault "shopsystem"
    When bc-container launch is run for BC name "shopsystem-messaging" with the operator broker CA and agent-vault credentials
    Then a Docker container named "bc-shopsystem-messaging" is running
    And the broker CA is bind-mounted read-only into the container at "/etc/agent-vault/broker-ca.pem"

  @scenario_hash:8d4f2b0a6e9c3751 @bc:shopsystem-bc-launcher
  Scenario: the TLS-trust env vars all point at the mounted broker CA container path
    Given the shopsystem-bc-launcher BC is installed
    And the operator supplies a broker CA file
    And the operator supplies agent-vault addr "https://agent-vault:14321" token "av_agt_xyz" and vault "shopsystem"
    When bc-container launch is run for BC name "shopsystem-messaging" with the operator broker CA and agent-vault credentials
    Then a Docker container named "bc-shopsystem-messaging" is running
    And the container env has NODE_EXTRA_CA_CERTS set to "/etc/agent-vault/broker-ca.pem"
    And the container env has SSL_CERT_FILE set to "/etc/agent-vault/broker-ca.pem"
    And the container env has GIT_SSL_CAINFO set to "/etc/agent-vault/broker-ca.pem"

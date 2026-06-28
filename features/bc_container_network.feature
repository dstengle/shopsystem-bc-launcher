Feature: bc-container product-scoped Docker network naming

  @scenario_hash:229760feb4af874b @bc:shopsystem-bc-launcher
  Scenario: bc-container launch derives the Docker network name from the product field in bc-manifest.yaml
    Given a bc-manifest.yaml exists containing:
      """
      product: shopsystem product
      bcs:
        - name: shopsystem-messaging
          remote: https://github.com/dstengle/shopsystem-messaging.git
          role: bc
      """
    And no Docker container named "bc-shopsystem-messaging" is running
    And no explicit "--network" flag is provided
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the command exits zero
    And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes the flag "--network shopsystem-product"

  @scenario_hash:b0861d05f82fd0f2 @bc:shopsystem-bc-launcher
  Scenario: network name derivation slugifies the product field by lowercasing and replacing spaces with hyphens
    Given a bc-manifest.yaml exists with product field "My Ecommerce Shop"
    And no Docker container named "bc-shopsystem-messaging" is running
    And no explicit "--network" flag is provided
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes the flag "--network my-ecommerce-shop"

  @scenario_hash:f1c3a6ca6b64b713 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch exits non-zero when no bc-manifest.yaml exists and no --network flag is provided
    Given no bc-manifest.yaml exists in the working directory
    And no explicit "--network" flag is provided
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the command exits non-zero
    And stderr includes the text "no network: bc-manifest.yaml not found and --network not provided"

  @scenario_hash:add8efc2668d1cdc @bc:shopsystem-bc-launcher
  Scenario: bc-container launch creates the derived network before starting the container when the network does not exist
    Given a bc-manifest.yaml exists with product field "shopsystem product"
    And no Docker network named "shopsystem-product" exists
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the command exits zero
    And the FakeDockerDriver records that "docker network create shopsystem-product" was called before "docker run"
    And a Docker network named "shopsystem-product" exists

  @scenario_hash:7bfab6f4f71e5a6e @bc:shopsystem-bc-launcher
  Scenario: bc-container launch does not attempt to create the network when it already exists
    Given a bc-manifest.yaml exists with product field "shopsystem product"
    And a Docker network named "shopsystem-product" already exists
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container launch with BC name "shopsystem-messaging"
    Then the command exits zero
    And the FakeDockerDriver records that "docker network create shopsystem-product" was NOT called
    And a Docker container named "bc-shopsystem-messaging" is running

  @scenario_hash:3cb6e3c0c8d8235e @bc:shopsystem-bc-launcher
  Scenario: explicit --network flag overrides the network name derived from bc-manifest.yaml
    Given a bc-manifest.yaml exists with product field "shopsystem product"
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container launch with BC name "shopsystem-messaging" and flag "--network custom-net"
    Then the command exits zero
    And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes the flag "--network custom-net"
    And the FakeDockerDriver records that the docker run command does NOT include "--network shopsystem-product"

  @scenario_hash:dde337597985c122 @bc:shopsystem-bc-launcher
  Scenario: explicit --network flag suppresses automatic network creation
    Given a bc-manifest.yaml exists with product field "shopsystem product"
    And no Docker network named "custom-net" exists
    And no Docker container named "bc-shopsystem-messaging" is running
    When I run bc-container launch with BC name "shopsystem-messaging" and flag "--network custom-net"
    Then the command exits zero
    And the FakeDockerDriver records that "docker network create" was NOT called
    And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes the flag "--network custom-net"

  @scenario_hash:efc2032059b5c8e5 @bc:shopsystem-bc-launcher
  Scenario: two BC containers launched under the same product are both attached to the same derived network
    Given no Docker container named "bc-shopsystem-messaging" is running
    And no Docker container named "bc-shopsystem-scenarios" is running
    When I run bc-container launch with BC name "shopsystem-messaging"
    And I run bc-container launch with BC name "shopsystem-scenarios"
    Then the command exits zero for both launches
    And the FakeDockerDriver records that the docker run command for "bc-shopsystem-messaging" includes the flag "--network shopsystem-product"
    And the FakeDockerDriver records that the docker run command for "bc-shopsystem-scenarios" includes the flag "--network shopsystem-product"

  @scenario_hash:b48bb2794a952a99 @bc:shopsystem-bc-launcher
  Scenario: network creation is attempted only once when a second BC is launched under the same product network that already exists
    Given no Docker container named "bc-shopsystem-messaging" is running
    And no Docker container named "bc-shopsystem-scenarios" is running
    And no Docker network named "shopsystem-product" exists
    When I run bc-container launch with BC name "shopsystem-messaging"
    And I run bc-container launch with BC name "shopsystem-scenarios"
    Then the FakeDockerDriver records that "docker network create shopsystem-product" was called exactly once across both launches

  @scenario_hash:5a1fc25a7823b268 @bc:shopsystem-bc-launcher
  Scenario: bc-container launch resolves the shop docker network from the shop's known on-disk configuration without a per-launch --network flag when bc-manifest.yaml carries no shop-level network field
    Given the shopsystem-bc-launcher BC is installed
    And the shop's on-disk configuration declares the shop docker network name "shopsystem" as the single derived network coordinate (the ADR-043 D2 ops-coordinates derivation root; in the interim the compose.yaml network "shopsystem" and the product slug)
    And the bc-manifest.yaml registers the BC "shopsystem-templates" but carries no shop-level network or product launch field
    And no explicit "--network" flag is provided
    And no Docker container named "bc-shopsystem-templates" is running
    When I run bc-container launch with BC name "shopsystem-templates"
    Then the command exits zero
    And the FakeDockerDriver records that the docker run command for "bc-shopsystem-templates" includes the flag "--network shopsystem"
    And the command does not emit the error "no network: bc-manifest.yaml not found and --network not provided"

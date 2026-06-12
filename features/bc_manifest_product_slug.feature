Feature: bc-container manifest validate derives the accepted BC-name prefix from the product slug

  The accepted canonical BC-name shape is '<product-slug>-<identifier>'. The
  product slug is resolved flag -> PRODUCT_SLUG env -> default 'shopsystem',
  so a non-shopsystem product can declare BC names under its own slug while
  the default keeps the shopsystem-* set unchanged.

  @scenario_hash:552ffe3a8a3c3f91 @bc:shopsystem-bc-launcher
  Scenario: the validate command accepts a non-shopsystem product BC name under a configured product slug
    Given a manifest file contains a single BC entry named "acme-widget" with a valid GitHub remote URL and role label "bc"
    And a FakeGitHubDriver is configured to report the declared remote URL as reachable
    When I run "bc-container manifest validate" against that manifest with product slug "acme"
    Then the command exits zero
    And the output reports the BC entry "acme-widget" as valid

  @scenario_hash:48884778a8266386 @bc:shopsystem-bc-launcher
  Scenario: the validate command still accepts shopsystem BC names under the default product slug
    Given a manifest file contains a single BC entry named "shopsystem-messaging" with a valid GitHub remote URL and role label "bc"
    And a FakeGitHubDriver is configured to report the declared remote URL as reachable
    When I run "bc-container manifest validate" against that manifest with the default product slug
    Then the command exits zero
    And the output reports the BC entry "shopsystem-messaging" as valid

  @scenario_hash:cd7571286d97d76d @bc:shopsystem-bc-launcher
  Scenario: the validate command under the default product slug accepts a non-shopsystem BC name (regression guard)
    Given a manifest file contains a single BC entry named "acme-widget" with a valid GitHub remote URL and role label "bc"
    And a FakeGitHubDriver is configured to report the declared remote URL as reachable
    When I run "bc-container manifest validate" against that manifest with the default product slug
    Then the command exits zero
    And the output reports the BC entry "acme-widget" as valid

  @scenario_hash:b225f050d50896f7 @bc:shopsystem-bc-launcher
  Scenario: the validate command rejects a BC name that does not match the configured product slug
    Given a manifest file contains a single BC entry named "shopsystem-messaging" with a valid GitHub remote URL and role label "bc"
    And a FakeGitHubDriver is configured to report the declared remote URL as reachable
    When I run "bc-container manifest validate" against that manifest with product slug "acme"
    Then the command exits non-zero
    And the output reports the BC entry "shopsystem-messaging" as not matching the configured product slug

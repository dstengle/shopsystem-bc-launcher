Feature: bc-base Dockerfile installs shop-templates from a VCS version pin (lead-dlrx)

  The bc-base image must carry the shop-templates CLI so that launched BC
  containers can pour the shop-templates skill-group into their workspace.
  shop-templates is installed from a "github.com/dstengle/shop-templates @
  vMAJOR.MINOR.PATCH" VCS version pin — the SAME pin shape the Dockerfile
  already uses for the other framework utility CLIs (shop-msg, beads) — and
  NOT from an editable clone of a sibling working tree.  This is pinned
  structurally by inspecting the committed Dockerfile, mirroring the
  scenario-36 declarative-artifact precedent.

  @scenario_hash:30165afc5692ac3d @bc:shopsystem-bc-launcher
  Scenario: the bc-base Dockerfile installs shop-templates from a VCS version pin alongside the other framework utility CLIs
    Given the shopsystem-bc-launcher BC repository
    When the bc-base Dockerfile in that repository is inspected
    Then the Dockerfile installs "shop-templates" from a "github.com/dstengle/shop-templates @ vMAJOR.MINOR.PATCH" version pin rather than from an editable clone
    And that shop-templates install sits alongside the other framework utility CLIs the Dockerfile installs in the same VCS-pin shape

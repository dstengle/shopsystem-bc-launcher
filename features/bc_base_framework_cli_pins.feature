Feature: bc-base Dockerfile pins all five framework-CLI installs to their correct owner/repo (shopsystem-bc-launcher-tuk)

  This is a BC-INTERNAL test-rigor hardening (bead shopsystem-bc-launcher-tuk,
  surfaced during the lead-po0j review). It is NOT a lead-assigned scenario:
  the @bc_internal tag below is a BC-owned marker, NOT a lead @scenario_hash.

  Scenario 42 (ccb145d71c7100a2) pins only the shop-templates install, and
  scenario 36 (d9909f38abea83b5) requires only ">=1 dstengle VCS pin + reject
  editable clones". Neither structurally binds the OTHER four framework CLIs
  to their correct owner/repo. That hole let two consecutive wrong-repo 404
  defects ship green (dstengle/shop-msg and dstengle/beads; corrected in
  lead-b6gd / lead-po0j). This scenario is ADDITIVE: it asserts ALL FIVE
  framework-CLI installs are present, each in the
  "<pkg> @ git+https://github.com/<owner>/<repo>.git@vMAJOR.MINOR.PATCH"
  VCS-pin shape with its CORRECT owner/repo, and rejects editable clones for
  all five — so a wrong-owner/wrong-repo regression (the 404 class) on ANY of
  the five FAILS the test. Note the deliberate owner split: beads installs
  from the legitimate third-party gascity/beads source, NOT dstengle.

  Version is asserted by SHAPE (vMAJOR.MINOR.PATCH), not exact value, so
  legitimate version bumps do not break the test while the 404 class still trips.

  @bc_internal @bc:shopsystem-bc-launcher
  Scenario: the bc-base Dockerfile installs all five framework CLIs each pinned to its correct owner/repo
    Given the shopsystem-bc-launcher BC repository
    When the bc-base Dockerfile in that repository is inspected
    Then the Dockerfile installs all five framework CLIs each from a VCS version pin bound to its correct owner and repo
    And none of the five framework CLIs is installed from an editable clone

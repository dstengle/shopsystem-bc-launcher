"""
Unit test for lead-za30: the launched BC container's persistent RUNTIME env
must carry a non-empty PLACEHOLDER GH_TOKEN so a bare `gh` inside the container
authenticates through the agent-vault broker instead of pre-flight-refusing
("gh auth login" / "populate GH_TOKEN") before its request ever reaches the
proxy.

BUG: the runtime-env builder in controller/_launch.py injected HTTPS_PROXY and
AGENT_VAULT_ADDR/TOKEN/VAULT into the `docker run` env — the wire path that
lets the broker substitute the real GitHub credential — but NOT GH_TOKEN. gh
insists on a non-empty token in its OWN env before it will emit a request, so a
bare `gh api user` exited non-zero even though the proxy WOULD authenticate it.
git already rides agent-vault transparently; gh did not, purely for this reason.

FIX (additive, extends GAP-A / lead-3mez exec-scope precedent to runtime scope):
the same runtime-env builder that carries HTTPS_PROXY / AGENT_VAULT_* also
carries GH_TOKEN = the existing dummy constant
"gh-dummy-agent-vault-rides-the-wire" (FABRO_SERVER_INSTALL_GH_TOKEN). The
placeholder remains a placeholder — it grants no access; the broker substitutes
the real credential on the wire. This makes gh symmetric to git.

The placeholder is a SENTINEL, never a real token: the no-real-credential
invariants (97734ca69a510e37, b8f2e121a5fd77ba, ff1ee370a4462e7d,
f23dfbe84c899968, f838de07a80749f9) continue to hold — the value asserted here
is exactly the known dummy sentinel, not a GitHub token value.
"""
from __future__ import annotations

import pytest
import yaml

from bc_launcher.agent_vault import (
    AGENT_VAULT_ADDR_ENV,
    AGENT_VAULT_PROXY_ENV,
    AGENT_VAULT_TOKEN_ENV,
    AGENT_VAULT_VAULT_ENV,
)
from bc_launcher.fabro import FABRO_SERVER_INSTALL_GH_TOKEN
from tests.fake_driver import FakeDockerDriver

from bc_launcher.controller import BcContainerController


def _launch_with_av_creds(controller, fake_driver, tmp_path, bc_name):
    """Drive a plain brokered launch with the operator-supplied addr/token/vault
    triple (mirrors the launch_starts_agent_with_av_creds step idiom), so the
    controller derives the runtime HTTPS_PROXY at the :14322 MITM listener and
    injects the AGENT_VAULT_* triple into the container env.
    """
    repo_url = f"https://github.com/shopsystem/{bc_name}.git"
    manifest_path = tmp_path / "bc-manifest.yaml"
    manifest_path.write_text(
        yaml.dump(
            {
                "product": "shopsystem product",
                "bcs": [{"name": bc_name, "remote": repo_url, "role": "bc"}],
            }
        )
    )
    result = controller.launch(
        bc_name=bc_name,
        repo_url=repo_url,
        shopmsg_dsn=None,
        startup_prompt="please begin your session",
        network=None,
        manifest_path=manifest_path,
        credential_home=None,
        agent_vault_addr="https://agent-vault:14321",
        agent_vault_token="av_agt_operator_supplied_xyz",
        agent_vault_vault="shopsystem",
    )
    return result, f"bc-{bc_name}"


@pytest.fixture
def fake_driver():
    return FakeDockerDriver()


@pytest.fixture
def controller(fake_driver):
    return BcContainerController(fake_driver, monotonic=fake_driver.monotonic)


def test_runtime_env_carries_gh_token_placeholder(controller, fake_driver, tmp_path):
    """The runtime-env builder injects a non-empty GH_TOKEN placeholder into the
    launched container's docker run env, alongside HTTPS_PROXY / AGENT_VAULT_*.
    """
    _result, container_name = _launch_with_av_creds(
        controller, fake_driver, tmp_path, "shopsystem-messaging"
    )
    env = fake_driver.container_env(container_name)

    # GH_TOKEN is present and is the known dummy placeholder constant.
    assert env.get("GH_TOKEN") == FABRO_SERVER_INSTALL_GH_TOKEN, (
        f"Expected the runtime GH_TOKEN to be the placeholder constant "
        f"{FABRO_SERVER_INSTALL_GH_TOKEN!r}, got {env.get('GH_TOKEN')!r} "
        f"(full container env: {env!r})"
    )
    # Non-empty: gh refuses a bare invocation when GH_TOKEN is empty/unset.
    assert env["GH_TOKEN"], "runtime GH_TOKEN must be non-empty so gh does not pre-flight-refuse"

    # ...and it is carried ALONGSIDE the already-pinned proxy/vault runtime env,
    # i.e. it is a member of the same runtime-env set the broker relies on.
    assert env.get(AGENT_VAULT_PROXY_ENV), "runtime HTTPS_PROXY must still be present"
    assert env.get(AGENT_VAULT_ADDR_ENV) == "https://agent-vault:14321"
    assert env.get(AGENT_VAULT_TOKEN_ENV) == "av_agt_operator_supplied_xyz"
    assert env.get(AGENT_VAULT_VAULT_ENV) == "shopsystem"


def test_runtime_gh_token_is_the_sentinel_not_a_real_token(
    controller, fake_driver, tmp_path
):
    """The runtime GH_TOKEN must be the placeholder sentinel — never a real
    GitHub token value (preserves the no-real-credential invariants). The dummy
    constant self-documents as a placeholder that grants no access.
    """
    _result, container_name = _launch_with_av_creds(
        controller, fake_driver, tmp_path, "shopsystem-messaging"
    )
    env = fake_driver.container_env(container_name)

    gh_token = env.get("GH_TOKEN")
    assert gh_token == "gh-dummy-agent-vault-rides-the-wire"
    # A real GitHub PAT/OAuth token would carry a ghp_/gho_/github_pat_ prefix;
    # the sentinel does not — the broker substitutes the real credential on the
    # wire, so no real token value ever enters the container env.
    assert not gh_token.startswith(("ghp_", "gho_", "ghs_", "github_pat_"))

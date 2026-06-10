"""
Unit tests for bclaunch-3le: the `bc-container launch` CLI operator knobs that
deliver agent-vault credentials + CA to controller.launch().

GAP (bclaunch-3le): launch() accepted agent_vault_broker but the CLI dispatch
never passed it; the launch subparser had no agent-vault flags and no
--env-file.  This wires:
  * --agent-vault-broker  -> threaded through to launch(agent_vault_broker=)
  * --env-file            -> parsed KEY=VALUE; AGENT_VAULT_ADDR/TOKEN/VAULT
                             supplied to launch()
  * --agent-vault-ca      -> launch(agent_vault_ca=)

Precedence (documented): an explicit flag wins over the env-file value for the
broker; for ADDR/TOKEN/VAULT the env-file is the supply channel (there are no
dedicated per-value flags).

These exercise the CLI layer directly via a recording controller stub, the
same idiom as test_startup_prompt_default.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bc_launcher import cli as cli_module
from bc_launcher.cli import build_parser, main as cli_main


class _RecordingController:
    def __init__(self):
        self.calls: list[dict] = []

    def launch(self, **kwargs):
        self.calls.append(kwargs)
        from bc_launcher.controller import CommandResult
        return CommandResult(exit_code=0, stdout="", stderr="")


@pytest.fixture
def recording_controller(monkeypatch):
    recorder = _RecordingController()
    monkeypatch.setattr(cli_module, "BcContainerController", lambda _d: recorder)
    monkeypatch.setattr(cli_module, "RealDockerDriver", lambda: object())
    return recorder


# --- argparse surface ------------------------------------------------------

def test_launch_parser_accepts_agent_vault_broker():
    args = build_parser().parse_args(
        ["launch", "shopsystem-messaging",
         "--agent-vault-broker", "https://agent-vault:14321"]
    )
    assert args.agent_vault_broker == "https://agent-vault:14321"


def test_launch_parser_accepts_env_file():
    args = build_parser().parse_args(
        ["launch", "shopsystem-messaging", "--env-file", "/tmp/x.env"]
    )
    assert args.env_file == "/tmp/x.env"


def test_launch_parser_accepts_agent_vault_ca():
    args = build_parser().parse_args(
        ["launch", "shopsystem-messaging", "--agent-vault-ca", "/tmp/ca.pem"]
    )
    assert args.agent_vault_ca == "/tmp/ca.pem"


# --- dispatch threads the broker through -----------------------------------

def test_agent_vault_broker_flag_is_threaded_to_launch(recording_controller):
    exit_code = cli_main(
        ["launch", "shopsystem-messaging",
         "--agent-vault-broker", "https://agent-vault:14321"]
    )
    assert exit_code == 0
    assert recording_controller.calls[0]["agent_vault_broker"] == (
        "https://agent-vault:14321"
    )


# --- --env-file supplies ADDR/TOKEN/VAULT ----------------------------------

def test_env_file_supplies_addr_token_vault(recording_controller, tmp_path):
    env_file = tmp_path / "av.env"
    env_file.write_text(
        "# operator-supplied agent-vault credentials\n"
        "AGENT_VAULT_ADDR=https://agent-vault:14321\n"
        "AGENT_VAULT_TOKEN=av_agt_from_env_file\n"
        "AGENT_VAULT_VAULT=shopsystem\n"
        "\n"
        "IGNORED_OTHER=value\n"
    )
    exit_code = cli_main(
        ["launch", "shopsystem-messaging", "--env-file", str(env_file)]
    )
    assert exit_code == 0
    call = recording_controller.calls[0]
    assert call["agent_vault_addr"] == "https://agent-vault:14321"
    assert call["agent_vault_token"] == "av_agt_from_env_file"
    assert call["agent_vault_vault"] == "shopsystem"


def test_env_file_tolerates_quotes_and_export_prefix(recording_controller, tmp_path):
    env_file = tmp_path / "av.env"
    env_file.write_text(
        'export AGENT_VAULT_ADDR="https://agent-vault:14321"\n'
        "AGENT_VAULT_TOKEN='av_agt_quoted'\n"
        "AGENT_VAULT_VAULT = shopsystem \n"
    )
    exit_code = cli_main(
        ["launch", "shopsystem-messaging", "--env-file", str(env_file)]
    )
    assert exit_code == 0
    call = recording_controller.calls[0]
    assert call["agent_vault_addr"] == "https://agent-vault:14321"
    assert call["agent_vault_token"] == "av_agt_quoted"
    assert call["agent_vault_vault"] == "shopsystem"


# --- --agent-vault-ca is threaded as a Path --------------------------------

def test_agent_vault_ca_flag_is_threaded_to_launch(recording_controller):
    exit_code = cli_main(
        ["launch", "shopsystem-messaging", "--agent-vault-ca", "/tmp/ca.pem"]
    )
    assert exit_code == 0
    assert recording_controller.calls[0]["agent_vault_ca"] == Path("/tmp/ca.pem")


# --- precedence: explicit flag wins over env-file for the broker -----------

def test_no_av_flags_passes_none(recording_controller):
    """Omitting all agent-vault knobs threads None (preserving prior default)."""
    exit_code = cli_main(["launch", "shopsystem-messaging"])
    assert exit_code == 0
    call = recording_controller.calls[0]
    assert call["agent_vault_broker"] is None
    assert call["agent_vault_addr"] is None
    assert call["agent_vault_token"] is None
    assert call["agent_vault_vault"] is None
    assert call["agent_vault_ca"] is None

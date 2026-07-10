"""Fabro loop-def bundle + orchestrator launch-path wiring (package).

Split from the former single-module bc_launcher/fabro.py. Submodules:
  constants   - all FABRO_*/LAUNCH_PATH_*/ANTHROPIC_* constants
  def_bundle  - fabro-def asset load + placement
  provider    - anthropic-oauth-shim start
  engage      - server/run/engage argv + engage script
  settings    - settings.toml + workflow.toml (re)write scripts

Every public name is re-exported here so the historical
``from bc_launcher.fabro import <name>`` import paths keep resolving.
"""
from __future__ import annotations

from bc_launcher.fabro.constants import (  # noqa: F401
    FABRO_DEF_CONTAINER_DIR,
    FABRO_DEF_ASSET_SUBDIR,
    FABRO_DEF_FILES,
    LAUNCH_PATH_TMUX,
    LAUNCH_PATH_FABRO,
    ANTHROPIC_OAUTH_SHIM_BIN,
    FABRO_SHIM_HOST,
    FABRO_SHIM_PORT,
    FABRO_SETTINGS_CONTAINER_PATH,
    FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    FABRO_WORKFLOW_TOML_DEFAULT_BC_NAME,
    FABRO_WORKFLOW_TOML_DEFAULT_WORK_ID,
    FABRO_ANTHROPIC_BASE_URL,
    FABRO_ANTHROPIC_ADAPTER,
    FABRO_BIN,
    FABRO_WORKFLOW_FILE,
    FABRO_DISPATCHER_FILE,
    FABRO_SERVER_INSTALL_GITHUB_USERNAME,
    FABRO_SERVER_INSTALL_GH_TOKEN,
    FABRO_SERVER_INSTALL_ARGV,
    FABRO_SERVER_DUMMY_ANTHROPIC_KEY,
    FABRO_SERVER_SETTINGS_CONTAINER_PATH,
)
from bc_launcher.fabro.def_bundle import (  # noqa: F401
    _fabro_def_asset_root,
    _load_fabro_def_files,
    _fabro_def_install_script,
)
from bc_launcher.fabro.provider import (  # noqa: F401
    _fabro_shim_start_argv,
    _fabro_shim_start_script,
)
from bc_launcher.fabro.engage import (  # noqa: F401
    _fabro_server_start_argv,
    _fabro_server_install_argv,
    _fabro_run_argv,
    _fabro_engage_script,
)
from bc_launcher.fabro.settings import (  # noqa: F401
    _fabro_settings_toml,
    _fabro_settings_install_script,
    _fabro_workflow_toml_rewrite,
    _fabro_workflow_toml_install_script,
)

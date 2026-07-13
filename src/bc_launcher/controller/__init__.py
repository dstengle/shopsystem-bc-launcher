"""bc_launcher.controller — facade package.

The BcContainerController class was decomposed (controller.py -> this package)
into mixins under core/_launch/_provisioning/_agent_session/_commands, and its
module-level helpers into sibling bc_launcher leaf modules. Every name that was
historically importable as ``from bc_launcher.controller import <name>`` is
re-exported here so those import paths keep resolving unchanged.
"""
from __future__ import annotations

from bc_launcher.controller.core import BcContainerController  # noqa: F401
from bc_launcher.controller._result import CommandResult  # noqa: F401

from bc_launcher.constants import (  # shared primitives (single source of truth)
    AGENT_CONTAINER_USER,
    AGENT_VAULT_CONTAINER_CA_PATH,
    CONTAINER_WORKSPACE,
    SSL_CERT_FILE_ENV,
)
from bc_launcher.constants import (  # noqa: F401,E402
    DOCKER_SOCKET_PATH,
    AGENT_TMUX_SESSION,
    MAX_ARG_STRLEN,
    BC_IMAGE,
    BC_IMAGE_ENV,
    SHOPMSG_DSN_ENV,
)
from bc_launcher.diagnostics import (  # noqa: F401,E402
    BCLAUNCHER_HOST_STATE_DIR_ENV,
    XDG_STATE_HOME_ENV,
    DEFAULT_HOST_STATE_DIR_LEAF,
    LAUNCH_DIAGNOSTIC_FILENAME,
    CAUSE_MARKER_MESSAGING_DB,
    CAUSE_MARKER_AGENT_VAULT,
    CAUSE_MARKER_READINESS,
    CAUSE_MARKER_AGENT_STARTUP,
    default_host_state_dir,
    launch_diagnostic_path,
    _resolve_host_path,
)
from bc_launcher.networking import (  # noqa: F401,E402
    SHOPMSG_SYSTEM_SLUG_ENV,
    DEFAULT_SYSTEM_SLUG,
    _resolve_shop_network,
    resolve_probe_broker_address,
)
from bc_launcher.tracker_provision import (  # noqa: F401,E402
    BEADS_REMOTE_ORG,
    TRACKER_PROVISION_GH_TOKEN,
    _beads_dolt_remote_url,
    _is_empty_remote_failure,
    _is_schema_skew_migration_refusal,
    _schema_skew_heal_script,
    _beads_dolt_repo_slug,
    _is_repo_not_found_failure,
    _create_absent_tracker_repo_script,
    _tracker_provision_exec_env,
    _empty_remote_seed_script,
    _resolve_origin_owner_writeback_script,
)
from bc_launcher.agent_vault import (  # noqa: F401,E402
    AGENT_VAULT_PLACEHOLDER_TOKEN,
    AGENT_VAULT_PROXY_ENV,
    DEFAULT_AGENT_VAULT_BROKER,
    AGENT_VAULT_BROKER_ENV,
    AGENT_VAULT_CONTROL_API_PORT,
    AGENT_VAULT_SERVICE_NAME,
    AGENT_VAULT_MITM_PROXY_PORT,
    GIT_SSL_CAINFO_ENV,
    CA_PEM_FIRST_LINE,
    AGENT_VAULT_ADDR_ENV,
    AGENT_VAULT_TOKEN_ENV,
    AGENT_VAULT_VAULT_ENV,
    AGENT_VAULT_CA_PEM_ENV,
    CONTAINER_BROKER_CA_PATH,
    CONTAINER_CLAUDE_CREDENTIALS_PATH,
    _clone_ca_materialize_script,
    _mitm_proxy_host,
    _build_clone_proxy_url,
    _build_runtime_proxy_url,
)
from bc_launcher.fabro import (  # noqa: E402,F401  (re-export for compat)
    _fabro_exec_env,
    FABRO_DEF_CONTAINER_DIR,
    FABRO_DEF_ASSET_SUBDIR,
    FABRO_DEF_FILES,
    _fabro_def_asset_root,
    _load_fabro_def_files,
    _fabro_def_install_script,
    _fabro_def_bundle_tar_b64,
    LAUNCH_PATH_TMUX,
    LAUNCH_PATH_FABRO,
    ANTHROPIC_OAUTH_SHIM_BIN,
    FABRO_SHIM_HOST,
    FABRO_SHIM_PORT,
    FABRO_SETTINGS_CONTAINER_PATH,
    FABRO_WORKFLOW_TOML_CONTAINER_PATH,
    FABRO_DISPATCHER_TOML_CONTAINER_PATH,
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
    FABRO_WATCH_STATE_DIR,
    FABRO_WATCH_SERVER_SOCKET,
    FABRO_WATCH_SERVER_STORAGE,
    FABRO_WATCH_INFLIGHT_DIR,
    FABRO_WATCH_COMPLETED_FILE,
    FABRO_WATCH_TELEMETRY_FILE,
    FABRO_WATCH_TELEMETRY_INTERVAL_SECS,
    _fabro_server_start_argv,
    _fabro_server_install_argv,
    _fabro_run_argv,
    _fabro_engage_script,
    _fabro_shim_start_argv,
    _fabro_shim_start_script,
    _fabro_settings_toml,
    _fabro_settings_install_script,
    _fabro_workflow_toml_rewrite,
    _fabro_workflow_toml_read_script,
    _fabro_workflow_toml_writeback_script,
)
from bc_launcher.readiness import (  # noqa: F401,E402
    CLAUDE_READY_MARKER,
    CLAUDE_INPUT_READY_MARKER,
    CLAUDE_READINESS_TIMEOUT_SECONDS,
    OPTION_SCREEN_MARKER,
    ESCAPE_AFFORDANCE_MARKER,
    ESCAPE_KEY_NAME,
    READINESS_PROMPT_ESCAPE_AFFORDANCE_MARKERS,
    WORKSPACE_TRUST_PROMPT_MARKERS,
    FULLSCREEN_RENDERER_PROMPT_MARKER,
    READINESS_DISMISS_POLL_SECONDS,
    _readiness_wait_blocking_prompt,
)
from bc_launcher.naming import (  # noqa: F401,E402
    _BEADS_ISSUE_ID_RE,
    _container_name,
    beads_prefix_for,
    committed_beads_prefix_from_registry,
    _slugify,
)
from bc_launcher.manifest import (  # noqa: F401,E402
    ManifestProductTypeError,
    _resolve_manifest_remote,
    _read_product_from_manifest,
)

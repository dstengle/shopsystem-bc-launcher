# Runbook: reading a bc-container launch-failure diagnostic

lead-63em (re-issue of lead-2qta). When `bc-container launch` fails to bring
up a **usable agent session**, it exits non-zero, leaves **no usable `agent`
tmux session**, and persists a diagnostic **file** so an operator can learn
**why** from the host — without attaching into any tmux session and without
relying on the (ephemeral) launch-command stderr or the `bc-container monitor`
tmux pane.

## Where the diagnostic file lives (documented location)

```
<BCLAUNCHER_HOST_STATE_DIR | /var/lib/bc-launcher>/<container-name>/launch-diagnostic.txt
```

- The state root is the `BCLAUNCHER_HOST_STATE_DIR` env var when set,
  otherwise the default `/var/lib/bc-launcher`.
- The per-BC subdirectory is the container name (`bc-<bc_name>`) — the same
  per-BC identity shape the launcher uses elsewhere, and the same
  host-visible per-BC surface the BC's mailbox/state is read from.
- Example: a failed launch of BC `shopsystem-messaging` writes
  `/var/lib/bc-launcher/bc-shopsystem-messaging/launch-diagnostic.txt`.

The source of truth for this path is
`bc_launcher.controller.launch_diagnostic_path()`.

## File contents

A short, human-readable, host-greppable record:

```
cause: <cause-marker>
reason: <human-readable explanation of why the session failed to come up>
```

The `cause:` line carries the literal **cause-marker token** so an operator
or tool can grep for the failure class and be pointed at the right repair:

| cause-marker    | meaning / repair                                                        |
| --------------- | ----------------------------------------------------------------------- |
| `messaging-db`  | messaging database at `SHOPMSG_DSN` unreachable — fix the DB / DSN       |
| `agent-vault`   | agent-vault broker unreachable — bring the broker up / fix its address   |
| `readiness`     | readiness barrier never reported both supporting servers ready          |
| `agent-startup` | claude or its tmux session never started inside the container           |

## Why a file (and not stderr / the monitor pane)

- **stderr** is ephemeral: once the launch process exits, an operator who
  arrives later has no stderr to read.
- The **bc-container monitor tmux pane** needs a live `agent` session — but
  on these failure paths no usable session ever came up.
- The persisted file is readable from the host at the documented path even
  when no container / tmux session ever came up, and survives the launch
  process exiting.

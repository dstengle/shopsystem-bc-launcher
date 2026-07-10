"""Claude/tmux readiness markers + interactive-prompt auto-dismissal.

Extracted verbatim from ``controller`` (Phase 1 of the controller.py
decomposition). Leaf module; re-exported by ``controller`` for import-path
compatibility. Do not import ``controller`` from here (would cycle).
"""
from __future__ import annotations



# Claude Code readiness markers used to sequence prompt injection inside
# the agent tmux session.  The default tmux session command is bash, so a
# naïve send-keys of the startup prompt lands in bash and fails as
# "-bash: <first-word>: command not found".  The launch sequence is:
#   1. send-keys 'claude --dangerously-skip-permissions' Enter
#                                         — start Claude Code with
#                                           in-container permission bypass
#                                           (the BC container is the
#                                           isolation boundary)
#   2. wait for CLAUDE_READY_MARKER       — workspace-trust banner appeared
#                                           (PRE-trust: this is the line
#                                           Claude Code prints BEFORE the
#                                           trust prompt clears, so it
#                                           confirms the agent has reached
#                                           interactive UI without
#                                           presupposing trust was accepted)
#   3. send-keys Enter                    — accept workspace-trust default
#                                           (empirically verified that
#                                           --dangerously-skip-permissions
#                                           does NOT bypass workspace trust,
#                                           so this step is still required)
#   4. wait for CLAUDE_INPUT_READY_MARKER — main input prompt is live
#                                           (POST-trust: "bypass permissions
#                                           on" appears only once the trust
#                                           prompt has cleared and the
#                                           main input UI is live — chosen
#                                           in preference to the bare "❯"
#                                           glyph because the PRE-trust
#                                           pane also contains "❯" as the
#                                           trust-prompt selector arrow,
#                                           which would otherwise cause
#                                           step 4 to succeed trivially)
#   5. send-keys <startup_prompt> Enter   — prompt lands inside Claude Code
# On any wait timeout, the launcher emits a stderr warning naming the
# step that did not confirm.
CLAUDE_READY_MARKER = "Accessing workspace:"

CLAUDE_INPUT_READY_MARKER = "bypass permissions on"

CLAUDE_READINESS_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Blocking interactive option-screen handling on engage (lead-q3uy)
# ---------------------------------------------------------------------------
#
# After the input-ready marker is observed (step 4) but BEFORE the startup
# prompt is submitted (step 5), the in-container agent runtime can present a
# blocking interactive option screen (e.g. a "select an option" / settings /
# theme chooser) that absorbs keystrokes — so a naive prompt submission would
# be eaten by the screen instead of reaching the input prompt.  The launcher
# captures the pane at this point and recognizes a blocking option screen by
# the OPTION_SCREEN_MARKER signature.
#
# Disposition (lead-q3uy):
#   * If the captured screen ALSO carries an ESCAPE_AFFORDANCE_MARKER (the
#     screen advertises an Escape/dismiss key), send a DISCRETE tmux send-keys
#     carrying ONLY the Escape key (NEVER Enter) to dismiss it, CAPTURE the
#     rendered screen content, log it as a host-discoverable WARNING (the same
#     launch-stderr surface every other engage warning uses), then proceed to
#     submit the startup prompt directly — no host-side `bc-container inject`.
#   * If the screen exposes NO escape affordance, do NOT send Enter and do NOT
#     auto-confirm a default (pressing Enter would blindly select whatever
#     option is highlighted); instead surface a WARNING NAMING the un-escapable
#     screen so a human can review it from the host, and do NOT submit the
#     prompt into a screen that would swallow it.
#
# Detection keys on rendered-pane substrings, mirroring the existing
# CLAUDE_*_MARKER readiness-marker idiom rather than inventing a new seam.  The
# ESCAPE key NAME is the tmux key-name token sent as the SOLE send-keys payload
# (a discrete pty write that the TUI processes as a single Escape keypress).
OPTION_SCREEN_MARKER = "Select an option"

ESCAPE_AFFORDANCE_MARKER = "esc to"

ESCAPE_KEY_NAME = "Escape"


# ---------------------------------------------------------------------------
# Readiness-wait interactive-prompt auto-dismissal (lead-cw7m / lead-c713)
# ---------------------------------------------------------------------------
#
# EXTENDS the lead-q3uy/gs03 Esc-not-Enter / warn / no-auto-confirm posture
# from the ENGAGE phase (AFTER input-ready) to the READINESS-WAIT phase
# (BEFORE input-ready).  The new bc-base Claude Code image (c50b3b) renders
# an EARLIER interactive prompt — "Try the new fullscreen renderer?
# (1. Yes / 2. Not now, Esc to cancel)" — BEFORE the "Accessing workspace:"
# trust banner.  The narrow step-4 readiness handler (wait for
# CLAUDE_INPUT_READY_MARKER) could not see past it: the input-ready marker
# never appeared, the wait timed out at 60s, the startup prompt was never
# injected, the watcher never armed, and the BC never came online.
#
# Disposition (lead-cw7m — launcher-runtime scan-and-solve; the PO chose
# this over an image-config pre-seed because it is robust to image-config
# drift):
#   * During the readiness wait (while waiting for the input-ready marker),
#     if the pane presents an interactive prompt that is NOT the
#     already-handled workspace-trust prompt and is NOT yet at input-ready,
#     dismiss it with a safe NON-COMMITTAL default by sending ONLY Esc
#     (decline — NEVER Enter / '1', so the renderer is NOT enabled), emit a
#     host-discoverable WARNING NAMING the auto-dismissed prompt, then
#     CONTINUE the readiness loop toward input-ready.
#   * The whole scan-dismiss loop stays BOUNDED by the existing 60s readiness
#     timeout.  On timeout WITHOUT input-ready: STOP attempting dismissals
#     (no infinite loop), warn that the main input did not become ready
#     within 60 seconds, and proceed WITHOUT injecting the startup prompt.
#
# Detection keys on rendered-pane substrings, mirroring the CLAUDE_*_MARKER /
# OPTION_SCREEN_MARKER idiom.  A readiness-wait prompt is recognized as a
# blocking interactive prompt that advertises an Esc/cancel affordance and is
# NOT the workspace-trust prompt and is NOT yet at input-ready.  The specific
# fullscreen-renderer onboarding prompt is recognized by its own signature.
READINESS_PROMPT_ESCAPE_AFFORDANCE_MARKERS = ("esc to", "esc to cancel")

WORKSPACE_TRUST_PROMPT_MARKERS = ("trust this folder", "Quick safety check")

FULLSCREEN_RENDERER_PROMPT_MARKER = "Try the new fullscreen renderer?"

# How long a single input-ready wait poll is given before the controller
# re-captures the pane to look for a blocking readiness-wait prompt.  The
# per-attempt budget keeps the loop responsive while the TOTAL elapsed time
# stays bounded by CLAUDE_READINESS_TIMEOUT_SECONDS.
READINESS_DISMISS_POLL_SECONDS = 5.0



def _readiness_wait_blocking_prompt(pane: str) -> str | None:
    """Classify a readiness-wait pane capture (lead-cw7m / lead-c713).

    Returns a short human-readable NAME of a blocking interactive prompt that
    is presenting during the readiness wait and must be auto-dismissed with
    Esc, or ``None`` when the pane carries no such prompt.

    A prompt qualifies when ALL hold:
      * the input-ready marker is NOT yet present (an input-ready pane is not a
        blocking prompt — it is success);
      * the pane is NOT the already-handled workspace-trust prompt (step 3 of
        the readiness sequence accepts that one with Enter);
      * the pane advertises an Esc/cancel affordance (so Esc is the screen's
        own non-committal decline default — we never blind-press Enter / '1').

    The specific fullscreen-renderer onboarding prompt (image c50b3b) is named
    explicitly; any other Esc-dismissable readiness-wait prompt is named
    generically from its first non-empty rendered line.
    """
    if not pane:
        return None
    if CLAUDE_INPUT_READY_MARKER in pane:
        # Input-ready reached — not a blocking prompt.
        return None
    if any(m in pane for m in WORKSPACE_TRUST_PROMPT_MARKERS):
        # The workspace-trust prompt is handled by step 3 (Enter); do NOT
        # treat it as an unexpected prompt to Esc-dismiss.
        return None
    pane_lower = pane.lower()
    if not any(m in pane_lower for m in READINESS_PROMPT_ESCAPE_AFFORDANCE_MARKERS):
        # No Esc/cancel affordance advertised — not an Esc-dismissable prompt.
        return None
    if FULLSCREEN_RENDERER_PROMPT_MARKER in pane:
        return FULLSCREEN_RENDERER_PROMPT_MARKER
    # Generic readiness-wait prompt: name it by its first non-empty line.
    for line in pane.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "an unexpected interactive prompt"

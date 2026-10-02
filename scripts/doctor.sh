#!/usr/bin/env bash
# doctor.sh — check that the digest-paper add-on can run here. Changes nothing,
# calls no API and never prints a key.
#
#   scripts/doctor.sh [--codex-home DIR] [--claude-skills-dir DIR]
set -uo pipefail

CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
CLAUDE_SKILLS="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --codex-home) CODEX_HOME_DIR="$2"; shift 2 ;;
    --claude-skills-dir) CLAUDE_SKILLS="$2"; shift 2 ;;
    *) echo "doctor: unknown argument: $1" >&2; exit 2 ;;
  esac
done
AGENTSTACK_HOME="${AGENTSTACK_HOME:-$HOME/.agentstack}"
ADDON="$AGENTSTACK_HOME/addons/digest-paper"
SKILL_LINK_TARGET="$ADDON/current/skills/digest-paper"
problems=0
ok() { echo "ok: $*"; }
warn() { echo "warn: $*"; problems=$((problems + 1)); }
note() { echo "note: $*"; }

if [[ -f "$SKILL_LINK_TARGET/SKILL.md" ]]; then
  ok "add-on installed: $(readlink "$ADDON/current")"
else
  warn "add-on not installed at $ADDON (run scripts/install.sh)"
fi

ZOTERO=false
[[ -f "$ADDON/install-state.json" ]] && grep -q 'digest-paper-zotero' "$ADDON/install-state.json" && ZOTERO=true
skills=(digest-paper)
[[ "$ZOTERO" == true ]] && skills+=(digest-paper-zotero)
for skill in "${skills[@]}"; do
  want="$ADDON/current/skills/$skill"
  for pair in "Claude:$CLAUDE_SKILLS/$skill" "Codex:$CODEX_HOME_DIR/skills/$skill"; do
    label="${pair%%:*}" target="${pair#*:}"
    if [[ -L "$target" && "$(readlink "$target")" == "$want" ]]; then
      ok "$label finds $skill: $target"
    elif [[ -e "$target" || -L "$target" ]]; then
      warn "$label has another $skill at $target; this add-on is used by its path $want/SKILL.md"
    else
      warn "$label does not see $skill ($target missing)"
    fi
  done
done

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
else
  warn "python3 3.9 or newer is required"
fi
python3 -c 'import PIL' 2>/dev/null && ok "Pillow available (contact sheets)" \
  || note "Pillow not installed; contact sheets are skipped and figures are opened one by one (recommended: sudo apt install python3-pil on WSL/Ubuntu, python3 -m pip install pillow on macOS)"

[[ -f "$AGENTSTACK_HOME/skills/delegate/SKILL.md" ]] && ok "ORRERY delegate skill found" \
  || warn "ORRERY core not found at $AGENTSTACK_HOME (the writer/reviewer team needs it)"
# Which agents can take a role. One kind is enough (two agents of it write and
# review: same-vendor); both make the usual cross-vendor team.
have_claude=false have_codex=false
command -v claude >/dev/null 2>&1 && have_claude=true
codex_bin="${AGENTSTACK_CODEX_BIN:-}"
if [[ -z "$codex_bin" && -f "$AGENTSTACK_HOME/env.sh" ]]; then
  codex_bin="$(sed -n "s/^export AGENTSTACK_CODEX_BIN=//p" "$AGENTSTACK_HOME/env.sh" | tail -n 1 | tr -d "'\"")"
fi
[[ -n "$codex_bin" ]] || codex_bin="$(command -v codex 2>/dev/null || true)"
codex_real="$( [[ -n "$codex_bin" ]] && python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$codex_bin" 2>/dev/null || true)"
if [[ "$codex_bin" == /mnt/* || "$codex_real" == /mnt/* ]]; then
  # A Windows codex seen from WSL is never used, even if it answers.
  note "codex at $codex_bin is the Windows one; ORRERY cannot use it from WSL. Install Codex inside WSL"
elif [[ -n "$codex_bin" ]]; then
  # codex --version writes into CODEX_HOME; give it a throwaway one.
  probe_home="$(mktemp -d)"
  if CODEX_HOME="$probe_home" "$codex_bin" --version >/dev/null 2>&1; then
    have_codex=true
  else
    note "codex at $codex_bin does not run ($codex_bin --version failed)"
  fi
  rm -rf "$probe_home"
fi
if [[ "$have_claude" == true && "$have_codex" == true ]]; then
  ok "claude and codex: a Claude writer and a Codex reviewer (cross-vendor)"
elif [[ "$have_claude" == true ]]; then
  ok "claude only: two Claude agents write and review (same-vendor; not another company's model)"
elif [[ "$have_codex" == true ]]; then
  ok "codex only: two Codex agents write and review (same-vendor; not another company's model)"
else
  warn "neither claude nor a working codex found (one of them is needed)"
fi
note "PDF input is not supported by this version; convert with the Obsidian pdf-mistral plugin first"

if [[ "$ZOTERO" == true ]]; then
  # Read-only: ping, Local API root and Better BibTeX api.ready.
  while IFS= read -r line; do
    case "$line" in
      ok:*) echo "$line" ;;
      warn:*) echo "$line"; problems=$((problems + 1)) ;;
      *) echo "$line" ;;
    esac
  done < <(python3 "$ADDON/current/skills/digest-paper-zotero/scripts/zotero_lookup.py" --doctor 2>&1)
fi

if [[ $problems -eq 0 ]]; then echo "digest-paper: ready"; else echo "digest-paper: $problems problem(s)"; fi
exit $(( problems > 0 ))

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
for cli in claude codex; do
  command -v "$cli" >/dev/null 2>&1 && ok "$cli on PATH" || warn "$cli not on PATH (needed for the $([[ $cli == claude ]] && echo writer || echo reviewer))"
done
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

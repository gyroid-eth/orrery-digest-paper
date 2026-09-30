#!/usr/bin/env bash
# uninstall.sh — remove the digest-paper add-on installed by install.sh.
#
#   scripts/uninstall.sh [--dry-run]
#
# Removes only the links this add-on created (and only while they still point
# at it) and $AGENTSTACK_HOME/addons/digest-paper except its runs/ folder.
# Notes, run folders (the drafts and reviews behind published notes), other
# skills of the same name and ORRERY itself are left alone.
set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true
AGENTSTACK_HOME="${AGENTSTACK_HOME:-$HOME/.agentstack}"
ADDON="$AGENTSTACK_HOME/addons/digest-paper"
STATE="$ADDON/install-state.json"
SKILLS_ROOT="$ADDON/current/skills"

if [[ ! -f "$STATE" ]]; then
  echo "uninstall: not installed ($STATE missing); nothing to do"
  exit 0
fi
while IFS= read -r target; do
  [[ -n "$target" ]] || continue
  if [[ -L "$target" && "$(readlink "$target")" == "$SKILLS_ROOT/$(basename "$target")" ]]; then
    echo "remove link: $target"
    [[ "$DRY_RUN" == true ]] || rm -f "$target"
  else
    echo "keep: $target (no longer this add-on's link)"
  fi
done < <(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["links"]))' "$STATE")
echo "remove: $ADDON (except runs/)"
[[ -d "$ADDON/runs" ]] && echo "keep: $ADDON/runs (run folders; delete it yourself if you no longer need them)"
[[ "$DRY_RUN" == true ]] && { echo "dry run: nothing removed"; exit 0; }
shopt -s dotglob nullglob
for entry in "$ADDON"/*; do
  [[ "$entry" == "$ADDON/runs" ]] && continue
  rm -rf "$entry"
done
rmdir "$ADDON" 2>/dev/null || true
echo "uninstalled"

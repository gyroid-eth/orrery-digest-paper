#!/usr/bin/env bash
# install.sh — install the digest-paper add-on for ORRERY from this checkout.
#
#   scripts/install.sh [--dry-run] [--with-zotero] [--codex-home DIR] [--claude-skills-dir DIR]
#
# --with-zotero also links the digest-paper-zotero skill (for Zotero users).
# Without it the install is exactly the MD-only digest-paper.
#
# Copies skills/digest-paper into
#   $AGENTSTACK_HOME/addons/digest-paper/releases/<version>-<commit>/
# points $AGENTSTACK_HOME/addons/digest-paper/current at it, and links the skill
# into Claude (~/.claude/skills/digest-paper) and Codex
# ($CODEX_HOME/skills/digest-paper) so both find it.
#
# It never replaces a skill it did not install: if either location already has
# a digest-paper that is not this add-on's link, that location is left alone
# and reported, and the skill can still be used by its absolute path. It does
# not touch ORRERY's own files, the user's notes, or any API key. Nothing is
# downloaded.
set -euo pipefail

die() { printf 'install: %s\n' "$*" >&2; exit 1; }

DRY_RUN=false
WITH_ZOTERO=false
CODEX_HOME_ARG=""
CLAUDE_SKILLS="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --with-zotero) WITH_ZOTERO=true; shift ;;
    --codex-home) [[ $# -ge 2 ]] || die "--codex-home needs a folder"; CODEX_HOME_ARG="$2"; shift 2 ;;
    --claude-skills-dir) [[ $# -ge 2 ]] || die "--claude-skills-dir needs a folder"; CLAUDE_SKILLS="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTSTACK_HOME="${AGENTSTACK_HOME:-$HOME/.agentstack}"
ADDON="$AGENTSTACK_HOME/addons/digest-paper"
CODEX_HOME_DIR="${CODEX_HOME_ARG:-${CODEX_HOME:-$HOME/.codex}}"
# A child agent's CODEX_HOME is a throwaway per-child home; installing there
# would vanish with the child. Require the persistent home explicitly.
if [[ -z "$CODEX_HOME_ARG" && "$CODEX_HOME_DIR" == */child-agents/* ]]; then
  die "CODEX_HOME points at a child agent's home ($CODEX_HOME_DIR); pass the persistent one with --codex-home"
fi

command -v python3 >/dev/null 2>&1 || die "python3 is required"
VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$SRC/addon.json")"
COMMIT="$(git -C "$SRC" rev-parse --short=12 HEAD 2>/dev/null || echo nogit)"
if [[ "$COMMIT" != nogit ]] && [[ -n "$(git -C "$SRC" status --porcelain -- skills addon.json 2>/dev/null)" ]]; then
  COMMIT="$COMMIT-dirty"
fi
RELEASE="$ADDON/releases/$VERSION-$COMMIT"
SKILL_LINK_TARGET="$ADDON/current/skills/digest-paper"
SKILLS=(digest-paper)
[[ "$WITH_ZOTERO" == true ]] && SKILLS+=(digest-paper-zotero)

echo "digest-paper add-on $VERSION ($COMMIT)"
echo "  from:    $SRC"
echo "  release: $RELEASE"
plan_link() {
  local label="$1" skill="$3" target="$2/$3" want="$ADDON/current/skills/$3"
  if [[ -L "$target" && "$(readlink "$target")" == "$want" ]]; then
    echo "  $label:  $target (already this add-on's link)"
  elif [[ -e "$target" || -L "$target" ]]; then
    echo "  $label:  $target exists and is not this add-on's; it will be KEPT, and the add-on is used by its path $want/SKILL.md"
  else
    echo "  $label:  $target -> $want"
  fi
}
for skill in "${SKILLS[@]}"; do
  plan_link Claude "$CLAUDE_SKILLS" "$skill"
  plan_link Codex "$CODEX_HOME_DIR/skills" "$skill"
done
[[ -f "$AGENTSTACK_HOME/skills/delegate/SKILL.md" ]] \
  || echo "  note: ORRERY core not found at $AGENTSTACK_HOME (the team flow needs its delegate skill)"
if [[ "$DRY_RUN" == true ]]; then
  echo "dry run: nothing written"
  exit 0
fi

# Stage the payload, check it, then move it into place.
mkdir -p "$ADDON/releases"
if [[ ! -d "$RELEASE" ]]; then
  staging="$ADDON/releases/.staging-$$"
  rm -rf "$staging"
  mkdir -p "$staging/skills"
  cp -R "$SRC/skills/digest-paper" "$SRC/skills/digest-paper-zotero" "$staging/skills/"
  cp "$SRC/addon.json" "$SRC/LICENSE" "$staging/"
  for script in "$staging"/skills/*/scripts/*.py; do
    python3 -m py_compile "$script" || { rm -rf "$staging"; die "payload check failed: $script"; }
  done
  find "$staging" -name __pycache__ -type d -prune -exec rm -rf {} +
  mv "$staging" "$RELEASE"
fi
# Swap the `current` link in one rename. `mv` onto a symlink to a directory
# moves the new link *into* the old release instead (it follows the link), so
# use rename(2) directly, which replaces the link itself.
rm -f "$ADDON/current.new"
ln -s "releases/$VERSION-$COMMIT" "$ADDON/current.new"
python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$ADDON/current.new" "$ADDON/current"

links=()
collisions=()
link_skill() {
  local dir="$1" target="$1/$2" want="$ADDON/current/skills/$2"
  if [[ -L "$target" && "$(readlink "$target")" == "$want" ]]; then
    links+=("$target")
  elif [[ -e "$target" || -L "$target" ]]; then
    collisions+=("$target")
  else
    mkdir -p "$dir"
    ln -s "$want" "$target"
    links+=("$target")
  fi
}
for skill in "${SKILLS[@]}"; do
  link_skill "$CLAUDE_SKILLS" "$skill"
  link_skill "$CODEX_HOME_DIR/skills" "$skill"
done
# A plain install after --with-zotero: take this add-on's own Zotero links
# away, so the recorded state matches what is linked and uninstall leaves no
# dangling link. Someone else's digest-paper-zotero is never touched.
if [[ "$WITH_ZOTERO" != true ]]; then
  for dir in "$CLAUDE_SKILLS" "$CODEX_HOME_DIR/skills"; do
    target="$dir/digest-paper-zotero"
    if [[ -L "$target" && "$(readlink "$target")" == "$ADDON/current/skills/digest-paper-zotero" ]]; then
      rm -f "$target"
      echo "unlinked: $target (install without --with-zotero)"
    fi
  done
fi

python3 - "$ADDON/install-state.json" "$VERSION" "$COMMIT" "$RELEASE" \
  "${#links[@]}" ${links[@]+"${links[@]}"} ${collisions[@]+"${collisions[@]}"} <<'PY'
import datetime, json, sys
path, version, commit, release, n = sys.argv[1:6]
rest = sys.argv[6:]
links, collisions = rest[:int(n)], rest[int(n):]
json.dump({"schema": 1, "version": version, "commit": commit, "release": release,
           "installed": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "links": links, "collisions": collisions},
          open(path, "w"), indent=2)
PY

for target in ${links[@]+"${links[@]}"}; do echo "linked: $target"; done
for target in ${collisions[@]+"${collisions[@]}"}; do
  echo "WARNING: $target already exists and is not this add-on's; left unchanged."
done
if [[ ${#collisions[@]} -gt 0 ]]; then
  echo "  Use this add-on by its path: $ADDON/current/skills/<skill>/SKILL.md"
fi
echo "installed. Check with: $SRC/scripts/doctor.sh"

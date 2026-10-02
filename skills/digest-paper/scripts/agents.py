#!/usr/bin/env python3
"""Which agents can take a digest-paper role on this machine.

    agents.py            prints JSON: {"claude": path|null, "codex": path|null,
                                       "team": "cross-vendor"|"claude-only"|"codex-only"|"none",
                                       "notes": [...]}

Claude counts when `claude` is on PATH. Codex counts when the CLI ORRERY would
launch actually runs: AGENTSTACK_CODEX_BIN, else the one saved in
$AGENTSTACK_HOME/env.sh, else `codex` on PATH, asked for its version with a
throwaway CODEX_HOME (the real one is never touched). A Windows `codex`
under /mnt/ (WSL) is never used, even if it answers.
Reads only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def saved_codex_bin() -> str:
    home = Path(os.environ.get("AGENTSTACK_HOME") or Path.home() / ".agentstack")
    try:
        text = (home / "env.sh").read_text(encoding="utf-8")
    except OSError:
        return ""
    found = re.findall(r"^export AGENTSTACK_CODEX_BIN=(.*)$", text, re.MULTILINE)
    return found[-1].strip().strip("'\"") if found else ""


def main() -> int:
    notes = []
    claude = shutil.which("claude")
    codex = None
    candidate = os.environ.get("AGENTSTACK_CODEX_BIN") or saved_codex_bin() or shutil.which("codex") or ""
    real = os.path.realpath(candidate) if candidate else ""
    if candidate and (candidate.startswith("/mnt/") or real.startswith("/mnt/")):
        # A Windows codex seen from WSL: even if it answers --version through
        # Windows interop, ORRERY cannot run it as a Linux child.
        notes.append(f"{candidate} is a Windows codex; ORRERY cannot use it from WSL. Install Codex inside WSL")
    elif candidate:
        with tempfile.TemporaryDirectory() as probe_home:
            try:
                ok = subprocess.run([candidate, "--version"], env={**os.environ, "CODEX_HOME": probe_home},
                                    capture_output=True, timeout=30).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
        if ok:
            codex = candidate
        else:
            notes.append(f"{candidate} --version failed; Codex is not usable")
    team = ("cross-vendor" if claude and codex else "claude-only" if claude
            else "codex-only" if codex else "none")
    print(json.dumps({"claude": claude, "codex": codex, "team": team, "notes": notes}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

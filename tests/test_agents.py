from __future__ import annotations

import json
import subprocess
import sys

from conftest import SCRIPTS


def agents(tmp_path, monkeypatch, tools):
    """Run agents.py with a PATH holding only fake CLIs; each exits with the
    given status."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, status in tools.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n[ -n \"$CODEX_HOME\" ] && touch \"$CODEX_HOME/probed\"\nexit {status}\n")
        path.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "AGENTSTACK_HOME": str(tmp_path / "as")}
    out = subprocess.run([sys.executable, str(SCRIPTS / "agents.py")], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_team_follows_what_runs(tmp_path, monkeypatch):
    assert agents(tmp_path, monkeypatch, {"claude": 0, "codex": 0})["team"] == "cross-vendor"
    only = agents(tmp_path / "a", monkeypatch, {"claude": 0, "codex": 1})
    assert only["team"] == "claude-only" and only["codex"] is None and "--version failed" in only["notes"][0]
    assert agents(tmp_path / "b", monkeypatch, {"codex": 0})["team"] == "codex-only"
    assert agents(tmp_path / "c", monkeypatch, {})["team"] == "none"


def test_the_codex_ORRERY_saved_is_the_one_asked(tmp_path, monkeypatch):
    saved = tmp_path / "saved-codex"
    saved.write_text("#!/bin/sh\nexit 0\n")
    saved.chmod(0o755)
    (tmp_path / "as").mkdir()
    (tmp_path / "as" / "env.sh").write_text(f"export AGENTSTACK_CODEX_BIN='{saved}'\n")
    # The codex on PATH is broken (as the Windows one is on WSL); ORRERY's works.
    result = agents(tmp_path, monkeypatch, {"codex": 1})
    assert result["codex"] == str(saved) and result["team"] == "codex-only"
    assert not (tmp_path / "probed").exists()  # the probe used a throwaway CODEX_HOME


def test_a_windows_codex_is_never_used_even_if_it_answers(tmp_path, monkeypatch):
    """CheeryNewton P2-4: on WSL a Windows codex under /mnt/ may answer
    --version through interop, but ORRERY cannot run it as a child."""
    sys.path.insert(0, str(SCRIPTS))
    import agents
    calls = []
    monkeypatch.setenv("AGENTSTACK_CODEX_BIN", "/mnt/c/Users/test/AppData/Roaming/npm/codex")
    monkeypatch.setattr(agents.shutil, "which", lambda name: None)
    monkeypatch.setattr(agents.subprocess, "run", lambda *a, **k: calls.append(a) or
                        subprocess.CompletedProcess(a, 0))
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        agents.main()
    result = json.loads(out.getvalue())
    assert result["codex"] is None and result["team"] == "none" and not calls
    assert "Windows codex" in result["notes"][0]

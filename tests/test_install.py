from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import ROOT


def sh(script, env, *args):
    return subprocess.run(["/bin/bash", str(ROOT / "scripts" / script), *args],
                          env=env, capture_output=True, text=True, check=False)


def env_for(tmp_path, **extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": os.environ["PATH"], "HOME": str(home),
           "AGENTSTACK_HOME": str(home / ".agentstack"), "CODEX_HOME": str(home / ".codex")}
    env.update(extra)
    return env, home


def test_dry_run_writes_nothing(tmp_path):
    env, home = env_for(tmp_path)
    result = sh("install.sh", env, "--dry-run")
    assert result.returncode == 0 and "dry run: nothing written" in result.stdout
    assert not (home / ".agentstack").exists() and not (home / ".claude").exists()


def test_install_links_both_and_uninstall_removes_only_its_own(tmp_path):
    env, home = env_for(tmp_path)
    result = sh("install.sh", env)
    assert result.returncode == 0, result.stderr
    claude = home / ".claude" / "skills" / "digest-paper"
    codex = home / ".codex" / "skills" / "digest-paper"
    for link in (claude, codex):
        assert link.is_symlink() and (link / "SKILL.md").is_file()
    state = json.loads((home / ".agentstack/addons/digest-paper/install-state.json").read_text())
    assert sorted(state["links"]) == sorted([str(claude), str(codex)]) and state["collisions"] == []
    doctor = sh("doctor.sh", env)
    assert "ok: Claude finds digest-paper" in doctor.stdout and "ok: Codex finds digest-paper" in doctor.stdout
    # A second install is idempotent.
    assert sh("install.sh", env).returncode == 0
    removed = sh("uninstall.sh", env)
    assert removed.returncode == 0, removed.stderr
    assert not claude.exists() and not codex.exists()
    assert not (home / ".agentstack" / "addons" / "digest-paper").exists()


def test_uninstall_keeps_the_run_folders(tmp_path):
    env, home = env_for(tmp_path)
    assert sh("install.sh", env).returncode == 0
    addon = home / ".agentstack" / "addons" / "digest-paper"
    review = addon / "runs" / "r1" / "review" / "review-1.md"
    review.parent.mkdir(parents=True)
    review.write_text("verdict: approved\n")
    removed = sh("uninstall.sh", env)
    assert removed.returncode == 0, removed.stderr
    assert "keep: " + str(addon / "runs") in removed.stdout
    assert review.read_text() == "verdict: approved\n"
    assert sorted(p.name for p in addon.iterdir()) == ["runs"]


def test_an_existing_skill_of_the_same_name_is_never_replaced(tmp_path):
    env, home = env_for(tmp_path)
    private = home / ".claude" / "skills" / "digest-paper"
    private.mkdir(parents=True)
    (private / "SKILL.md").write_text("my own private skill\n")
    dry = sh("install.sh", env, "--dry-run")
    assert "exists and is not this add-on's; it will be KEPT" in dry.stdout
    result = sh("install.sh", env)
    assert result.returncode == 0
    assert "already exists and is not this add-on's; left unchanged" in result.stdout
    assert "Use this add-on by its path:" in result.stdout
    assert (private / "SKILL.md").read_text() == "my own private skill\n" and not private.is_symlink()
    assert (home / ".codex" / "skills" / "digest-paper").is_symlink()
    doctor = sh("doctor.sh", env)
    assert "Claude has another digest-paper" in doctor.stdout
    sh("uninstall.sh", env)
    assert (private / "SKILL.md").read_text() == "my own private skill\n"


def test_a_child_agents_codex_home_is_refused(tmp_path):
    env, home = env_for(tmp_path, CODEX_HOME=str(tmp_path / "runtime" / "child-agents" / "X.codex-home"))
    result = sh("install.sh", env)
    assert result.returncode != 0 and "child agent's home" in result.stderr
    assert not (home / ".agentstack").exists()


def test_an_update_switches_current_to_the_new_release(tmp_path):
    """`mv` onto the current symlink moved the new link into the old release
    and left Claude and Codex on the old version (PinkMendeleev, 7e4b0b3)."""
    env, home = env_for(tmp_path)
    addon = home / ".agentstack" / "addons" / "digest-paper"
    assert sh("install.sh", env).returncode == 0
    old = os.readlink(addon / "current")
    # Pretend the old release is an earlier version.
    (addon / "releases" / "0.0.1-old").mkdir()
    (addon / "releases" / "0.0.1-old" / "marker").write_text("old")
    os.remove(addon / "current")
    os.symlink("releases/0.0.1-old", addon / "current")
    assert sh("install.sh", env).returncode == 0
    assert os.readlink(addon / "current") == old
    assert not (addon / "releases" / "0.0.1-old" / "current.new").exists()
    assert not list((addon / "releases" / "0.0.1-old").glob("*.new"))
    assert (home / ".claude" / "skills" / "digest-paper" / "SKILL.md").is_file()

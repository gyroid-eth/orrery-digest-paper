from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.parse

from conftest import SCRIPTS, png, runs_of


def run_prepare(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "prepare_input.py"), *map(str, args)],
                          capture_output=True, text=True, check=False)


def test_links_are_resolved_as_links_and_originals_are_untouched(paper):
    before = hashlib.sha256(paper["md"].read_bytes()).hexdigest()
    result = run_prepare("--input", paper["md"], "--output-dir", paper["out"],
                         "--vault-root", paper["vault"], "--image-root", paper["outside"], "--run-id", "r1")
    assert result.returncode == 0, result.stderr
    run = paper["runs"] / "r1"
    record = json.loads((run / "input.json").read_text())
    by_line = {i["line"]: i for i in record["images"]}
    statuses = [i["status"] for i in record["images"]]
    assert statuses == ["resolved", "resolved", "resolved", "remote", "unresolved"]
    assert record["summary"] == {"resolved": 3, "unresolved": 1, "remote": 1, "unique_images": 3}
    first = record["images"][0]
    assert first["asset_id"] == "a001" and (first["width"], first["height"]) == (10, 4)
    assert first["caption_nearby"].startswith("Fig. 1 | A gel sheet")
    assert record["images"][1]["caption_nearby"].startswith("Figure 2.")
    assert (run / "source" / "images" / "a002.png").is_file()
    assert (run / "source" / "paper.md").read_bytes() == paper["md"].read_bytes()
    assert hashlib.sha256(paper["md"].read_bytes()).hexdigest() == before
    assert "http(s) images are not fetched" in by_line[max(by_line) - 2]["reason"]
    assert "missing" in result.stdout or "file not found" in result.stdout


def test_a_file_uri_outside_the_allowed_folders_is_not_read(paper):
    result = run_prepare("--input", paper["md"], "--output-dir", paper["out"],
                         "--vault-root", paper["vault"], "--run-id", "r2")
    assert result.returncode == 0
    record = json.loads((paper["runs"] / "r2" / "input.json").read_text())
    uri_entry = record["images"][1]
    assert uri_entry["status"] == "unresolved"
    assert "--image-root" in uri_entry["reason"]
    assert len(list((paper["runs"] / "r2" / "source" / "images").iterdir())) == 2


def test_wiki_embeds_need_a_vault_root(paper):
    result = run_prepare("--input", paper["md"], "--output-dir", paper["out"], "--run-id", "r3")
    record = json.loads((paper["runs"] / "r3" / "input.json").read_text())
    assert record["images"][0]["reason"] == "wiki embed needs --vault-root"


def test_a_bare_name_found_twice_is_not_guessed(paper, tmp_path):
    root = tmp_path / "imgs"
    png(root / "one" / "same.png")
    png(root / "two" / "same.png")
    md = tmp_path / "bare.md"
    md.write_text("# Bare\n\n![[same.png]]\n", encoding="utf-8")
    run_prepare("--input", md, "--output-dir", paper["out"], "--image-root", root, "--run-id", "r4")
    entry = json.loads((paper["runs"] / "r4" / "input.json").read_text())["images"][0]
    assert entry["status"] == "unresolved" and len(entry["candidates"]) == 2


def test_an_existing_run_is_not_overwritten(paper):
    args = ("--input", paper["md"], "--output-dir", paper["out"], "--vault-root", paper["vault"], "--run-id", "same")
    assert run_prepare(*args).returncode == 0
    second = run_prepare(*args)
    assert second.returncode == 2 and "not overwriting" in second.stderr


def test_parentheses_in_a_file_uri_do_not_end_the_link(tmp_path):
    """The plugin writes file URIs with Node's pathToFileURL, which leaves
    parentheses unencoded (PinkMendeleev, 7e4b0b3)."""
    img = png(tmp_path / "imgs" / "figure (control) 1.png")
    # Node's pathToFileURL encodes the space but not the parentheses.
    uri = "file://" + urllib.parse.quote(str(img), safe="/()")
    assert "(control)" in uri
    md = tmp_path / "p.md"
    md.write_text(f"# P\n\n![]({uri})\n", encoding="utf-8")
    out = tmp_path / "out"
    assert run_prepare("--input", md, "--output-dir", out, "--image-root", tmp_path / "imgs",
                       "--run-id", "r").returncode == 0
    entry = json.loads((runs_of(tmp_path) / "r" / "input.json").read_text())["images"][0]
    assert entry["status"] == "resolved", entry


def test_a_repeated_image_is_snapshotted_once(paper, tmp_path):
    md = tmp_path / "twice.md"
    img = png(tmp_path / "imgs" / "one.png")
    md.write_text(f"# T\n\n![]({img.as_uri()})\n\n![]({img.as_uri()})\n", encoding="utf-8")
    out = tmp_path / "out2"
    run_prepare("--input", md, "--output-dir", out, "--image-root", tmp_path / "imgs", "--run-id", "r")
    images = json.loads((runs_of(tmp_path) / "r" / "input.json").read_text())["images"]
    assert [i["asset_id"] for i in images] == ["a001", "a001"]
    assert "snapshot" in images[0] and "snapshot" not in images[1]
    adopt = subprocess.run([sys.executable, str(SCRIPTS / "bundle.py"), "adopt", str(runs_of(tmp_path) / "r"), "a001"],
                           capture_output=True, text=True, check=False)
    assert adopt.returncode == 0 and adopt.stdout.strip() == "assets/a001.png", adopt.stderr


def test_nested_parentheses_and_broken_links_never_vanish(tmp_path):
    """figure (poly(NIPAm)).png, as Node's pathToFileURL writes it, used to
    disappear from the manifest entirely (PinkMendeleev, 352e683)."""
    img = png(tmp_path / "imgs" / "figure (poly(NIPAm)).png")
    uri = "file://" + urllib.parse.quote(str(img), safe="/()")
    md = tmp_path / "n.md"
    md.write_text(f"# N\n\n![]({uri})\n\n![](file:///broken(.png\n", encoding="utf-8")
    out = tmp_path / "out"
    result = run_prepare("--input", md, "--output-dir", out, "--image-root", tmp_path / "imgs", "--run-id", "r")
    images = json.loads((runs_of(tmp_path) / "r" / "input.json").read_text())["images"]
    assert [i["status"] for i in images] == ["resolved", "unresolved"]
    assert images[1]["reason"].startswith("not a complete image link")
    assert "not a complete image link" in result.stdout


def test_the_run_folder_is_outside_the_output_folder(paper, tmp_path):
    """A Codex reviewer cannot write into a Windows vault under /mnt/c, so the
    run lives under $AGENTSTACK_HOME, which ORRERY lets every Codex child
    write; the output folder only receives the published bundle (ProOpus,
    Windows test run of 2026-09-29)."""
    out = paper["out"]
    out.mkdir()
    out.chmod(0o555)  # stands in for a folder the reviewer cannot write
    try:
        result = run_prepare("--input", paper["md"], "--output-dir", out, "--vault-root", paper["vault"],
                             "--image-root", paper["outside"], "--run-id", "r1")
    finally:
        out.chmod(0o755)
    assert result.returncode == 0, result.stderr
    run = paper["runs"] / "r1"
    assert f"run: {run.resolve()}" in result.stdout
    assert (run / "review").is_dir() and (run / "draft" / "assets").is_dir()
    assert list(out.iterdir()) == []
    assert json.loads((run / "input.json").read_text())["output_dir"] == str(out.resolve())


def test_runs_dir_can_be_given_and_the_output_folder_is_created(paper, tmp_path):
    out = tmp_path / "new" / "Notes"
    result = run_prepare("--input", paper["md"], "--output-dir", out, "--runs-dir", tmp_path / "myruns",
                         "--run-id", "r1")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "myruns" / "r1" / "input.json").is_file() and out.is_dir()
    assert not paper["runs"].exists()


def test_an_output_folder_outside_the_vault_is_warned_about(paper):
    """162nd (2026-10-01): a note that is not in the vault Obsidian has open
    does not show up; say so before the run, instead of after."""
    outside = run_prepare("--input", paper["md"], "--output-dir", paper["out"],
                 "--vault-root", paper["vault"], "--run-id", "w1")
    assert outside.returncode == 0 and "outside the vault" in outside.stdout
    inside = run_prepare("--input", paper["md"], "--output-dir",
                paper["vault"] / "Notes", "--vault-root", paper["vault"], "--run-id", "w2")
    assert inside.returncode == 0 and "warning:" not in inside.stdout
    novault = run_prepare("--input", paper["md"], "--output-dir", paper["out"], "--run-id", "w3")
    assert "no --vault-root" in novault.stdout

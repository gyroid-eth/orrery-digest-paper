from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SCRIPTS

NOTE = """---
title: "A made-up paper about gels"
language: en
review_status: {status}
source_check: ocr-and-images
writer: Writer-Curie
reviewer: Reviewer-Bohr
run_id: r1
---

# A made-up paper about gels

- mdpaper: [[MDPapers/Sample 2024]]

![Fig. 1](assets/a001.png)
"""


def sh(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True, check=False)


@pytest.fixture
def run(paper):
    result = sh(SCRIPTS / "prepare_input.py", "--input", paper["md"], "--output-dir", paper["out"],
                "--vault-root", paper["vault"], "--image-root", paper["outside"], "--run-id", "r1")
    assert result.returncode == 0, result.stderr
    run = paper["runs"] / "r1"
    assert sh(SCRIPTS / "bundle.py", "adopt", run, "a001").stdout.strip() == "assets/a001.png"
    (run / "draft" / "evidence" / "figures.json").write_text(json.dumps(
        [{"file": "assets/a001.png", "asset_id": "a001", "figure": "Fig. 1"}]))
    (run / "draft" / "note.md").write_text(NOTE.format(status="needs-review"))
    return run


def review(run, n, verdict, digest):
    (run / "review" / f"review-{n}.md").write_text(
        f"---\nreview: {n}\ndraft_digest: {digest}\nverdict: {verdict}\nreviewer: Reviewer-Bohr\n---\n")


def digest(run):
    return sh(SCRIPTS / "bundle.py", "hash", run).stdout.strip()


def test_check_passes_a_complete_draft(run):
    result = sh(SCRIPTS / "bundle.py", "check", run)
    assert result.returncode == 0 and result.stdout.strip() == "ok"


@pytest.mark.parametrize("body, problem", [
    ("![x](/abs/a001.png)", "must be relative"),
    ("![x](file:///tmp/a.png)", "must be relative"),
    ("![x](../source/images/a001.png)", "directly in assets/"),
    ("![[assets/a001.png]]", "not ![[...]] embeds"),
    ("![x](assets/a999.png)", "is missing"),
])
def test_check_rejects_links_that_would_break_elsewhere(run, body, problem):
    note = run / "draft" / "note.md"
    note.write_text(note.read_text() + "\n" + body + "\n")
    result = sh(SCRIPTS / "bundle.py", "check", run)
    assert result.returncode == 1 and problem in result.stdout


def test_unused_assets_and_missing_fields_are_reported(run):
    sh(SCRIPTS / "bundle.py", "adopt", run, "a002")
    (run / "draft" / "note.md").write_text(NOTE.format(status="done").replace("reviewer: Reviewer-Bohr\n", ""))
    out = sh(SCRIPTS / "bundle.py", "check", run).stdout
    assert "assets/a002.png is not used" in out
    assert "`reviewer` is missing" in out
    assert "review_status must be one of" in out


def test_checked_needs_an_approval_of_exactly_this_draft(run):
    note = run / "draft" / "note.md"
    note.write_text(NOTE.format(status="checked"))
    first = digest(run)
    review(run, 1, "changes-requested", first)
    refused = sh(SCRIPTS / "bundle.py", "publish", run)
    assert refused.returncode == 1 and "did not approve the current draft" in refused.stderr
    review(run, 2, "approved", first)
    note.write_text(note.read_text() + "\nA later edit.\n")  # the approved draft changed
    stale = sh(SCRIPTS / "bundle.py", "publish", run)
    assert stale.returncode == 1 and "did not approve the current draft" in stale.stderr
    review(run, 3, "approved", digest(run))
    published = sh(SCRIPTS / "bundle.py", "publish", run)
    assert published.returncode == 0, published.stderr
    bundle = published.stdout.strip().rsplit("/", 1)[0]
    result = json.loads(open(f"{bundle}/evidence/result.json").read())
    assert result["review_status"] == "checked" and result["reviewed_digest"] == result["draft_digest"]
    assert open(f"{bundle}/evidence/review.md").read().startswith("---\nreview: 3")


def test_needs_review_can_be_published_and_is_never_overwritten(run):
    first = sh(SCRIPTS / "bundle.py", "publish", run)
    assert first.returncode == 0, first.stderr
    again = sh(SCRIPTS / "bundle.py", "publish", run)
    assert again.returncode == 1 and "not overwriting" in again.stderr
    revision = sh(SCRIPTS / "bundle.py", "publish", run, "--revision")
    assert revision.returncode == 0 and "-r2/=MadeupPaperGels-r2=.md" in revision.stdout
    assert not [p for p in (run.parent.parent).iterdir() if p.name.startswith(".staging-")]


ONIMARU = """---
title: "The fin-to-limb transition as the re-organization of a Turing pattern"
authors: "Koh Onimaru, Luciano Marcon, Marco Musy, Mikiko Tanaka, James Sharpe"
year: 2016
language: en
review_status: checked
source_check: ocr-and-images
writer: Writer-Curie
reviewer: Reviewer-Bohr
run_id: r1
---

- mdpaper: [[MDPapers/Sample 2024]]

![Fig. 1](assets/a001.png)
"""


def test_publish_names_the_note_after_a_better_bibtex_citekey(run):
    """Shuto 2026-09-30: note.md is hard to link to in Obsidian; name it like
    the Lit notes made from Zotero (=citekey=.md)."""
    (run / "draft" / "note.md").write_text(ONIMARU)
    approved = digest(run)
    review(run, 1, "approved", approved)
    published = sh(SCRIPTS / "bundle.py", "publish", run)
    assert published.returncode == 0, published.stderr
    note = Path(published.stdout.strip())
    key = "OnimaruFintolimbTransitionReorganization2016"
    assert note.name == f"={key}=.md" and note.parent.name.startswith(f"{key}-")
    assert not (note.parent / "note.md").exists()
    assert note.read_text() == ONIMARU
    # The rename leaves the approved content, and so the digest, unchanged.
    result = json.loads((note.parent / "evidence" / "result.json").read_text())
    assert result["note_file"] == note.name
    assert result["draft_digest"] == result["reviewed_digest"] == approved


@pytest.mark.parametrize("authors, title, year, key", [
    ('"Onimaru K, Marcon L"', "Pattern", "2016", "OnimaruPattern2016"),
    ('"Onimaru, K., Marcon, L."', "Pattern", "2016", "OnimaruPattern2016"),
    ('"K. Onimaru and L. Marcon"', "Pattern", "2016", "OnimaruPattern2016"),
    ("\n  - Alexander Heyde\n  - L. Mahadevan", "Self-organized biotectonics of termite nests", "2021",
     "HeydeSelforganizedBiotectonicsTermite2021"),
    ('"Rico-Guevara A"', "The Hummingbird Tongue Is a Fluid Trap", "2011", "Rico-guevaraHummingbirdTongueFluid2011"),
    ('"Müller J"', "The mechanics of non-Euclidean plates", "2010", "MullerMechanicsNonEuclideanPlates2010"),
    ('"K. LI"', "Gels", "2024", "LiGels2024"),
    ('"LI, K., Yang, X."', "Gels", "2024", "LiGels2024"),
    ('"LI K, YANG X"', "Gels", "2024", "LiGels2024"),
    ('"<authors as printed>"', "A made-up paper about gels", "<year>", "MadeupPaperGels"),
    ('"鬼丸 洸"', "ひれから肢への転換", "2016", "ひれから肢への転換2016"),
])
def test_citekey_from_front_matter(authors, title, year, key):
    sys.path.insert(0, str(SCRIPTS))
    from bundle import front_matter, make_citekey
    text = f"---\ntitle: {title}\nauthors: {authors}\nyear: {year}\n---\n"
    assert make_citekey(front_matter(text), text) == key


def test_another_paper_with_the_same_key_gets_a_letter(paper, run):
    """VioletBohr 2026-09-30: two papers with the same author, year and title
    words, and a revision, all made the same note name in different folders,
    and an Obsidian link to that name opened one of them at random."""
    first = Path(sh(SCRIPTS / "bundle.py", "publish", run).stdout.strip())
    other_md = paper["vault"] / "MDPapers" / "Other 2024.md"
    other_md.write_text("# A made-up paper about gels\n\nA different text.\n")
    other = prepare({**paper, "md": other_md}, "other")
    (other / "draft" / "evidence" / "figures.json").write_text("[]")
    (other / "draft" / "note.md").write_text(NOTE.format(status="needs-review").replace(
        "- mdpaper: [[MDPapers/Sample 2024]]", "- mdpaper: [[MDPapers/Other 2024]]").replace(
        "![Fig. 1](assets/a001.png)\n", ""))
    second = sh(SCRIPTS / "bundle.py", "publish", other)
    assert second.returncode == 0, second.stderr
    names = [first.name, Path(second.stdout.strip()).name,
             Path(sh(SCRIPTS / "bundle.py", "publish", run, "--revision").stdout.strip()).name]
    assert names == ["=MadeupPaperGels=.md", "=MadeupPaperGelsa=.md", "=MadeupPaperGels-r2=.md"]


def links_of(run):
    return json.loads((run / "input.json").read_text())["links"]


def test_the_note_links_to_the_markdown_paper_and_its_pdf(paper, run):
    """Shuto 2026-09-30: the note named the source as an absolute path in code,
    which does not open in Obsidian (and a WSL path means nothing on Windows)."""
    links = links_of(run)
    assert links["mdpaper"] == "[[MDPapers/Sample 2024]]" and links["pdf"] is None
    assert "no PDF of the paper in the vault" in links["notes"][0]
    note = run / "draft" / "note.md"
    note.write_text(note.read_text().replace("- mdpaper: [[MDPapers/Sample 2024]]\n", ""))
    out = sh(SCRIPTS / "bundle.py", "check", run).stdout
    assert "add the line `- mdpaper: [[MDPapers/Sample 2024]]`" in out


def prepare(paper, run_id, *extra):
    result = sh(SCRIPTS / "prepare_input.py", "--input", paper["md"], "--output-dir", paper["out"],
                "--vault-root", paper["vault"], "--run-id", run_id, *extra)
    assert result.returncode == 0, result.stderr
    return paper["runs"] / run_id


def test_the_pdf_is_found_by_the_markdown_name_and_never_guessed(paper):
    import unicodedata
    pdf = paper["vault"] / "Zotero" / "Sample 2024.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4\n")
    assert links_of(prepare(paper, "one"))["pdf"] == "[[Zotero/Sample 2024.pdf]]"
    (paper["vault"] / "Other").mkdir()
    (paper["vault"] / "Other" / "Sample 2024.pdf").write_bytes(b"%PDF-1.4\n")
    two = links_of(prepare(paper, "two"))
    assert two["pdf"] is None and "several PDFs match" in two["notes"][0]
    # A Zotero record names its own PDF, which wins over the search by name.
    bib = paper["vault"].parent / "bib.json"
    bib.write_text(json.dumps({"source": "zotero", "citekey": "K", "item_key": "I", "attachments": [
        {"is_pdf": True, "local_path": str(paper["vault"] / "Other" / "Sample 2024.pdf")}]}))
    assert links_of(prepare(paper, "zotero", "--bib", bib))["pdf"] == "[[Other/Sample 2024.pdf]]"
    # A macOS (NFD) name is linked in NFC, the spelling Obsidian writes.
    nfd = paper["vault"] / "Zotero" / unicodedata.normalize("NFD", "ゲル.pdf")
    md = paper["vault"] / "MDPapers" / unicodedata.normalize("NFD", "ゲル.md")
    nfd.write_bytes(b"%PDF-1.4\n")
    md.write_text("# ゲル\n")
    links = links_of(prepare({**paper, "md": md}, "nfd"))
    assert links["pdf"] == unicodedata.normalize("NFC", "[[Zotero/ゲル.pdf]]")
    assert links["mdpaper"] == unicodedata.normalize("NFC", "[[MDPapers/ゲル]]")


def test_a_name_a_wikilink_cannot_hold_becomes_a_markdown_link(paper):
    md = paper["vault"] / "MDPapers" / "Gels #2 [review].md"
    md.write_text("# Gels\n")
    links = links_of(prepare({**paper, "md": md}, "odd"))
    assert links["mdpaper"] == "[Gels #2 [review]](<MDPapers/Gels #2 [review].md>)"


def test_setting_the_status_after_approval_needs_no_new_review(run):
    """E2E 2026-09-29: the writer had to ask for a third review only because
    it changed review_status from needs-review to checked."""
    note = run / "draft" / "note.md"
    approved = digest(run)
    review(run, 1, "changes-requested", approved)
    review(run, 2, "approved", approved)
    note.write_text(note.read_text().replace("review_status: needs-review", "review_status: checked"))
    assert digest(run) == approved
    published = sh(SCRIPTS / "bundle.py", "publish", run)
    assert published.returncode == 0, published.stderr
    bundle = published.stdout.strip().rsplit("/", 1)[0]
    import os
    assert sorted(os.listdir(f"{bundle}/evidence/reviews")) == ["review-1.md", "review-2.md"]
    # Any other change to the note still needs a review of the new digest.
    note.write_text(note.read_text() + "\nMore text.\n")
    assert digest(run) != approved


def test_assets_in_a_subfolder_are_rejected(run):
    """A subfolder passed check but was outside the digest, so a figure could
    be swapped after approval (PinkMendeleev P2-4, 7e4b0b3)."""
    sub = run / "draft" / "assets" / "fig1"
    sub.mkdir()
    (sub / "panel.png").write_bytes((run / "draft" / "assets" / "a001.png").read_bytes())
    note = run / "draft" / "note.md"
    note.write_text(note.read_text() + "\n![p](assets/fig1/panel.png)\n")
    out = sh(SCRIPTS / "bundle.py", "check", run).stdout
    assert "must point to a file directly in assets/" in out
    assert "assets/fig1 is a folder" in out


def test_every_adopted_figure_needs_a_row_in_figures_json(run):
    (run / "draft" / "evidence" / "figures.json").write_text("[]")
    out = sh(SCRIPTS / "bundle.py", "check", run).stdout
    assert "assets/a001.png has no row in figures.json" in out

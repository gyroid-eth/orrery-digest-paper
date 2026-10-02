from __future__ import annotations

import json
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from conftest import SCRIPTS

pytest.importorskip("pypdfium2")
pytest.importorskip("PIL")


def make_pdf(path: Path, text: str | None, image: bool) -> Path:
    """A one-page PDF: Helvetica text (a text layer) and/or a 2 x 2 inch raster
    image, so the tests need no real paper."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               "/Resources << /Font << /F1 5 0 R >> /XObject << /Im1 6 0 R >> >> >>"]
    content = ""
    if text:
        lines = " ".join(f"({line}) Tj 0 -16 Td" for line in text.split("\n"))
        content += f"BT /F1 12 Tf 72 720 Td {lines} ET\n"
    if image:
        content += "q 144 0 0 144 72 400 cm /Im1 Do Q\n"
    objects.append(f"<< /Length {len(content)} >>\nstream\n{content}endstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pixels = zlib.compress(b"".join(bytes([200, (x * 2) % 256, 40]) for _ in range(120) for x in range(120)))
    objects.append(f"<< /Type /XObject /Subtype /Image /Width 120 /Height 120 /ColorSpace /DeviceRGB "
                   f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(pixels)} >>\nstream\n")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}".encode("latin-1")
        if number == 6:
            out += pixels + b"\nendstream"
        out += b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


TEXT = "Gels fold under light\nFig. 1 | A gel sheet folds into a cone.\n# not a heading\nThe swelling may suggest a mechanism."


def convert(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "pdf_local.py"), *map(str, args)],
                          capture_output=True, text=True, check=False)


def test_a_pdf_becomes_markdown_with_figures_inside_the_vault(tmp_path):
    vault = tmp_path / "vault"
    pdf = make_pdf(vault / "Papers" / "Sample 2024.pdf", TEXT, image=True)
    result = convert("--pdf", pdf, "--vault-root", vault)
    assert result.returncode == 0, result.stderr
    md = vault / "20_MDPapers" / "Sample 2024 (local).md"
    text = md.read_text()
    assert text.startswith("---\nconverter: local-pdfium\n")
    assert 'source_pdf: "Papers/Sample 2024.pdf"' in text
    assert "Gels fold under light" in text and "may suggest" in text
    # A line of the paper that looks like Markdown stays text.
    assert "\\# not a heading" in text and "\n# not a heading" not in text
    images = vault / "20_MDPapers" / "local-images"
    assert (images / "Sample 2024_p01-fig1.png").is_file() and (images / "Sample 2024_p01.png").is_file()
    assert "![[20_MDPapers/local-images/Sample 2024_p01-fig1.png]]" in text
    from PIL import Image
    with Image.open(images / "Sample 2024_p01-fig1.png") as figure:
        # 2 inches at 300 dpi, plus the margin for labels around it.
        assert 600 <= figure.width <= 720


def test_it_never_overwrites_and_stops_on_a_scan(tmp_path):
    vault = tmp_path / "vault"
    pdf = make_pdf(vault / "Sample.pdf", TEXT, image=False)
    assert convert("--pdf", pdf, "--vault-root", vault).returncode == 0
    again = convert("--pdf", pdf, "--vault-root", vault)
    assert again.returncode == 2 and "not overwriting" in again.stderr
    scan = make_pdf(vault / "Scan.pdf", None, image=True)
    stopped = convert("--pdf", scan, "--vault-root", vault)
    assert stopped.returncode == 3 and "no text layer" in stopped.stderr
    assert not (vault / "20_MDPapers" / "Scan (local).md").exists()
    outside = convert("--pdf", pdf, "--vault-root", vault, "--out-dir", tmp_path / "elsewhere")
    assert outside.returncode == 2 and "inside the vault" in outside.stderr


def test_the_run_and_the_note_carry_the_local_conversion(tmp_path):
    vault = tmp_path / "vault"
    pdf = make_pdf(vault / "Papers" / "Sample 2024.pdf", TEXT, image=True)
    assert convert("--pdf", pdf, "--vault-root", vault).returncode == 0
    md = vault / "20_MDPapers" / "Sample 2024 (local).md"
    sh = lambda *a: subprocess.run([sys.executable, *map(str, a)], capture_output=True, text=True, check=False)
    prepared = sh(SCRIPTS / "prepare_input.py", "--input", md, "--output-dir", tmp_path / "out",
                  "--vault-root", vault, "--run-id", "l1")
    assert prepared.returncode == 0 and "converter: local-pdfium" in prepared.stdout, prepared.stderr
    run = tmp_path / "agentstack" / "addons" / "digest-paper" / "runs" / "l1"
    record = json.loads((run / "input.json").read_text())
    assert record["source"]["converter"] == "local-pdfium"
    # The PDF is found from the front matter, though the Markdown's name differs.
    assert record["links"]["pdf"] == "[[Papers/Sample 2024.pdf]]"
    assert record["summary"]["resolved"] == 2
    sh(SCRIPTS / "bundle.py", "adopt", run, "a001")
    (run / "draft" / "evidence" / "figures.json").write_text(json.dumps([{"file": "assets/a001.png"}]))
    note = ("---\ntitle: Gels fold under light\nlanguage: en\nreview_status: needs-review\n"
            "source_check: local-text-and-page-images\nwriter: W\nwriter_program: claude\nreviewer: R\n"
            "reviewer_program: codex\nreview_pairing: cross-vendor\nrun_id: l1\n{extra}---\n\n"
            "- pdf: [[Papers/Sample 2024.pdf]]\n- mdpaper: [[20_MDPapers/Sample 2024 (local)]]\n"
            "{line}- Review pairing: cross-vendor\n\n![Fig. 1](assets/a001.png)\n")
    (run / "draft" / "note.md").write_text(note.format(extra="", line=""))
    missing = sh(SCRIPTS / "bundle.py", "check", run).stdout
    assert "`source_converter` must be 'local-pdfium'" in missing and "Conversion: local-pdfium" in missing
    (run / "draft" / "note.md").write_text(note.format(
        extra="source_converter: local-pdfium\n", line="- 変換 / Conversion: local-pdfium — PDF をこの機械で変換した。\n"))
    assert sh(SCRIPTS / "bundle.py", "check", run).stdout.strip() == "ok"
    published = sh(SCRIPTS / "bundle.py", "publish", run)
    assert published.returncode == 0, published.stderr
    result = json.loads((Path(published.stdout.strip()).parent / "evidence" / "result.json").read_text())
    assert result["source_converter"] == "local-pdfium"

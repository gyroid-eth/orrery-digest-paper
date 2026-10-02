#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pypdfium2==5.13.0", "pillow>=10"]
# ///
"""Convert a PDF to a Markdown paper with figure images on this machine, for
when the pdf-mistral plugin cannot be used (no Mistral API key, or a paper that
must not be sent out). The output has the shape digest-paper reads, so the
rest of the run is unchanged.

    uv run SKILL_DIR/scripts/pdf_local.py --pdf PAPER.pdf --vault-root VAULT
                                          [--out-dir DIR] [--images-dir DIR]

Writes, without overwriting anything:

    DIR/<pdf name> (local).md            text of each page, with figure embeds
    IMAGES/<pdf name>_p03-fig1.png       each figure found from its caption, cut from a 300 dpi render
    IMAGES/<pdf name>_p03.png            each page as a whole, 150 dpi

DIR defaults to VAULT/20_MDPapers and IMAGES to DIR/local-images. The name ends
in "(local)" so a later pdf-mistral conversion of the same PDF never collides
with it; when both exist, use the pdf-mistral one.

What this is not: there is no OCR (a scanned PDF without a text layer stops
here), the text keeps the PDF's own reading order (two columns, equations and
tables can come out jumbled), and a figure is cut out only when its caption is
found ("Fig. 2 |", "Figure 2." at the start of a line); otherwise, or when a
cut-out misses a panel, it is only in the page image. The Markdown says so in
its front matter (`converter: local-pdfium`), and digest-paper carries that
into the note.

Nothing is sent anywhere. `uv run` fetches pypdfium2 and Pillow from PyPI once.

Exit status: 0 written, 2 usage error or something would be overwritten,
3 no text layer (scanned PDF).
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

CONVERTER = "local-pdfium"
FIGURE_DPI = 300
PAGE_DPI = 150
# An image object smaller than this (in inches, either side) is a logo, an
# icon or a bit of a glyph, not a figure.
MIN_FIGURE_INCHES = 1.0
# Panel letters and axis labels are often drawn as text just outside the
# raster image; cut this much more (points) around it.
MARGIN_PT = 14
# A page with fewer characters than this counts as having no text layer.
MIN_PAGE_CHARS = 40


def load_pdfium():
    try:
        import pypdfium2
        import pypdfium2.raw
    except ImportError:
        print("pdf_local: pypdfium2 is not available. Run this script with uv, which fetches it:\n"
              f"  uv run {Path(__file__).resolve()} --pdf ... --vault-root ...", file=sys.stderr)
        raise SystemExit(2)
    return pypdfium2, pypdfium2.raw


def vault_link(path: Path, vault: Path) -> str:
    return unicodedata.normalize("NFC", path.resolve().relative_to(vault).as_posix())


def page_text(page) -> str:
    text = page.get_textpage().get_text_bounded()
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = []
    for line in text.split("\n"):
        line = line.rstrip()
        # The text is material, not Markdown: keep a line that starts like a
        # heading, a list, a quote or an embed from becoming one.
        if re.match(r"^\s*(#|>|!\[|-\s|\*\s|\d+\.\s|```|---)", line):
            line = "\\" + line.lstrip()
        lines.append(line)
    return "\n".join(lines).strip()


# A caption starts a line with its figure number and a separator ("Fig. 2 |",
# "Figure 2.", "図 2:"); "(Fig. 3)." in running text does not.
CAPTION = re.compile(r"(?:Fig\.|Figure|FIGURE|図)[ \t]*\d+[a-z]?[ \t]*[|.:｜]")
CAPTION_WORDS = ("Fig.", "Figure", "FIGURE", "図")
# Running headers and footers live in these bands (points from the edge).
EDGE_BAND = 45


def raster_boxes(page, raw) -> list[tuple[float, float, float, float]]:
    """Raster images on the page large enough to be figures."""
    boxes = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=0):
        left, bottom, right, top = obj.get_bounds()
        if min(right - left, top - bottom) >= MIN_FIGURE_INCHES * 72:
            boxes.append((left, bottom, right, top))
    return boxes


def figure_boxes(page, raw, textpage) -> list[tuple[tuple[float, float, float, float], str]]:
    """Each figure on the page as (box, caption start), top to bottom.

    A figure is what is drawn above its caption ("Fig. 2 |", "Figure 2.", ...
    at the start of a line): every image and vector path between the caption
    and the previous caption (or the header band), so vector panels and their
    labels come with it. Without a caption on the page, large raster images
    are used alone. Boxes are in PDF points (left, bottom, right, top)."""
    width, height = page.get_size()
    captions = []
    seen = set()
    for word in CAPTION_WORDS:
        # pdfium's own search, so the index matches its character boxes.
        searcher = textpage.search(word, match_case=True)
        while (found := searcher.get_next()) is not None:
            index = found[0]
            if index in seen:
                continue
            seen.add(index)
            before = textpage.get_text_range(max(0, index - 1), 1) if index else "\n"
            if before not in ("\n", "\r", ""):
                continue  # not at the start of a line
            head = textpage.get_text_range(index, 200)
            if not CAPTION.match(head):
                continue
            first = textpage.get_charbox(index)
            line = re.split(r"[\r\n]", head, maxsplit=1)[0]
            captions.append((first[3], first[0], line.strip()[:160]))
    graphics = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH,
                                        raw.FPDF_PAGEOBJ_SHADING, raw.FPDF_PAGEOBJ_FORM], max_depth=0):
        left, bottom, right, top = obj.get_bounds()
        if bottom >= height - EDGE_BAND or top <= EDGE_BAND:
            continue  # header or footer rule
        if right - left > width * 0.9 and top - bottom < 3:
            continue  # a full-width rule
        graphics.append((left, bottom, right, top))
    spans = []
    for caption_top, caption_left, caption in captions:
        # The figure's column, from what is drawn just above the caption: a
        # figure across the middle of the page is full width; otherwise it
        # sits in the caption's half (two-column pages).
        near = [g for g in graphics if caption_top - 2 <= g[1] <= caption_top + 60]
        middle = width / 2
        if near and min(g[0] for g in near) < middle - 20 and max(g[2] for g in near) > middle + 20:
            span = (0.0, width)
        elif (min(g[0] for g in near) if near else caption_left) < middle - 20:
            span = (0.0, middle)
        else:
            span = (middle, width)
        spans.append((caption_top, span, caption))
    captions = spans
    figures = []
    for caption_top, span, caption in captions:
        # Below the nearest caption above it in the same column (or the header).
        above = [c[0] for c in captions if c[0] > caption_top + 2 and c[1][0] < span[1] and span[0] < c[1][1]]
        ceiling = min(above) - 12 if above else height - EDGE_BAND
        inside = [g for g in graphics
                  if g[1] >= caption_top - 2 and g[3] <= ceiling + 2
                  and span[0] - 6 <= (g[0] + g[2]) / 2 <= span[1] + 6]
        if inside:
            box = (min(g[0] for g in inside), min(g[1] for g in inside),
                   max(g[2] for g in inside), max(g[3] for g in inside))
            if min(box[2] - box[0], box[3] - box[1]) >= MIN_FIGURE_INCHES * 72:
                figures.append((box, caption))
    figures.sort(key=lambda f: (f[0][0] >= width / 2, -f[0][3]))
    if figures:
        return figures
    boxes = sorted(raster_boxes(page, raw), key=lambda b: (-b[3], b[0]))
    kept: list[tuple[float, float, float, float]] = []
    for box in boxes:
        if not any(box[0] >= k[0] - 1 and box[1] >= k[1] - 1 and box[2] <= k[2] + 1 and box[3] <= k[3] + 1
                   for k in kept):
            kept.append(box)
    return [(box, "") for box in kept]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--vault-root", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, help="default: VAULT/20_MDPapers")
    parser.add_argument("--images-dir", type=Path, help="default: OUT/local-images")
    args = parser.parse_args(argv)

    pdf_path = args.pdf.expanduser().resolve()
    vault = args.vault_root.expanduser().resolve()
    if not pdf_path.is_file():
        print(f"pdf_local: not a file: {pdf_path}", file=sys.stderr)
        return 2
    if not vault.is_dir():
        print(f"pdf_local: --vault-root is not a folder: {vault}", file=sys.stderr)
        return 2
    out_dir = (args.out_dir.expanduser() if args.out_dir else vault / "20_MDPapers").resolve()
    images_dir = (args.images_dir.expanduser() if args.images_dir else out_dir / "local-images").resolve()
    for label, folder in (("--out-dir", out_dir), ("--images-dir", images_dir)):
        if vault != folder and vault not in folder.parents:
            print(f"pdf_local: {label} must be inside the vault, so Obsidian can show the figures: {folder}",
                  file=sys.stderr)
            return 2

    pdfium, raw = load_pdfium()
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except pdfium.PdfiumError as exc:
        print(f"pdf_local: cannot open the PDF ({exc}): {pdf_path}", file=sys.stderr)
        return 2

    stem = unicodedata.normalize("NFC", pdf_path.stem)
    md_path = out_dir / f"{stem} (local).md"
    pages = []
    for index in range(len(pdf)):
        page = pdf[index]
        number = f"{index + 1:02d}" if len(pdf) < 100 else f"{index + 1:03d}"
        # Object and character positions are in the unrotated page; work there
        # and turn each cut-out afterwards (a /Rotate page otherwise crops
        # the wrong part, often blank).
        rotation = page.get_rotation()
        if rotation:
            page.set_rotation(0)
        textpage = page.get_textpage()
        found = figure_boxes(page, raw, textpage)
        boxes = [box for box, _ in found]
        pages.append({"page": page, "number": number, "text": page_text(page), "boxes": boxes,
                      "rotation": rotation,
                      "captions": [caption for _, caption in found],
                      "page_image": images_dir / f"{stem}_p{number}.png",
                      "figures": [images_dir / f"{stem}_p{number}-fig{k + 1}.png" for k in range(len(boxes))]})

    if sum(len(p["text"]) >= MIN_PAGE_CHARS for p in pages) == 0:
        print("pdf_local: this PDF has no text layer (a scan?). This converter does no OCR; "
              "use the pdf-mistral plugin for it, or a PDF with text.", file=sys.stderr)
        return 3

    targets = [md_path] + [path for p in pages for path in [p["page_image"], *p["figures"]]]
    existing = [path for path in targets if path.exists()]
    if existing:
        print("pdf_local: not overwriting what is already there:", file=sys.stderr)
        for path in existing[:5]:
            print(f"  {path}", file=sys.stderr)
        print("Remove it first if you want a new conversion.", file=sys.stderr)
        return 2

    images_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    title = (pdf.get_metadata_dict().get("Title") or "").strip() or stem
    try:
        pdf_link = vault_link(pdf_path, vault)
    except ValueError:
        pdf_link = ""
    version = __import__("importlib.metadata").metadata.version("pypdfium2")
    quote = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    out = ["---", f"converter: {CONVERTER}", f"converter_version: {quote('pypdfium2 ' + version)}"]
    if pdf_link:
        out.append(f"source_pdf: {quote(pdf_link)}")
    out += [f"pages: {len(pdf)}", f"figure_dpi: {FIGURE_DPI}", "---", "",
            f"# {title}", "",
            "> [!warning] この Markdown は、PDF をこの機械で変換したもの（pdf-mistral ではない）。"
            "OCR はしていない。2 段組・数式・表は崩れていることがある。図はキャプションから見つけたものを、"
            "ベクターの部分も含めて切り出した。見つからない図や、パネルが欠けた切り出しは、ページ全体の画像で確かめる。",
            ""]
    figures = 0
    for p in pages:
        page = p["page"]
        width_pt, height_pt = page.get_size()
        out += [f"## p. {int(p['number'])}", ""]
        out += [p["text"] or "（このページには文字の層が無い）", ""]
        if p["boxes"]:
            scale = FIGURE_DPI / 72
            image = page.render(scale=scale).to_pil()
            for box, path, caption in zip(p["boxes"], p["figures"], p["captions"]):
                left, bottom, right, top = (box[0] - MARGIN_PT, box[1] - MARGIN_PT,
                                            box[2] + MARGIN_PT, box[3] + MARGIN_PT)
                crop = (max(0, int(left * scale)), max(0, int((height_pt - top) * scale)),
                        min(image.width, int(right * scale) + 1), min(image.height, int((height_pt - bottom) * scale) + 1))
                cut = image.crop(crop)
                if p["rotation"]:
                    # /Rotate turns the page clockwise; PIL turns counterclockwise.
                    cut = cut.rotate(-p["rotation"], expand=True)
                cut.save(path)
                out += [f"![[{vault_link(path, vault)}]]", ""]
                if caption:
                    # The caption's first line again, right under its figure,
                    # so the figure can be matched to its number.
                    out += [caption.replace("[", "\\[").replace("]", "\\]"), ""]
                figures += 1
        if p["rotation"]:
            page.set_rotation(p["rotation"])
        page.render(scale=PAGE_DPI / 72).to_pil().save(p["page_image"])
        out += [f"![[{vault_link(p['page_image'], vault)}]]", ""]
    md_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")

    print(f"markdown: {md_path}")
    print(f"images: {images_dir} ({figures} figures cut out, {len(pages)} page images)")
    thin = [str(int(p["number"])) for p in pages if len(p["text"]) < MIN_PAGE_CHARS]
    if thin:
        print(f"note: pages with little or no text: {', '.join(thin)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

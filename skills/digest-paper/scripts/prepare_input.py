#!/usr/bin/env python3
"""Snapshot one pdf-mistral Markdown paper and its figures into a run folder.

    prepare_input.py --input PAPER.md --output-dir DIR [--vault-root DIR]
                     [--image-root DIR ...] [--lang ja|en] [--run-id ID]
                     [--runs-dir RUNS]

Reads only. The original Markdown and images are never modified. Creates

    RUNS/<run-id>/
      input.json          what was read, how each image link resolved, hashes
      source/paper.md     a copy of the Markdown
      source/images/      a copy of each resolved image, named a001.png ...
      draft/              empty; the writer puts note.md, assets/, evidence/ here
      review/             empty; the reviewer writes review-<n>.md here
      tasks/              empty; the coordinator writes the other agent's task here

RUNS defaults to $AGENTSTACK_HOME/addons/digest-paper/runs (AGENTSTACK_HOME
defaults to ~/.agentstack), not the output folder: ORRERY lets every Codex
child write under $AGENTSTACK_HOME, while an output folder such as a Windows
vault under /mnt/c is outside a Codex child's sandbox. DIR receives only the
published bundle (bundle.py publish).

Image links are parsed as links, never guessed from a bare file name:

  ![[path/to/img.png|500]]   Obsidian embed, resolved from --vault-root (a bare
                             file name is looked up only inside --image-root)
  ![alt](relative/img.png)   relative to the Markdown file
  ![alt](file:///C:/...)     a file URI; on WSL a Windows path is converted with
                             `wslpath`; percent-encoding is decoded

A resolved file must lie inside the Markdown's folder, --vault-root or an
--image-root; anything else is reported as unresolved and not read. http(s)
images are never fetched. Nothing here calls a network service.

Exit status: 0 when the snapshot was written (unresolved images are listed in
input.json and on stdout), 2 on a usage error.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mdlinks import image_links  # noqa: E402

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
FIG_LINE = re.compile(r"^\s*(?:\*\*)?(fig(?:ure)?\.?\s*\d+|図\s*\d+)", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_size(path: Path) -> tuple[int, int] | None:
    """Width and height from the file header (PNG, JPEG, GIF), no dependency."""
    try:
        with path.open("rb") as handle:
            head = handle.read(32)
            if head.startswith(b"\x89PNG\r\n\x1a\n") and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head.startswith(b"\xff\xd8"):
                handle.seek(2)
                while True:
                    marker = handle.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    if marker[1] in (0xD8, 0x01) or 0xD0 <= marker[1] <= 0xD7:
                        continue
                    length = struct.unpack(">H", handle.read(2))[0]
                    if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                        handle.read(1)
                        height, width = struct.unpack(">HH", handle.read(4))
                        return width, height
                    handle.seek(length - 2, 1)
    except (OSError, struct.error):
        return None
    return None


def on_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def windows_to_local(path_text: str) -> Path | None:
    """C:/Users/... or C:\\Users\\... -> a local path (WSL via wslpath)."""
    if not re.match(r"^[A-Za-z]:[\\/]", path_text):
        return None
    if not on_wsl():
        return None
    try:
        out = subprocess.run(["wslpath", "-u", path_text], capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out) if out else None


def existing_variant(path: Path) -> Path | None:
    """The path as written, or its NFC / NFD spelling (macOS vs Windows names)."""
    for form in (None, "NFC", "NFD"):
        candidate = Path(unicodedata.normalize(form, str(path))) if form else path
        if candidate.is_file():
            return candidate
    return None


def inside(path: Path, roots: list[Path]) -> bool:
    real = path.resolve()
    for root in roots:
        try:
            real.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def find_by_name(name: str, roots: list[Path]) -> list[Path]:
    wanted = {unicodedata.normalize("NFC", name)}
    hits = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for file in files:
                if unicodedata.normalize("NFC", file) in wanted:
                    hits.append(Path(dirpath) / file)
    return hits


def resolve(ref: dict, md_dir: Path, vault_root: Path | None,
            image_roots: list[Path]) -> dict:
    """Fill ref['status'] and ref['resolved'] for one image reference."""
    allowed = [md_dir] + ([vault_root] if vault_root else []) + image_roots
    target = ref["target"]
    candidate: Path | None = None
    if ref["kind"] == "broken":
        return {**ref, "status": "unresolved", "reason": "not a complete image link; check the Markdown"}
    if ref["kind"] == "wiki":
        target = target.split("|", 1)[0].split("#", 1)[0].strip()
        if vault_root is None and "/" in target:
            return {**ref, "status": "unresolved", "reason": "wiki embed needs --vault-root"}
        if "/" in target:
            candidate = existing_variant(vault_root / target) if vault_root else None
        else:
            hits = find_by_name(target, image_roots) if image_roots else []
            if len(hits) > 1:
                return {**ref, "status": "unresolved",
                        "reason": "same file name in several places",
                        "candidates": [str(h) for h in hits]}
            candidate = hits[0] if hits else existing_variant(md_dir / target)
    else:
        url = target[1:-1] if target.startswith("<") else target
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("http", "https"):
            return {**ref, "status": "remote", "reason": "http(s) images are not fetched"}
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                return {**ref, "status": "unresolved", "reason": "file URI on another host"}
            path_text = urllib.parse.unquote(parsed.path)
            if re.match(r"^/[A-Za-z]:/", path_text):
                local = windows_to_local(path_text[1:])
                if local is None:
                    return {**ref, "status": "unresolved",
                            "reason": "Windows path; pass the folder with --image-root "
                                      "after converting it, or run on WSL"}
                candidate = existing_variant(local)
            else:
                candidate = existing_variant(Path(path_text))
        elif parsed.scheme == "":
            candidate = existing_variant(md_dir / urllib.parse.unquote(url))
        else:
            return {**ref, "status": "unresolved", "reason": f"unsupported scheme {parsed.scheme}"}
    if candidate is None:
        return {**ref, "status": "unresolved", "reason": "file not found"}
    if not inside(candidate, allowed):
        return {**ref, "status": "unresolved",
                "reason": "outside the allowed folders; add its folder with --image-root",
                "path": str(candidate)}
    if candidate.suffix.lower() not in IMAGE_EXTS:
        return {**ref, "status": "unresolved", "reason": "not an image file"}
    return {**ref, "status": "resolved", "resolved": str(candidate)}


def nearby_caption(lines: list[str], index: int) -> str:
    """The first figure-caption-like line within the next 6 lines, if any."""
    for line in lines[index + 1:index + 7]:
        if FIG_LINE.match(line):
            return line.strip()[:400]
    return ""


def default_runs_dir() -> Path:
    home = os.environ.get("AGENTSTACK_HOME") or str(Path.home() / ".agentstack")
    return Path(home).expanduser() / "addons" / "digest-paper" / "runs"


def slug(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(r"[\\/:*?\"<>|#^\[\]\x00-\x1f]", " ", text)
    text = re.sub(r"\s+", "-", text).strip("-.")
    return text[:limit].rstrip("-.") or "paper"


WIKILINK_UNSAFE = re.compile(r"[\[\]|#^]")


def vault_link(path: Path, vault_root: Path, drop_suffix: bool = False) -> str | None:
    """An Obsidian link to a file in the vault, relative to its root in NFC (a
    macOS vault and a Windows vault spell the same name alike). A name that a
    wikilink cannot hold becomes a Markdown link."""
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except ValueError:
        return None
    rel_text = unicodedata.normalize("NFC", rel.as_posix())
    if drop_suffix:
        rel_text = rel_text[:-len(path.suffix)] if path.suffix else rel_text
    if WIKILINK_UNSAFE.search(rel_text):
        # A Markdown link target is a URL: "#" would start a fragment, so
        # every character but the folder separator is percent-encoded.
        target = urllib.parse.quote(rel_text + (path.suffix if drop_suffix else ""), safe="/")
        label = re.sub(r"([\\\[\]])", r"\\\1", unicodedata.normalize("NFC", path.stem))
        return f"[{label}]({target})"
    return f"[[{rel_text}]]"


def source_links(md: Path, vault_root: Path | None, bib: dict | None) -> dict:
    """Links from the note to the Markdown paper and its PDF, for the
    bibliography (`- pdf: [[...]]`, `- mdpaper: [[...]]`, as in Lit notes).
    A link that cannot be made is null, with the reason."""
    links = {"mdpaper": None, "pdf": None, "notes": []}
    if vault_root is None:
        links["notes"].append("no --vault-root: no links to the paper or its PDF")
        return links
    links["mdpaper"] = vault_link(md, vault_root, drop_suffix=True)
    if links["mdpaper"] is None:
        links["notes"].append("the Markdown is outside the vault: no mdpaper link")
    pdfs: list[Path] = []
    if bib and bib.get("source") == "zotero":
        # The Zotero record names the PDF; prefer it over a search by name.
        pdfs = [Path(a["local_path"]) for a in bib.get("attachments", [])
                if a.get("is_pdf") and a.get("local_path") and Path(a["local_path"]).is_file()
                and vault_link(Path(a["local_path"]), vault_root)]
        same = [p for p in pdfs if unicodedata.normalize("NFC", p.stem) == unicodedata.normalize("NFC", md.stem)]
        pdfs = same if len(pdfs) > 1 and same else pdfs
    if not pdfs:
        # pdf-mistral names the Markdown after the PDF.
        pdfs = find_by_name(md.stem + ".pdf", [vault_root])
    if len(pdfs) == 1:
        links["pdf"] = vault_link(pdfs[0], vault_root)
    elif pdfs:
        names = ", ".join(sorted(unicodedata.normalize("NFC", p.relative_to(vault_root).as_posix()) for p in pdfs))
        links["notes"].append(f"several PDFs match, so no pdf link: {names}")
    else:
        links["notes"].append(f"no PDF of the paper in the vault (looked for {md.stem}.pdf): no pdf link")
    return links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--vault-root", type=Path)
    parser.add_argument("--image-root", type=Path, action="append", default=[])
    parser.add_argument("--lang", choices=("ja", "en"), default="ja")
    parser.add_argument("--run-id")
    parser.add_argument("--runs-dir", type=Path,
                        help="where run folders go (default: $AGENTSTACK_HOME/addons/digest-paper/runs)")
    parser.add_argument("--bib", type=Path,
                        help="bibliographic record to keep with the run (e.g. from zotero_lookup.py)")
    args = parser.parse_args(argv)

    md = args.input.expanduser()
    if not md.is_file():
        print(f"prepare_input: not a file: {md}", file=sys.stderr)
        return 2
    vault_root = args.vault_root.expanduser().resolve() if args.vault_root else None
    if vault_root and not vault_root.is_dir():
        print(f"prepare_input: --vault-root is not a folder: {vault_root}", file=sys.stderr)
        return 2
    image_roots = []
    for root in args.image_root:
        root = root.expanduser()
        if not root.is_dir():
            print(f"prepare_input: --image-root is not a folder: {root}", file=sys.stderr)
            return 2
        image_roots.append(root.resolve())

    text = md.read_text(encoding="utf-8")
    lines = text.splitlines()
    source_hash = sha256_file(md)
    title = next((l[2:].strip() for l in lines if l.startswith("# ")), md.stem)
    run_id = args.run_id or f"{slug(title, 40)}-{source_hash[:8]}-{_dt.datetime.now():%Y%m%dT%H%M%S}"
    if not re.fullmatch(r"[^/\\\x00]{1,120}", run_id) or run_id in (".", ".."):
        print("prepare_input: --run-id must be a plain folder name", file=sys.stderr)
        return 2

    if args.bib and not args.bib.expanduser().is_file():
        print(f"prepare_input: --bib is not a file: {args.bib}", file=sys.stderr)
        return 2
    output_dir = args.output_dir.expanduser()
    runs_dir = (args.runs_dir.expanduser() if args.runs_dir else default_runs_dir()).resolve()
    run = runs_dir / run_id
    if run.exists():
        print(f"prepare_input: run folder already exists, not overwriting: {run}", file=sys.stderr)
        return 2
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"prepare_input: cannot create the output folder {output_dir}: {exc}", file=sys.stderr)
        return 2
    (run / "source" / "images").mkdir(parents=True)
    (run / "draft" / "assets").mkdir(parents=True)
    (run / "draft" / "evidence").mkdir(parents=True)
    (run / "review").mkdir(parents=True)
    (run / "tasks").mkdir(parents=True)
    shutil.copy2(md, run / "source" / "paper.md")
    bib = None
    if args.bib:
        bib = json.loads(args.bib.expanduser().read_text(encoding="utf-8"))
        (run / "bib.json").write_text(json.dumps(bib, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    refs = []
    for number, line in enumerate(lines):
        for kind, target, source in image_links(line):
            refs.append({"kind": kind, "target": target, "text": source, "line": number + 1})

    images, seen = [], {}
    for ref in refs:
        entry = resolve(ref, md.parent.resolve(), vault_root, image_roots)
        entry["caption_nearby"] = nearby_caption(lines, ref["line"] - 1)
        if entry["status"] == "resolved":
            real = Path(entry["resolved"]).resolve()
            if real in seen:
                entry["asset_id"] = seen[real]
            else:
                asset_id = f"a{len(seen) + 1:03d}"
                seen[real] = asset_id
                copy = run / "source" / "images" / f"{asset_id}{real.suffix.lower()}"
                shutil.copy2(real, copy)
                size = image_size(copy)
                entry.update(asset_id=asset_id, snapshot=str(copy.relative_to(run)),
                             sha256=sha256_file(copy), bytes=copy.stat().st_size,
                             width=size[0] if size else None, height=size[1] if size else None)
        images.append(entry)

    summary = {
        "resolved": sum(1 for i in images if i["status"] == "resolved"),
        "unresolved": sum(1 for i in images if i["status"] == "unresolved"),
        "remote": sum(1 for i in images if i["status"] == "remote"),
        "unique_images": len(seen),
    }
    record = {
        "schema": 1,
        "run_id": run_id,
        "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "language": args.lang,
        "title_guess": title,
        "source": {"path": str(md.resolve()), "sha256": source_hash, "lines": len(lines)},
        "vault_root": str(vault_root) if vault_root else None,
        "image_roots": [str(r) for r in image_roots],
        "output_dir": str(output_dir.resolve()),
        "summary": summary,
        "bib": {k: bib.get(k) for k in ("source", "citekey", "item_key", "library_id", "doi")} if bib else None,
        "links": source_links(md, vault_root, bib),
        "images": images,
    }
    (run / "input.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"run: {run}")
    print(f"title: {title}")
    for kind in ("pdf", "mdpaper"):
        if record["links"][kind]:
            print(f"{kind}: {record['links'][kind]}")
    for note in record["links"]["notes"]:
        print(f"links: {note}")
    print("images: {resolved} resolved ({unique_images} unique), {unresolved} unresolved, {remote} remote".format(**summary))
    for entry in images:
        if entry["status"] != "resolved":
            print(f"  line {entry['line']}: {entry['status']}: {entry.get('reason', '')}: {entry['text'][:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

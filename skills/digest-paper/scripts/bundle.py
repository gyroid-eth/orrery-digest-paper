#!/usr/bin/env python3
"""Build, check and publish the note bundle of one digest-paper run.

    bundle.py adopt   RUN ASSET_ID [ASSET_ID ...]   copy figures into draft/assets/
    bundle.py hash    RUN                           print the draft digest
    bundle.py check   RUN                           check draft/ (exit 1 on problems)
    bundle.py publish RUN [--revision]              copy draft/ to the output folder

RUN is the folder prepare_input.py printed
($AGENTSTACK_HOME/addons/digest-paper/runs/<run-id> by default). publish writes
into the output folder recorded in RUN/input.json.

The draft digest covers draft/note.md (without its `review_status:` line, which
the writer sets after the review), draft/evidence/figures.json and every file in
draft/assets/. The reviewer names the digest it reviewed; publish
refuses a note marked `review_status: checked` unless the latest review
approved exactly the current digest, so an old review is never reused for a
changed draft.

publish never overwrites: an existing bundle stops it unless --revision is
given, which writes <name>-r2, -r3, ... It writes into a staging folder first
and renames it into place, so a half-written bundle never has the final name.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import shutil
import sys
import unicodedata
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mdlinks import image_links  # noqa: E402

STATUSES = ("checked", "needs-review", "blocked")
REQUIRED_KEYS = ("title", "language", "review_status", "source_check", "writer", "reviewer", "run_id")
# A link target may contain balanced parentheses: the pdf-mistral plugin writes
# file URIs with Node's pathToFileURL, which leaves "(" and ")" unencoded, so
# "figure (control).png" must not end the link at its first ")".


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


STATUS_LINE = re.compile(rb"^review_status:[^\n]*\n", re.MULTILINE)


def reviewed_note_bytes(data: bytes) -> bytes:
    """The note as the reviewer judges it: the review_status line in the front
    matter is the outcome of the review, not part of what was reviewed, so
    setting it after approval does not change the digest."""
    if not data.startswith(b"---\n"):
        return data
    end = data.find(b"\n---", 4)
    if end < 0:
        return data
    return data[:4] + STATUS_LINE.sub(b"", data[4:end + 1], count=1) + data[end + 1:]


def draft_digest(run: Path) -> str:
    draft = run / "draft"
    parts = []
    for rel in ["note.md", "evidence/figures.json"] + sorted(
            f"assets/{p.name}" for p in (draft / "assets").glob("*") if p.is_file()):
        path = draft / rel
        data = path.read_bytes() if path.is_file() else b""
        if rel == "note.md":
            data = reviewed_note_bytes(data)
        parts.append(f"{rel}\0{sha256_bytes(data)}\n")
    return sha256_bytes("".join(parts).encode())


def front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    fields = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith((" ", "-", "#")):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def load_run(run: Path) -> dict:
    record = run / "input.json"
    if not record.is_file():
        raise SystemExit(f"bundle: not a run folder (no input.json): {run}")
    return json.loads(record.read_text(encoding="utf-8"))


def cmd_adopt(run: Path, asset_ids: list[str]) -> int:
    record = load_run(run)
    by_id = {}
    for entry in record["images"]:
        # A repeated reference to the same image shares the first one's asset
        # ID but has no snapshot of its own; keep the first.
        if entry.get("status") == "resolved" and entry.get("snapshot"):
            by_id.setdefault(entry["asset_id"], entry)
    status = 0
    for asset_id in asset_ids:
        entry = by_id.get(asset_id)
        if entry is None:
            print(f"bundle: unknown asset {asset_id}", file=sys.stderr)
            status = 1
            continue
        source = run / entry["snapshot"]
        target = run / "draft" / "assets" / source.name
        shutil.copy2(source, target)
        print(f"assets/{source.name}")
    return status


def check(run: Path) -> list[str]:
    problems = []
    draft = run / "draft"
    note = draft / "note.md"
    if not note.is_file():
        return ["draft/note.md is missing"]
    text = note.read_text(encoding="utf-8")
    fields = front_matter(text)
    for key in REQUIRED_KEYS:
        if not fields.get(key):
            problems.append(f"front matter: `{key}` is missing")
    if fields.get("review_status") and fields["review_status"] not in STATUSES:
        problems.append(f"front matter: review_status must be one of {', '.join(STATUSES)}")
    linked = set()
    for kind, target, source in (link for line in text.splitlines() for link in image_links(line)):
        if kind == "wiki":
            problems.append("use relative Markdown image links (![...](assets/...)), not ![[...]] embeds")
            continue
        if kind == "broken":
            problems.append(f"not a complete image link: {source}")
            continue
        target = urllib.parse.unquote(target)
        if re.match(r"^[a-z]+:", target) or target.startswith("/"):
            problems.append(f"image link must be relative to the bundle: {target}")
            continue
        path = (draft / target).resolve()
        # Only files directly in assets/: that is what adopt creates and what
        # the digest covers, so a figure cannot change after approval unseen.
        if path.parent != (draft / "assets").resolve():
            problems.append(f"image link must point to a file directly in assets/: {target}")
            continue
        if not path.is_file():
            problems.append(f"image link target is missing: {target}")
        linked.add(path.name)
    for sub in sorted(p.name for p in (draft / "assets").iterdir() if not p.is_file()):
        problems.append(f"assets/{sub} is a folder; keep figures directly in assets/ (bundle.py adopt)")
    assets = {p.name for p in (draft / "assets").glob("*") if p.is_file()}
    for name in sorted(assets - linked):
        problems.append(f"assets/{name} is not used in note.md (remove it or link it)")
    bib_path = run / "bib.json"
    if bib_path.is_file():
        bib = json.loads(bib_path.read_text(encoding="utf-8"))
        if bib.get("source") == "zotero":
            # The note must name exactly the Zotero item the run was made from.
            expected = [("citekey", bib.get("citekey")), ("zotero_item", bib.get("item_key")),
                        ("zotero_library", bib.get("library")), ("zotero_link", bib.get("zotero_link"))]
            if bib.get("doi"):
                expected.append(("doi", bib["doi"]))
            for key, value in expected:
                if fields.get(key) != (value or ""):
                    problems.append(f"front matter: `{key}` must be {value!r} (from the run's Zotero record)")
    figures = draft / "evidence" / "figures.json"
    if not figures.is_file():
        problems.append("draft/evidence/figures.json is missing")
    else:
        try:
            data = json.loads(figures.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else data.get("figures", [])
            mapped = set()
            for row in rows:
                name = row.get("file") or ""
                if name and Path(name).name not in assets:
                    problems.append(f"figures.json names {name}, which is not in assets/")
                mapped.add(Path(name).name)
            for name in sorted(assets - mapped):
                problems.append(f"assets/{name} has no row in figures.json")
        except (json.JSONDecodeError, AttributeError) as exc:
            problems.append(f"figures.json is not valid: {exc}")
    return problems


def latest_review(run: Path) -> tuple[Path | None, dict[str, str]]:
    reviews = sorted((run / "review").glob("review-*.md"),
                     key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not reviews:
        return None, {}
    text = reviews[-1].read_text(encoding="utf-8")
    fields = front_matter(text)
    return reviews[-1], fields


def slug(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKC", text).strip()
    text = re.sub(r"[\\/:*?\"<>|#^\[\]\x00-\x1f]", " ", text)
    text = re.sub(r"\s+", "-", text).strip("-.")
    return text[:limit].rstrip("-.") or "paper"


WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


def safe_citekey(citekey: str) -> str:
    """A citekey usable as a file name on Windows, macOS and Linux."""
    text = unicodedata.normalize("NFC", citekey)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).rstrip(" .")
    if not text or text.upper().split(".")[0] in WINDOWS_RESERVED:
        text = f"_{text}"
    return text[:120]


def cmd_publish(run: Path, revision: bool, layout: str = "auto") -> int:
    record = load_run(run)
    problems = check(run)
    if problems:
        print("bundle: draft has problems; not publishing:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    note_text = (run / "draft" / "note.md").read_text(encoding="utf-8")
    fields = front_matter(note_text)
    digest = draft_digest(run)
    review_path, review = latest_review(run)
    status = fields["review_status"]
    if status == "checked":
        if review_path is None:
            print("bundle: review_status is checked but there is no review", file=sys.stderr)
            return 1
        if review.get("verdict") != "approved" or review.get("draft_digest") != digest:
            print("bundle: review_status is checked, but the latest review "
                  f"({review_path.name}: verdict={review.get('verdict')}, "
                  f"digest={review.get('draft_digest', '')[:12]}) did not approve the "
                  f"current draft ({digest[:12]}); ask the reviewer to confirm this draft "
                  "or mark the note needs-review", file=sys.stderr)
            return 1

    output_dir = Path(record["output_dir"])
    note_name = "note.md"
    if layout == "auto":
        # The run decides: a run made from a Zotero record is a Lit note, so
        # the shared writer's plain `publish RUN` does the right thing.
        bib_path = run / "bib.json"
        is_zotero = bib_path.is_file() and json.loads(bib_path.read_text(encoding="utf-8")).get("source") == "zotero"
        layout = "lit" if is_zotero else "bundle"
    if layout == "lit":
        bib_path = run / "bib.json"
        if not bib_path.is_file():
            print("bundle: --layout lit needs a run made with --bib (a Zotero record)", file=sys.stderr)
            return 1
        bib = json.loads(bib_path.read_text(encoding="utf-8"))
        cite = safe_citekey(bib["citekey"])
        base = f"{cite}_{bib['item_key']}"
        note_name = f"={cite}=.md"
    else:
        base = f"{slug(fields.get('title') or record['title_guess'])}-{record['source']['sha256'][:8]}"
    name, n = base, 1
    while (output_dir / name).exists():
        if not revision:
            print(f"bundle: {output_dir / name} exists; not overwriting (use --revision for a new one)",
                  file=sys.stderr)
            return 1
        n += 1
        name = f"{base}-r{n}"
    staging = output_dir / f".staging-{name}-{_dt.datetime.now():%H%M%S}"
    shutil.copytree(run / "draft", staging)
    evidence = staging / "evidence"
    evidence.mkdir(exist_ok=True)
    shutil.copy2(run / "input.json", evidence / "input.json")
    if review_path is not None:
        # The latest review decides the status; every round is kept, since the
        # substantive findings are usually in the earlier ones.
        shutil.copy2(review_path, evidence / "review.md")
        (evidence / "reviews").mkdir(exist_ok=True)
        for path in sorted((run / "review").glob("review-*.md")):
            shutil.copy2(path, evidence / "reviews" / path.name)
    result = {
        "schema": 1,
        "run_id": record["run_id"],
        "published": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "review_status": status,
        "source_check": fields.get("source_check"),
        "draft_digest": digest,
        "reviewed_digest": review.get("draft_digest") if review else None,
        "review_verdict": review.get("verdict") if review else None,
        "note_sha256": sha256_bytes((run / "draft" / "note.md").read_bytes()),
        "source_sha256": record["source"]["sha256"],
    }
    if (run / "bib.json").is_file():
        shutil.copy2(run / "bib.json", evidence / "bib.json")
    if note_name != "note.md":
        # Renaming does not touch the content, so the approved digest still
        # describes exactly what is published.
        (staging / "note.md").rename(staging / note_name)
    result["note_file"] = note_name
    (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    staging.rename(output_dir / name)
    print(output_dir / name / note_name)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("adopt"); p.add_argument("run", type=Path); p.add_argument("assets", nargs="+")
    p = sub.add_parser("hash"); p.add_argument("run", type=Path)
    p = sub.add_parser("check"); p.add_argument("run", type=Path)
    p = sub.add_parser("publish"); p.add_argument("run", type=Path); p.add_argument("--revision", action="store_true")
    p.add_argument("--layout", choices=("auto", "bundle", "lit"), default="auto",
                   help="auto (default): lit for runs made from a Zotero record, bundle otherwise; "
                        "lit: <citekey>_<itemKey>/=<citekey>=.md")
    args = parser.parse_args(argv)
    run = args.run.expanduser()
    if args.command == "adopt":
        return cmd_adopt(run, args.assets)
    if args.command == "hash":
        load_run(run)
        print(draft_digest(run))
        return 0
    if args.command == "check":
        load_run(run)
        problems = check(run)
        for problem in problems:
            print(f"- {problem}")
        print("ok" if not problems else f"{len(problems)} problem(s)")
        return 0 if not problems else 1
    return cmd_publish(run, args.revision, args.layout)


if __name__ == "__main__":
    sys.exit(main())

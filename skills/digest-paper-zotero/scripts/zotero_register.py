#!/usr/bin/env python3
"""Register one paper in the user's Zotero by DOI, only when asked to.

    zotero_register.py --doi DOI --ops-dir DIR [--pdf FILE] [--transport ...]

Run this only when the user asked for a new paper to be added. It:

  1. normalises the DOI and takes a lock for it (one run per DOI at a time);
  2. looks the DOI up in the personal library first (Local API; a 403 or a
     failed page is an error, never "not registered"); an existing item is
     reported and nothing is written;
  3. reads the save target Zotero will use (/connector/getSelectedCollection)
     and stops without writing unless it is the personal library ("My
     Library"); the connector saves wherever the Zotero window points;
  4. builds the item from Crossref's record for the DOI and saves it with the
     Zotero Connector API (/connector/saveItems);
  5. confirms the new item through the Local API (DOI, key) before anything
     else;
  6. attaches a PDF: the file given with --pdf, else an open-access PDF link
     that Crossref lists for the DOI (and Unpaywall, if UNPAYWALL_EMAIL is set).
     A download counts only if it starts with %PDF; a login page never does.
     No other source is used. Without a PDF it stops and asks for one;
  7. prints where the PDF is (the Windows path on a Windows Zotero), for the
     user to convert it with the pdf-mistral plugin.

Every step is written to DIR/<doi>/op.json before and after it runs, with the
connector session, the Zotero item key once confirmed and the PDF hash. A rerun
reads it: a confirmed item is never created again, and an unanswered save is
looked up before anything is retried. Nothing is ever deleted.

Exit status: 0 done (item exists and has a PDF), 3 stopped (reason printed),
2 usage.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zotero_lookup as zl  # noqa: E402

CONNECTOR_PATHS = {"/connector/getSelectedCollection", "/connector/saveItems", "/connector/saveAttachment"}
PERSONAL_LIBRARY_ID = 1   # Zotero's local libraryID of "My Library"
MAX_PDF_BYTES = 200 * 1024 * 1024
USER_AGENT = "orrery-digest-paper (digest-paper-zotero; +https://github.com/gyroid-eth/orrery-digest-paper)"
CROSSREF = os.environ.get("DIGEST_CROSSREF_URL", "https://api.crossref.org")
TYPE_MAP = {"journal-article": "journalArticle", "proceedings-article": "conferencePaper",
            "book-chapter": "bookSection", "book": "book", "posted-content": "preprint",
            "report": "report", "dissertation": "thesis"}


class Stop(Exception):
    pass


# --------------------------------------------------------------------------- #
# connector transport (write path; separate from the read allowlist)
# --------------------------------------------------------------------------- #
def connector(z: zl.Zotero, path: str, *, json_body=None, pdf: Path | None = None,
              metadata: dict | None = None) -> tuple[int, str]:
    if path not in CONNECTOR_PATHS:
        raise ValueError(f"connector path not allowed: {path}")
    headers = {"X-Zotero-Connector-API-Version": "3"}
    if metadata is not None:
        headers["X-Metadata"] = json.dumps(metadata, ensure_ascii=True)
    if z.transport == "local-http":
        if pdf is not None:
            data, headers["Content-Type"] = pdf.read_bytes(), "application/pdf"
        else:
            data, headers["Content-Type"] = json.dumps(json_body or {}).encode(), "application/json"
        request = urllib.request.Request(f"http://127.0.0.1:{zl.PORT}{path}", data=data, headers=headers)
        try:
            with zl._OPENER.open(request, timeout=60) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise zl.Unavailable(f"no answer from Zotero ({exc})") from exc
    # Windows helper: the PDF goes by file path, never in the command line.
    req = {"op": "connector", "path": path, "metadata": headers.get("X-Metadata")}
    if pdf is not None:
        win = subprocess.run(["wslpath", "-w", str(pdf)], capture_output=True, text=True, check=True).stdout.strip()
        req["file"] = win
    else:
        req["body_b64"] = base64.b64encode(json.dumps(json_body or {}).encode()).decode()
    return zl.windows_helper_raw(req, 90)


# --------------------------------------------------------------------------- #
# operation record
# --------------------------------------------------------------------------- #
class Op:
    def __init__(self, directory: Path):
        self.dir = directory
        self.path = directory / "op.json"
        self.data = json.loads(self.path.read_text()) if self.path.is_file() else {"schema": 1, "steps": []}

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def step(self, name: str, **info):
        self.data["steps"].append({"step": name, "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                                   **info})
        self.save()


def doi_folder(doi: str) -> str:
    """A folder name that only this DOI can have: a readable prefix plus a hash
    of the whole DOI ("10.1234/a/b" and "10.1234/a_b" must never share one)."""
    readable = re.sub(r"[^A-Za-z0-9._-]", "_", doi)[:60]
    return f"{readable}-{hashlib.sha256(doi.encode()).hexdigest()[:16]}"


# --------------------------------------------------------------------------- #
# pieces
# --------------------------------------------------------------------------- #
def find_personal(z: zl.Zotero, doi: str) -> list[dict]:
    return zl.find_by_doi(z, doi)


def save_target(z: zl.Zotero) -> dict:
    status, body = connector(z, "/connector/getSelectedCollection", json_body={})
    if status != 200:
        raise zl.Unavailable(f"could not read Zotero's save target (HTTP {status})")
    return json.loads(body)


def http_get(url: str, accept: str, limit: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise Stop(f"download larger than {limit} bytes: {url}")
        return data, response.geturl()


def crossref(doi: str) -> dict:
    data, _ = http_get(f"{CROSSREF}/works/{urllib.parse.quote(doi, safe='/')}", "application/json", 5_000_000)
    return json.loads(data)["message"]


def connector_item(meta: dict, doi: str, item_id: str) -> dict:
    parts = ((meta.get("issued") or meta.get("published") or {}).get("date-parts") or [[]])[0]
    item = {
        "itemType": TYPE_MAP.get(meta.get("type"), "journalArticle"),
        "title": (meta.get("title") or [""])[0],
        "creators": [{"creatorType": "author", "firstName": a.get("given", ""), "lastName": a.get("family", "")}
                     if a.get("family") else {"creatorType": "author", "name": a.get("name", ""), "fieldMode": 1}
                     for a in meta.get("author") or []],
        "publicationTitle": (meta.get("container-title") or [""])[0],
        "volume": meta.get("volume", ""), "issue": meta.get("issue", ""), "pages": meta.get("page", ""),
        "date": "-".join(str(p) for p in parts),
        "DOI": doi, "url": f"https://doi.org/{doi}",
        "id": item_id,
    }
    return {k: v for k, v in item.items() if v not in ("", [])}


def pdf_candidates(meta: dict, doi: str) -> list[str]:
    urls = [l["URL"] for l in meta.get("link") or []
            if l.get("content-type") == "application/pdf" and l.get("URL")]
    email = os.environ.get("UNPAYWALL_EMAIL", "").strip()
    if email:
        try:
            data, _ = http_get(f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi, safe='/')}"
                               f"?email={urllib.parse.quote(email)}", "application/json", 2_000_000)
            for loc in json.loads(data).get("oa_locations") or []:
                if loc.get("url_for_pdf"):
                    urls.append(loc["url_for_pdf"])
        except (OSError, ValueError, urllib.error.URLError):
            pass
    seen, out = set(), []
    for url in urls:
        if url not in seen and url.startswith("https://"):
            seen.add(url)
            out.append(url)
    return out


def fetch_pdf(urls: list[str], directory: Path, op: Op) -> tuple[Path, str] | None:
    for url in urls:
        try:
            data, final = http_get(url, "application/pdf", MAX_PDF_BYTES)
        except (OSError, urllib.error.URLError, Stop) as exc:
            op.step("pdf-download-failed", url=url, error=str(exc)[:200])
            continue
        if not data.startswith(b"%PDF"):
            op.step("pdf-rejected", url=url, final_url=final, reason="not a PDF (no %PDF header)")
            continue
        path = directory / "download.pdf"
        path.write_bytes(data)
        op.step("pdf-downloaded", url=url, final_url=final, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        return path, url
    return None


def confirm_item(z: zl.Zotero, doi: str, tries: int = 10) -> dict | None:
    for _ in range(tries):
        hits = find_personal(z, doi)
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise Stop(f"{len(hits)} items with DOI {doi} after saving; left as they are, check Zotero")
        time.sleep(1)
    return None


# --------------------------------------------------------------------------- #
# main flow
# --------------------------------------------------------------------------- #
def register(z: zl.Zotero, doi: str, ops_dir: Path, user_pdf: Path | None) -> dict:
    directory = ops_dir / doi_folder(doi)
    directory.mkdir(parents=True, exist_ok=True)
    lock = open(directory / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise Stop(f"another run is registering {doi}") from exc
    legacy = ops_dir / re.sub(r"[^A-Za-z0-9._-]", "_", doi)[:120] / "op.json"
    if legacy.is_file() and not (directory / "op.json").is_file():
        # A record from the earlier folder scheme: do not start afresh beside it.
        raise Stop(f"an older record for this DOI exists at {legacy}; check the item in Zotero, then move "
                   "that record aside and run again. Nothing was done.")
    op = Op(directory)
    if op.data.get("schema", 1) != 1 or op.data.get("doi", doi) != doi:
        raise Stop(f"{op.path} is not a record for DOI {doi} (schema {op.data.get('schema')}, "
                   f"DOI {op.data.get('doi')}); nothing was done")
    recorded_target = next((s for s in op.data["steps"] if s["step"] == "save-target"), None)
    if recorded_target and recorded_target.get("library_id") != PERSONAL_LIBRARY_ID:
        raise Stop(f"{op.path} records a save into library {recorded_target.get('library')}; nothing was done")
    op.data.setdefault("doi", doi)
    op.data.setdefault("transport", z.transport)

    status, _ = z.get("/connector/ping")
    if status != 200:
        raise zl.Unavailable("Zotero is not running")

    item = None
    if op.data.get("item_key"):
        item = confirm_item(z, doi, tries=3)
        if item is not None and item["key"] != op.data["item_key"]:
            # The DOI now points at a different item: the recorded session and
            # its parent belong to the old one; never use them for this one.
            raise Stop(f"the record says item {op.data['item_key']} but DOI {doi} now finds {item['key']}; "
                       "check Zotero. Nothing was done.")
    else:
        existing = find_personal(z, doi)          # raises on 403 / a failed page
        op.step("lookup-before", matches=[i["key"] for i in existing])
        if len(existing) > 1:
            raise Stop(f"several items already have DOI {doi}: {', '.join(i['key'] for i in existing)}")
        if existing:
            item = existing[0]
            op.data["item_key"], op.data["created"] = item["key"], False
            op.save()
        elif op.data.get("save_items_sent"):
            # An earlier run sent saveItems but never confirmed it: look again,
            # never send it twice.
            item = confirm_item(z, doi)
            if item is None:
                raise Stop("an earlier saveItems was sent but no item with this DOI exists; "
                           "check Zotero before trying again (op.json has the details)")
        else:
            target = save_target(z)
            op.step("save-target", library_id=target.get("libraryID"), library=target.get("libraryName"),
                    collection=target.get("name") if target.get("id") else None,
                    editable=target.get("libraryEditable", target.get("editable")))
            if target.get("libraryID") != PERSONAL_LIBRARY_ID:
                raise Stop(f"Zotero is set to save into '{target.get('libraryName')}', not the personal library. "
                           "In Zotero, select My Library (or a collection in it) and run this again. Nothing was written.")
            if target.get("libraryEditable") is False or target.get("editable") is False:
                raise Stop("Zotero reports the selected library as read-only; nothing was written")
            meta = crossref(doi)
            session = f"orrery-{doi_folder(doi)}-{int(time.time())}"
            body = {"sessionID": session, "uri": f"https://doi.org/{doi}",
                    "items": [connector_item(meta, doi, "item1")]}
            op.data.update(session=session, save_items_sent=True, crossref_title=body["items"][0].get("title"),
                           body_sha256=hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest())
            op.step("save-items-sent")
            try:
                status, text = connector(z, "/connector/saveItems", json_body=body)
            except zl.Unavailable as exc:
                status, text = None, str(exc)
            op.step("save-items-answer", status=status, body=text[:200])
            # Whatever the answer (or none), look the DOI up before deciding:
            # the save may have happened even if the answer was lost.
            item = confirm_item(z, doi)
            if item is None and status not in (200, 201, 409):
                raise Stop(f"Zotero did not save the item (answer: {status or 'none'}: {text[:200]}); "
                           "nothing with this DOI exists. Check Zotero, then run again.")
            if item is None:
                raise Stop("saveItems answered but the item does not show up yet; run again to re-check "
                           "(it will not be sent twice)")
            op.data["created"] = True
        if item is not None:
            op.data["item_key"] = item["key"]
            op.step("item-confirmed", item_key=item["key"])

    if item is None:
        raise Stop("the recorded item is no longer found by DOI; check Zotero (nothing was changed)")
    key = item["key"]
    pdfs = [a for a in zl.attachments_of(z, "", key) if a["is_pdf"]]
    if not pdfs:
        if not op.data.get("created"):
            raise Stop(f"{doi} is already in Zotero ({key}) without a PDF. Attach the PDF in Zotero yourself; "
                       "this tool only attaches to an item it has just created.")
        if user_pdf is not None:
            data = user_pdf.read_bytes()
            if not data.startswith(b"%PDF"):
                raise Stop(f"{user_pdf} is not a PDF")
            pdf, source = user_pdf, "user"
            op.step("pdf-from-user", path=str(user_pdf), sha256=hashlib.sha256(data).hexdigest())
        else:
            got = fetch_pdf(pdf_candidates(crossref(doi), doi), directory, op)
            if got is None:
                raise Stop(f"no open-access PDF was found for {doi}. Give the PDF you have with --pdf FILE "
                           "(the item is registered; it will not be created again).")
            pdf, source = got
        if op.data.get("attachment_sent"):
            # Zotero does not de-duplicate attachments: an earlier send whose
            # answer was lost is looked for, never repeated.
            raise Stop(f"a PDF was sent to item {key} earlier but does not show up. Check the item in Zotero "
                       "and attach the PDF yourself if it is missing; it will not be sent again.")
        op.data.update(attachment_sent=True,
                       attachment_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())
        op.step("save-attachment-sent", session=op.data.get("session"))
        try:
            status, text = connector(z, "/connector/saveAttachment", pdf=pdf,
                                     metadata={"id": "att1", "parentItemID": "item1", "title": "Full Text PDF",
                                               "url": source if source.startswith("https://") else f"https://doi.org/{doi}",
                                               "sessionID": op.data.get("session", "")})
        except zl.Unavailable as exc:
            status, text = None, str(exc)
        op.step("save-attachment-answer", status=status, body=text[:200])
        for _ in range(10):
            pdfs = [a for a in zl.attachments_of(z, "", key) if a["is_pdf"]]
            if pdfs:
                break
            time.sleep(1)
        if not pdfs:
            if status not in (200, 201):
                raise Stop(f"Zotero did not attach the PDF (answer: {status or 'none'}); the item {key} exists. "
                           "Attach the PDF in Zotero yourself; it will not be sent again.")
            raise Stop("the PDF was sent but does not show up under the item yet; check Zotero "
                       "(a rerun will look again, not send it again)")
    op.step("done", pdf=pdfs[0].get("native_path"))
    return {"doi": doi, "item_key": key, "created": bool(op.data.get("created")),
            "pdf_native_path": pdfs[0].get("native_path"), "pdf_local_path": pdfs[0].get("local_path"),
            "op_record": str(op.path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--doi", required=True)
    parser.add_argument("--ops-dir", required=True, type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--transport", choices=("auto", "local-http", "windows-helper"), default="auto")
    args = parser.parse_args(argv)
    doi = zl.normalize_doi(args.doi)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        print(f"zotero_register: not a DOI: {args.doi}", file=sys.stderr)
        return 2
    if args.pdf is not None and not args.pdf.is_file():
        print(f"zotero_register: --pdf is not a file: {args.pdf}", file=sys.stderr)
        return 2
    try:
        result = register(zl.Zotero(args.transport), doi, args.ops_dir.expanduser(), args.pdf)
    except (Stop, zl.Unavailable, LookupError) as exc:
        print(f"zotero_register: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    where = result["pdf_native_path"] or result["pdf_local_path"]
    print(f"PDF in Zotero: {where}\nConvert it with the pdf-mistral plugin in Obsidian, then make the Lit note.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

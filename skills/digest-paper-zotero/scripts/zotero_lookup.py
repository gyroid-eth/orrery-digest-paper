#!/usr/bin/env python3
"""Look up one paper in the user's Zotero, read-only, for digest-paper-zotero.

    zotero_lookup.py --citekey KEY  [--out bib.json] [--attachment N]
    zotero_lookup.py --doi 10.xxxx/... [--out bib.json] [--attachment N]
    zotero_lookup.py --doctor

Talks only to the Zotero running on this computer (or, from WSL, on the
Windows side): the standard Local API (/api/...) and Better BibTeX's JSON-RPC
(/better-bibtex/json-rpc). It never writes to Zotero, never registers a paper,
never downloads a PDF and never reads zotero.sqlite.

Transport (--transport auto by default):
  local-http       http://127.0.0.1:23119 directly (macOS, Linux, WSL mirrored)
  windows-helper   from WSL: a fixed PowerShell script run through WSL interop
                   that calls the Windows localhost:23119 with an allowlisted
                   request and returns JSON (WSL NAT networking, where the
                   Windows localhost is not reachable from Linux)
auto tries local-http, and on WSL falls back to windows-helper.

Writes a JSON record (--out, or stdout) with the item identity (library,
itemKey, citekey), bibliographic fields, the zotero://select link, the PDF
attachments (the path as Zotero reports it, and the local path this machine
can read), which transport answered, and the location of the shared
digest-paper skill. Exit status: 0 found, 3 not found / ambiguous / Zotero
unavailable (the reason is printed), 2 usage.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Tests point this at a fake server; the Windows helper always uses 23119.
PORT = int(os.environ.get("DIGEST_ZOTERO_PORT", "23119"))
# item.search is not used: Better BibTeX 9.0.64 fails it under Zotero 10
# ("Invalid condition 'blockStart' in hasOperator()", Windows test machine, 2026-09-29).
BBT_METHODS = {"api.ready", "item.citationkey", "item.attachments"}
# Personal library only in this version. Every GET the tool can make:
API_PATH = re.compile(
    r"^/api/(?:"
    r"|users/0/items/[A-Z0-9]{8}"
    r"|users/0/items/[A-Z0-9]{8}/children"
    r"|users/0/items/top\?q=[A-Za-z0-9%._~-]{1,300}&qmode=everything&limit=25"
    r"|users/0/items/top\?limit=100&start=\d{1,6}"
    r")$")
MAX_SCAN = 5000  # items scanned to find a citekey that search did not find
GET_PATHS = {"/connector/ping"}


class Unavailable(Exception):
    pass


def on_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# transports: every request passes the same allowlist
# --------------------------------------------------------------------------- #
def check_request(req: dict) -> None:
    if req["op"] == "get" and (req["path"] in GET_PATHS or API_PATH.match(req["path"])):
        return
    if req["op"] == "rpc" and req.get("method") in BBT_METHODS:
        return
    raise ValueError(f"request not allowed: {req}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is returned as-is, never followed: nothing but the fixed
    localhost paths is ever requested."""
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def local_http(req: dict, timeout: float) -> tuple[int, str]:
    check_request(req)
    url = f"http://127.0.0.1:{PORT}"
    if req["op"] == "get":
        request = urllib.request.Request(url + req["path"])
    else:
        body = json.dumps({"jsonrpc": "2.0", "method": req["method"], "params": req.get("params", []), "id": 1})
        request = urllib.request.Request(url + "/better-bibtex/json-rpc", data=body.encode(),
                                         headers={"Content-Type": "application/json"})
    request.add_header("Zotero-API-Version", "3")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise Unavailable(f"no answer on 127.0.0.1:{PORT} ({exc})") from exc


def windows_helper(req: dict, timeout: float) -> tuple[int, str]:
    check_request(req)
    return windows_helper_raw(req, timeout)


def windows_helper_raw(req: dict, timeout: float) -> tuple[int, str]:
    """Run the helper with a request the caller has already checked. The
    helper checks it again against its own list."""
    script = (HERE / "zotero_helper.ps1").read_text(encoding="utf-8")
    payload = base64.b64encode(json.dumps(req).encode()).decode()
    # The request travels as base64 in a string literal; the script checks it
    # against its own allowlist again before calling localhost.
    command = f"$RequestB64 = '{payload}'\n" + script
    encoded = base64.b64encode(command.encode("utf-16-le")).decode()
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                             capture_output=True, timeout=timeout + 20, check=False)
    except FileNotFoundError as exc:
        raise Unavailable("powershell.exe is not reachable (WSL interop disabled?)") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unavailable("the Windows helper timed out") from exc
    text = out.stdout.decode("utf-8", "replace").strip().lstrip("﻿")
    try:
        answer = json.loads(text.splitlines()[-1]) if text else {}
    except json.JSONDecodeError as exc:
        raise Unavailable(f"the Windows helper returned no JSON: {text[:200]}") from exc
    if answer.get("error"):
        raise Unavailable(f"Windows helper: {answer['error']}")
    return int(answer.get("status", 0)), answer.get("body", "")


class Zotero:
    def __init__(self, transport: str, timeout: float = 8.0, record: dict | None = None):
        self.timeout = timeout
        self.record = record if record is not None else {}
        if transport == "auto":
            try:
                local_http({"op": "get", "path": "/connector/ping"}, 2.0)
                transport = "local-http"
            except Unavailable:
                transport = "windows-helper" if on_wsl() else "local-http"
        self.transport = transport
        self.call = local_http if transport == "local-http" else windows_helper

    def get(self, path: str) -> tuple[int, str]:
        return self.call({"op": "get", "path": path}, self.timeout)

    def rpc(self, method: str, params: list):
        status, body = self.call({"op": "rpc", "method": method, "params": params}, self.timeout)
        if status == 404:
            raise Unavailable("Better BibTeX JSON-RPC not found (is Better BibTeX installed?)")
        if status != 200:
            raise Unavailable(f"Better BibTeX {method}: HTTP {status}")
        data = json.loads(body)
        if "error" in data:
            raise Unavailable(f"Better BibTeX {method}: {data['error'].get('message', data['error'])}")
        return data.get("result")


# --------------------------------------------------------------------------- #
# lookup
# --------------------------------------------------------------------------- #
def normalize_doi(doi: str) -> str:
    doi = doi.strip()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    return doi.lower()


def creators_of(data: dict) -> list[str]:
    names = []
    for person in data.get("creators") or []:
        if person.get("creatorType", "author") != "author":
            continue
        if person.get("name"):
            names.append(person["name"])
        else:
            names.append(" ".join(p for p in (person.get("firstName"), person.get("lastName")) if p))
    return names


def extra_citekey(data: dict) -> str:
    """A citekey stored by Zotero itself (citationKey field, or the
    'Citation Key:' line of Extra)."""
    if data.get("citationKey"):
        return str(data["citationKey"])
    match = re.search(r"^Citation Key:\s*(\S+)\s*$", data.get("extra") or "", re.M)
    return match.group(1) if match else ""


def to_local_path(native: str) -> tuple[str, str | None]:
    """(path this machine can read, problem or None)."""
    if re.match(r"^[A-Za-z]:[\\/]", native):
        if not on_wsl():
            return "", "Windows path on a non-WSL machine"
        try:
            local = subprocess.run(["wslpath", "-u", native], capture_output=True, text=True,
                                   check=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "", "wslpath could not convert the path"
        return local, None if Path(local).is_file() else "file not readable from WSL"
    if native.startswith("\\\\"):
        return "", "UNC path; not read automatically"
    return native, None if Path(native).is_file() else "file not found"


def api_json(z: Zotero, path: str):
    status, body = z.get(path)
    if status == 403:
        raise Unavailable("Zotero's Local API is off; turn on Settings > Advanced > "
                          "'Allow other applications on this computer to communicate with Zotero'")
    if status != 200:
        raise Unavailable(f"Zotero Local API {path.split('?')[0]}: HTTP {status}")
    return json.loads(body)


def citekeys_for(z: Zotero, keys: list[str]) -> dict[str, str]:
    """itemKey -> citekey from Better BibTeX (personal library, libraryID 1)."""
    found = {}
    for start in range(0, len(keys), 100):
        chunk = keys[start:start + 100]
        answer = z.rpc("item.citationkey", [[f"1:{k}" for k in chunk]]) or {}
        if isinstance(answer, dict):
            for ident, cite in answer.items():
                key = str(ident).split(":")[-1]
                if cite:
                    found[key] = str(cite)
    return found


SEARCH_LIMIT = 25


def search(z: Zotero, term: str) -> tuple[list[dict], bool]:
    """Quick-search candidates, and whether they are ALL the matches: only a
    raw answer shorter than the limit is complete (notes and attachments are
    dropped after counting)."""
    q = urllib.parse.quote(term, safe="")
    raw = api_json(z, f"/api/users/0/items/top?q={q}&qmode=everything&limit={SEARCH_LIMIT}")
    items = [i for i in raw if i.get("data", {}).get("itemType") not in ("attachment", "note")]
    return items, len(raw) < SEARCH_LIMIT


def find_by_doi(z: Zotero, doi: str) -> list[dict]:
    """Every item of the personal library whose DOI equals `doi`."""
    def same(page):
        return [i for i in page if normalize_doi(i.get("data", {}).get("DOI", "")) == doi]
    found, complete = search(z, doi)
    return same(found) if complete else scan(z, same)


def find_by_citekey(z: Zotero, citekey: str) -> list[dict]:
    """Every item whose Better BibTeX citekey equals `citekey`. The quick
    search never sees citekeys, so the whole library is always scanned; a
    match among search candidates alone would not prove it is the only one."""
    def matching(page):
        cites = citekeys_for(z, [i["key"] for i in page])
        return [i for i in page if cites.get(i["key"]) == citekey]
    return scan(z, matching)


def scan(z: Zotero, matching) -> list[dict]:
    """Every top-level item of the personal library, page by page. Raises
    rather than returning a partial result as 'not found'."""
    hits = []
    for start in range(0, MAX_SCAN + 1, 100):
        raw = api_json(z, f"/api/users/0/items/top?limit=100&start={start}")
        page = [i for i in raw if i.get("data", {}).get("itemType") not in ("attachment", "note")]
        hits += matching(page) if page else []
        if len(raw) < 100:
            return hits
    raise Unavailable(f"the library has more than {MAX_SCAN} items; give the DOI or a more specific citekey")


def attachments_of(z: Zotero, citekey: str, key: str) -> list[dict]:
    """PDF attachments with the path Zotero reports, and a local readable path."""
    out = []
    try:
        answer = z.rpc("item.attachments", [citekey]) or []
        for att in answer:
            if att.get("path"):
                out.append({"native_path": att["path"], "open": att.get("open"), "via": "better-bibtex"})
    except Unavailable:
        answer = None
    if not out:
        for child in api_json(z, f"/api/users/0/items/{key}/children"):
            data = child.get("data", {})
            if data.get("itemType") != "attachment":
                continue
            href = ((child.get("links") or {}).get("enclosure") or {}).get("href", "")
            native = ""
            if href.startswith("file:"):
                native = urllib.parse.unquote(urllib.parse.urlparse(href).path)
                if re.match(r"^/[A-Za-z]:/", native):
                    native = native[1:]
            elif data.get("linkMode") == "linked_file" and data.get("path"):
                native = data["path"]
            out.append({"native_path": native, "attachment_key": child.get("key"),
                        "content_type": data.get("contentType"), "filename": data.get("filename"),
                        "via": "local-api"})
    for att in out:
        native = att["native_path"]
        if native:
            att["local_path"], att["problem"] = to_local_path(native)
        else:
            att["local_path"], att["problem"] = "", "Zotero did not report a file path"
        att["is_pdf"] = (native or att.get("filename") or "").lower().endswith(".pdf") \
            or att.get("content_type") == "application/pdf"
    return out


def lookup(z: Zotero, citekey: str | None, doi: str | None) -> dict:
    status, _ = z.get("/connector/ping")
    if status != 200:
        raise Unavailable("Zotero is not running (connector/ping did not answer)")
    if not z.rpc("api.ready", []):
        raise Unavailable("Better BibTeX is not ready yet; try again in a moment")
    if citekey:
        matches = find_by_citekey(z, citekey)
        what = f"citekey {citekey}"
    else:
        wanted = normalize_doi(doi or "")
        matches = find_by_doi(z, wanted)
        what = f"DOI {wanted}"
    if not matches:
        raise LookupError(f"no Zotero item with {what} in the personal library"
                          " (group libraries are not supported in this version;"
                          " this skill does not register papers; add it in Zotero first)")
    if len(matches) > 1:
        listed = "; ".join(f"{i['key']} ({i.get('data', {}).get('title', '?')[:60]})" for i in matches)
        raise LookupError(f"several Zotero items match {what}: {listed}; give the citekey")
    item = matches[0]
    key, data = item["key"], item.get("data", {})
    cite = citekey or citekeys_for(z, [key]).get(key, "")
    if not cite:
        raise LookupError("Better BibTeX gave no citekey for the item")
    return {
        "schema": 1,
        "source": "zotero",
        "transport": z.transport,
        "citekey": cite,
        "item_key": key,
        "library_id": 1,
        "library": "personal",
        "zotero_link": f"zotero://select/library/items/{key}",
        "title": data.get("title"),
        "authors": creators_of(data),
        "year": (re.search(r"\d{4}", data.get("date") or "") or [""])[0],
        "doi": data.get("DOI") or "",
        "container": data.get("publicationTitle") or "",
        "item_type": data.get("itemType"),
        "attachments": attachments_of(z, cite, key),
        "digest_paper_skill": str((HERE.parent.parent / "digest-paper").resolve()),
    }


def doctor(z: Zotero) -> int:
    print(f"transport: {z.transport}")
    problems = 0
    try:
        status, body = z.get("/connector/ping")
    except Unavailable as exc:
        print(f"warn: Zotero not reachable: {exc}")
        return 1
    print(("ok: Zotero is running" if status == 200 else f"warn: connector/ping answered {status}"))
    status, _ = z.get("/api/")
    if status == 200:
        print("ok: Zotero Local API is enabled")
    elif status == 403:
        print("warn: Zotero Local API is off; turn on Settings > Advanced > 'Allow other "
              "applications on this computer to communicate with Zotero' (needed to find papers)")
        problems = 1
    else:
        print(f"warn: Local API answered {status}")
    try:
        ready = z.rpc("api.ready", [])
        print("ok: Better BibTeX JSON-RPC ready" if ready else "warn: Better BibTeX not ready yet")
        problems += 0 if ready else 1
    except Unavailable as exc:
        print(f"warn: {exc}")
        return 1
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--citekey")
    group.add_argument("--doi")
    group.add_argument("--doctor", action="store_true")
    parser.add_argument("--transport", choices=("auto", "local-http", "windows-helper"), default="auto")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    z = Zotero(args.transport)
    if args.doctor:
        return doctor(z)
    try:
        record = lookup(z, args.citekey, args.doi)
    except (Unavailable, LookupError) as exc:
        print(f"zotero_lookup: {exc}", file=sys.stderr)
        return 3
    text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(args.out)
    else:
        sys.stdout.write(text)
    pdfs = [a for a in record["attachments"] if a["is_pdf"]]
    print(f"{record['citekey']} ({record['item_key']}): {record['title']}", file=sys.stderr)
    for index, att in enumerate(pdfs):
        print(f"  pdf[{index}] {att['native_path']}" + (f"  ({att['problem']})" if att["problem"] else ""),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

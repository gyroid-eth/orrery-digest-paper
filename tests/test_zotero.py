"""digest-paper-zotero against a fake Zotero (no real Zotero is contacted)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import ROOT, SCRIPTS, png, runs_of

LOOKUP = ROOT / "skills" / "digest-paper-zotero" / "scripts" / "zotero_lookup.py"

ITEM = {
    "key": "ABCD2345", "library": {"type": "user", "id": 0},
    "data": {"key": "ABCD2345", "itemType": "journalArticle",
             "title": "Bioinspired 3D structures with programmable morphologies",
             "creators": [{"creatorType": "author", "firstName": "Amirali", "lastName": "Nojoomi"},
                          {"creatorType": "author", "firstName": "Kyungsuk", "lastName": "Yum"},
                          {"creatorType": "editor", "firstName": "Ed", "lastName": "Itor"}],
             "date": "2018-09-12", "DOI": "10.1038/s41467-018-05569-8",
             "publicationTitle": "Nature Communications"},
}
CITEKEYS = {"ABCD2345": "nojoomi2018bioinspired"}


def other(key, title, doi=""):
    return {"key": key, "library": {"type": "user", "id": 0},
            "data": {"key": key, "itemType": "journalArticle", "title": title, "DOI": doi, "creators": []}}


CROSSREF_NEW = {"type": "journal-article", "title": ["A new paper"], "DOI": "10.9999/new.1",
                "author": [{"given": "Ada", "family": "Lovelace"}], "container-title": ["J. Tests"],
                "issued": {"date-parts": [[2024, 1, 2]]}}


class FakeZotero(BaseHTTPRequestHandler):
    """Speaks the Local API and Better BibTeX JSON-RPC the way Zotero 10.0.3
    with Better BibTeX 9.0.64 did on the Windows test machine: item.search fails."""
    items: list = [ITEM]
    citekeys: dict = CITEKEYS
    api_status = 200
    bbt = True
    bbt_attachments = True
    attachments: list = []
    children: list = []
    seen: list = []
    selected = {"libraryID": 1, "libraryName": "My Library", "libraryEditable": True, "editable": True}
    crossref_links: list = []
    created_children: dict = {}
    save_items_lost = False
    pdf_dir = None

    def connector(self, raw):
        type(self).seen.append(("CONNECTOR", self.path))
        if self.path == "/connector/getSelectedCollection":
            return self.reply(200, self.selected)
        if self.path == "/connector/saveItems":
            body = json.loads(raw)
            item = body["items"][0]
            key = f"NEW{len(self.items):05d}"
            type(self).items = self.items + [{"key": key, "library": {"type": "user", "id": 0},
                                              "data": {k: v for k, v in item.items() if k != "id"} | {"key": key}}]
            type(self).last_session = (body["sessionID"], item["id"], key)
            if self.save_items_lost:
                self.close_connection = True
                return self.reply(500, "connection lost")
            return self.reply(201, "")
        if self.path == "/connector/saveAttachment":
            meta = json.loads(self.headers["X-Metadata"])
            session, item_id, key = self.last_session
            if meta["sessionID"] != session or meta["parentItemID"] != item_id:
                return self.reply(400, "unknown parent")
            stored = self.pdf_dir / f"{key}.pdf"
            stored.write_bytes(raw)
            type(self).created_children = {**self.created_children, key: [
                {"key": "ATT" + key[3:], "data": {"itemType": "attachment", "linkMode": "imported_url",
                                                  "contentType": "application/pdf", "filename": "x.pdf"},
                 "links": {"enclosure": {"href": stored.as_uri()}}}]}
            return self.reply(201, "")
        return self.reply(404, "no")

    def log_message(self, *args):
        pass

    def reply(self, status, body, headers=None):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        type(self).seen.append(("GET", self.path))
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path == "/connector/ping":
            return self.reply(200, "Zotero is running")
        if path.startswith("/api/") and self.api_status != 200:
            return self.reply(self.api_status, "Forbidden")
        if path == "/api/":
            return self.reply(200, "Nothing to see here.")
        if path == "/api/users/0/items/top":
            if "q" in params:
                q = urllib.parse.unquote(params["q"]).lower()
                # Zotero's quick search matches title/creator/year fields
                # (and DOI in "everything" mode), never Better BibTeX citekeys.
                hits = [i for i in self.items
                        if q in " ".join((i["data"].get("title", ""), i["data"].get("DOI", ""),
                                          i["data"].get("extra", ""))).lower()]
                return self.reply(200, hits[:int(params.get("limit", 25))])
            start = int(params.get("start", 0))
            return self.reply(200, self.items[start:start + int(params.get("limit", 100))])
        if path.startswith("/api/users/0/items/") and path.endswith("/children"):
            key = path.split("/")[5]
            return self.reply(200, self.children if key == "ABCD2345" else self.created_children.get(key, []))
        if path == "/works/10.9999/new.1":
            return self.reply(200, {"message": CROSSREF_NEW | {"link": self.crossref_links}})
        if path == "/pdf/ok.pdf":
            return self.reply(200, "%PDF-1.4 open access copy")
        if path == "/pdf/login.pdf":
            return self.reply(200, "<html>Please log in</html>")
        if path == "/api/users/0/items/REDIR234":
            return self.reply(302, "", {"Location": "http://example.org/steal"})
        return self.reply(404, "not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        if self.path.startswith("/connector/"):
            return self.connector(raw)
        req = json.loads(raw)
        type(self).seen.append(("POST", req["method"]))
        if not self.bbt:
            return self.reply(404, "no such endpoint")
        method, params = req["method"], req["params"]
        if method == "api.ready":
            result = {"zotero": "10.0.3", "betterbibtex": "9.0.64"}
        elif method == "item.search":
            return self.reply(200, {"jsonrpc": "2.0", "id": 1, "error": {
                "code": -32000, "message": "ZoteroInvalidDataError: Invalid condition 'blockStart' in hasOperator()"}})
        elif method == "item.citationkey":
            result = {k: self.citekeys[k.split(":")[1]] for k in params[0] if k.split(":")[1] in self.citekeys}
        elif method == "item.attachments":
            if not self.bbt_attachments or not params[0]:
                return self.reply(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "broken"}})
            result = self.attachments
        else:
            return self.reply(200, {"jsonrpc": "2.0", "error": {"code": -32601, "message": "no method"}, "id": 1})
        return self.reply(200, {"jsonrpc": "2.0", "result": result, "id": 1})


@pytest.fixture
def zotero(tmp_path):
    FakeZotero.items = [ITEM]
    FakeZotero.citekeys = dict(CITEKEYS)
    FakeZotero.api_status = 200
    FakeZotero.bbt = True
    FakeZotero.bbt_attachments = True
    FakeZotero.children = []
    FakeZotero.selected = {"libraryID": 1, "libraryName": "My Library", "libraryEditable": True, "editable": True}
    FakeZotero.crossref_links = []
    FakeZotero.created_children = {}
    FakeZotero.save_items_lost = False
    FakeZotero.pdf_dir = tmp_path
    pdf = tmp_path / "zotero-storage" / "Nojoomi 2018 (poly(NIPAm)).pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4 fake")
    FakeZotero.attachments = [{"path": str(pdf), "open": "zotero://open-pdf/library/items/PDFK2345"}]
    FakeZotero.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeZotero)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"port": server.server_address[1], "pdf": pdf}
    server.shutdown()


def lookup(zotero, *args):
    env = {**os.environ, "DIGEST_ZOTERO_PORT": str(zotero["port"])}
    return subprocess.run([sys.executable, str(LOOKUP), "--transport", "local-http", *args],
                          env=env, capture_output=True, text=True, check=False)


def test_lookup_by_citekey_returns_the_item_identity_and_readable_pdf(zotero, tmp_path):
    out = tmp_path / "bib.json"
    result = lookup(zotero, "--citekey", "nojoomi2018bioinspired", "--out", out)
    assert result.returncode == 0, result.stderr
    bib = json.loads(out.read_text())
    assert (bib["citekey"], bib["item_key"], bib["library_id"]) == ("nojoomi2018bioinspired", "ABCD2345", 1)
    assert bib["zotero_link"] == "zotero://select/library/items/ABCD2345"
    assert bib["title"].startswith("Bioinspired 3D structures with programmable")
    assert bib["authors"] == ["Amirali Nojoomi", "Kyungsuk Yum"] and bib["year"] == "2018"
    assert bib["attachments"][0]["local_path"] == str(zotero["pdf"]) and bib["attachments"][0]["problem"] is None
    assert Path(bib["digest_paper_skill"]).name == "digest-paper"
    # Read-only: only GETs and the four lookup methods were used.
    posts = {m for kind, m in FakeZotero.seen if kind == "POST"}
    assert posts <= {"api.ready", "item.citationkey", "item.attachments"}  # never item.search


def test_lookup_by_doi_normalises_the_prefix(zotero):
    result = lookup(zotero, "--doi", "https://doi.org/10.1038/S41467-018-05569-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["item_key"] == "ABCD2345"


def test_several_matches_are_listed_not_chosen(zotero):
    FakeZotero.items = [ITEM, other("ZZZZ2345", "A preprint of the same", ITEM["data"]["DOI"])]
    result = lookup(zotero, "--doi", ITEM["data"]["DOI"])
    assert result.returncode == 3
    assert "several Zotero items match" in result.stderr and "ZZZZ2345" in result.stderr


def test_a_citekey_search_cannot_find_is_found_by_scanning_the_library(zotero):
    """Quick search never matches citekeys; the library is scanned in pages
    and Better BibTeX gives the citekeys."""
    FakeZotero.items = [other(f"K{n:07d}", f"Paper {n}") for n in range(150)] + [ITEM]
    result = lookup(zotero, "--citekey", "nojoomi2018bioinspired")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["item_key"] == "ABCD2345"
    assert ("GET", "/api/users/0/items/top?limit=100&start=100") in FakeZotero.seen


def test_not_found_is_not_registered(zotero):
    result = lookup(zotero, "--citekey", "absent2020")
    assert result.returncode == 3 and "does not register papers" in result.stderr


def test_local_api_off_stops_with_the_setting_to_change(zotero):
    FakeZotero.api_status = 403
    result = lookup(zotero, "--citekey", "nojoomi2018bioinspired")
    assert result.returncode == 3 and "Local API is off" in result.stderr
    doctor = lookup(zotero, "--doctor")
    assert "warn: Zotero Local API is off" in doctor.stdout and doctor.returncode == 1


def test_attachments_fall_back_to_the_local_api_children(zotero, tmp_path):
    FakeZotero.bbt_attachments = False
    FakeZotero.children = [
        {"key": "PDFK2345", "data": {"itemType": "attachment", "linkMode": "imported_file",
                                     "contentType": "application/pdf", "filename": "Nojoomi.pdf"},
         "links": {"enclosure": {"href": zotero["pdf"].as_uri()}}},
        {"key": "NOTE2345", "data": {"itemType": "note"}},
    ]
    result = lookup(zotero, "--citekey", "nojoomi2018bioinspired")
    assert result.returncode == 0, result.stderr
    [att] = json.loads(result.stdout)["attachments"]
    assert att["via"] == "local-api" and att["is_pdf"] and att["local_path"] == str(zotero["pdf"])


def test_missing_better_bibtex_is_named(zotero):
    FakeZotero.bbt = False
    result = lookup(zotero, "--citekey", "x")
    assert result.returncode == 3 and "Better BibTeX" in result.stderr
    assert "warn: Better BibTeX JSON-RPC not found" in lookup(zotero, "--doctor").stdout


def test_redirects_are_not_followed_and_requests_are_allowlisted(zotero):
    sys.path.insert(0, str(LOOKUP.parent))
    import importlib
    os.environ["DIGEST_ZOTERO_PORT"] = str(zotero["port"])
    try:
        mod = importlib.import_module("zotero_lookup")
        mod = importlib.reload(mod)
        status, _body = mod.local_http({"op": "get", "path": "/api/users/0/items/REDIR234"}, 5)
        assert status == 302  # returned as-is, example.org never contacted
        for bad in ({"op": "get", "path": "/api/users/0/items/ABCD2345/file"},
                    {"op": "get", "path": "http://example.org/"},
                    {"op": "rpc", "method": "item.pandoc"},
                    {"op": "post", "path": "/api/users/0/items"}):
            with pytest.raises(ValueError):
                mod.local_http(bad, 5)
    finally:
        del os.environ["DIGEST_ZOTERO_PORT"]


def test_the_windows_helper_keeps_the_same_allowlist():
    import re
    sys.path.insert(0, str(LOOKUP.parent))
    import zotero_lookup
    ps1 = (LOOKUP.parent / "zotero_helper.ps1").read_text()
    # No proxy (PowerShell 5.1 sent loopback through the system proxy), no redirects.
    assert "$web.Proxy = $null" in ps1 and "$web.AllowAutoRedirect = $false" in ps1
    code = [line for line in ps1.splitlines() if not line.lstrip().startswith("#")]
    assert not any("Invoke-WebRequest" in line for line in code)
    assert "http://127.0.0.1:23119" in ps1
    methods = re.search(r"\$allowedMethods = @\(([^)]*)\)", ps1).group(1)
    assert {m.strip(" '") for m in methods.split(",")} == zotero_lookup.BBT_METHODS
    ps_api = re.compile(re.search(r"\$apiPath = '([^']*)'", ps1).group(1))
    samples = [
        "/api/", "/api/users/0/items/ABCD2345", "/api/users/0/items/ABCD2345/children",
        "/api/users/0/items/top?q=10.1038%2Fncomms11582&qmode=everything&limit=25",
        "/api/users/0/items/top?limit=100&start=200",
        "/api/users/0/items/ABCD2345/file", "/api/users/0/items", "/api/groups/1/items/ABCD2345",
        "/api/users/0/items/top?q=x&qmode=everything&limit=25&format=bibtex",
        "/api/users/0/items/top?q=a b&qmode=everything&limit=25", "/api/users/1/items/ABCD2345",
        "/api/users/0/items/abcd2345",
    ]
    for path in samples:
        assert bool(ps_api.match(path)) == bool(zotero_lookup.API_PATH.match(path)), path
    assert not zotero_lookup.API_PATH.match("/api/users/0/items/ABCD2345/file")

def test_lit_publish_names_the_note_after_the_citekey(zotero, tmp_path):
    bib_path = tmp_path / "bib.json"
    assert lookup(zotero, "--citekey", "nojoomi2018bioinspired", "--out", bib_path).returncode == 0
    md = tmp_path / "Nojoomi 2018.md"
    img = png(tmp_path / "img.png")
    md.write_text(f"# Bioinspired\n\n![]({img.name})\n", encoding="utf-8")
    lit = tmp_path / "Lit"
    sh = lambda *a: subprocess.run([sys.executable, *map(str, a)], capture_output=True, text=True, check=False)
    assert sh(SCRIPTS / "prepare_input.py", "--input", md, "--output-dir", lit, "--bib", bib_path,
              "--run-id", "z1").returncode == 0
    run = runs_of(tmp_path) / "z1"
    assert json.loads((run / "input.json").read_text())["bib"]["citekey"] == "nojoomi2018bioinspired"
    sh(SCRIPTS / "bundle.py", "adopt", run, "a001")
    (run / "draft" / "evidence" / "figures.json").write_text(json.dumps([{"file": "assets/a001.png"}]))
    front = ("---\ntitle: Bioinspired\nlanguage: ja\nreview_status: needs-review\nsource_check: ocr-and-images\n"
             "writer: W\nwriter_program: claude\nreviewer: R\nreviewer_program: codex\n"
             "review_pairing: cross-vendor\nrun_id: z1\ncitekey: {cite}\nzotero_item: ABCD2345\n"
             "zotero_library: personal\ndoi: 10.1038/s41467-018-05569-8\n"
             "zotero_link: zotero://select/library/items/ABCD2345\n---\n\n- 確かめの組 / Review pairing: cross-vendor — 書き手（Claude）と別の会社のモデル（Codex）が確かめた。\n\n![Fig](assets/a001.png)\n")
    (run / "draft" / "note.md").write_text(front.format(cite="someoneelse2020"))
    bad = sh(SCRIPTS / "bundle.py", "check", run)
    assert bad.returncode == 1 and "`citekey` must be 'nojoomi2018bioinspired'" in bad.stdout
    (run / "draft" / "note.md").write_text(front.format(cite="nojoomi2018bioinspired"))
    assert sh(SCRIPTS / "bundle.py", "check", run).returncode == 0
    # The plain publish the shared writer runs picks the Lit layout itself.
    published = sh(SCRIPTS / "bundle.py", "publish", run)
    assert published.returncode == 0, published.stderr
    note = Path(published.stdout.strip())
    assert note == lit / "nojoomi2018bioinspired_ABCD2345" / "=nojoomi2018bioinspired=.md"
    assert note.read_text() == (run / "draft" / "note.md").read_text()
    assert (note.parent / "evidence" / "bib.json").is_file()
    assert json.loads((note.parent / "evidence" / "result.json").read_text())["note_file"] == note.name
    again = sh(SCRIPTS / "bundle.py", "publish", run)
    assert again.returncode == 1 and "not overwriting" in again.stderr
    # VioletBohr 2026-09-30: a note of the same name already in the folder (a
    # Lit note directly in it, in any letter case) stops the publish; the
    # Zotero citekey is never changed to get around it.
    import shutil
    shutil.rmtree(note.parent)
    (lit / "=Nojoomi2018Bioinspired=.md").write_text("an older Lit note\n")
    clash = sh(SCRIPTS / "bundle.py", "publish", run)
    assert clash.returncode == 1 and "already in" in clash.stderr
    assert sorted(p.name for p in lit.iterdir()) == ["=Nojoomi2018Bioinspired=.md"]


def test_install_with_zotero_links_both_skills_and_default_does_not(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "AGENTSTACK_HOME": str(home / ".agentstack"),
           "CODEX_HOME": str(home / ".codex")}
    run = lambda *a: subprocess.run(["/bin/bash", str(ROOT / "scripts" / a[0]), *a[1:]], env=env,
                                    capture_output=True, text=True, check=False)
    assert run("install.sh").returncode == 0
    assert not (home / ".claude" / "skills" / "digest-paper-zotero").exists()
    assert run("install.sh", "--with-zotero").returncode == 0
    for base in (home / ".claude" / "skills", home / ".codex" / "skills"):
        assert (base / "digest-paper-zotero" / "SKILL.md").is_file()
        assert (base / "digest-paper" / "SKILL.md").is_file()
    assert run("uninstall.sh").returncode == 0
    assert not (home / ".claude" / "skills" / "digest-paper-zotero").exists()


def test_a_plain_install_after_with_zotero_leaves_no_dangling_link(tmp_path):
    """PinkMendeleev (05f611e): --with-zotero, then a plain install, then
    uninstall left the two Zotero links pointing at a removed folder."""
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "AGENTSTACK_HOME": str(home / ".agentstack"),
           "CODEX_HOME": str(home / ".codex")}
    run = lambda *a: subprocess.run(["/bin/bash", str(ROOT / "scripts" / a[0]), *a[1:]], env=env,
                                    capture_output=True, text=True, check=False)
    assert run("install.sh", "--with-zotero").returncode == 0
    plain = run("install.sh")
    assert plain.returncode == 0 and "unlinked:" in plain.stdout
    assert run("uninstall.sh").returncode == 0
    for base in (home / ".claude" / "skills", home / ".codex" / "skills"):
        for name in ("digest-paper", "digest-paper-zotero"):
            assert not (base / name).is_symlink() and not (base / name).exists()


def test_a_doi_hidden_behind_a_full_quick_search_is_still_found(zotero):
    """The quick search stops at 25 hits; the DOI is then compared on every item."""
    FakeZotero.items = [other(f"N{n:07d}", f"Paper 10.1038 {n}") for n in range(30)] + [ITEM]
    result = lookup(zotero, "--doi", ITEM["data"]["DOI"])
    assert result.returncode == 0, result.stderr


def test_a_missing_or_wrong_library_and_doi_are_refused_by_check(zotero, tmp_path):
    bib = json.loads(lookup(zotero, "--citekey", "nojoomi2018bioinspired").stdout)
    assert bib["library"] == "personal"


REGISTER = LOOKUP.parent / "zotero_register.py"


def register(zotero, *args):
    env = {**os.environ, "DIGEST_ZOTERO_PORT": str(zotero["port"]),
           "DIGEST_CROSSREF_URL": f"http://127.0.0.1:{zotero['port']}"}
    env.pop("UNPAYWALL_EMAIL", None)
    return subprocess.run([sys.executable, str(REGISTER), "--transport", "local-http", *map(str, args)],
                          env=env, capture_output=True, text=True, check=False)


def connector_calls():
    return [p for kind, p in FakeZotero.seen if kind == "CONNECTOR"]


def test_register_a_new_doi_with_the_users_pdf_and_rerun_creates_nothing(zotero, tmp_path):
    pdf = tmp_path / "mine.pdf"
    pdf.write_bytes(b"%PDF-1.4 my copy")
    first = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", tmp_path / "ops", "--pdf", pdf)
    assert first.returncode == 0, first.stderr
    out = json.loads(first.stdout)
    assert out["created"] is True and out["item_key"].startswith("NEW")
    assert "pdf-mistral" in first.stderr
    assert connector_calls() == ["/connector/getSelectedCollection", "/connector/saveItems",
                                 "/connector/saveAttachment"]
    [op_file] = (tmp_path / "ops").glob("10.9999_new.1-*/op.json")
    op = json.loads(op_file.read_text())
    steps = [s["step"] for s in op["steps"]]
    assert steps[:3] == ["lookup-before", "save-target", "save-items-sent"] and "done" in steps
    assert next(s for s in op["steps"] if s["step"] == "save-target")["library"] == "My Library"
    FakeZotero.seen = []
    again = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", tmp_path / "ops", "--pdf", pdf)
    assert again.returncode == 0, again.stderr
    assert connector_calls() == []                       # nothing written the second time
    assert sum(1 for i in FakeZotero.items if i["data"].get("DOI") == "10.9999/new.1") == 1


def test_an_existing_doi_is_never_created_again(zotero, tmp_path):
    FakeZotero.children = [{"key": "PDFK2345", "data": {"itemType": "attachment", "contentType": "application/pdf"},
                            "links": {"enclosure": {"href": zotero["pdf"].as_uri()}}}]
    FakeZotero.bbt_attachments = False
    result = register(zotero, "--doi", ITEM["data"]["DOI"], "--ops-dir", tmp_path / "ops")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["created"] is False
    assert connector_calls() == []


def test_a_group_selected_in_zotero_means_nothing_is_written(zotero, tmp_path):
    FakeZotero.selected = {"libraryID": 7, "libraryName": "Lab Group", "libraryEditable": True}
    result = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", tmp_path / "ops")
    assert result.returncode == 3
    assert "select My Library" in result.stderr and "Nothing was written" in result.stderr
    assert connector_calls() == ["/connector/getSelectedCollection"]


def test_a_lost_save_answer_is_looked_up_not_resent(zotero, tmp_path):
    FakeZotero.save_items_lost = True
    FakeZotero.crossref_links = [{"URL": f"https://127.0.0.1:{zotero['port']}/unreachable.pdf",
                                  "content-type": "application/pdf"}]
    first = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", tmp_path / "ops")
    # The item was created although the answer was lost: it is confirmed by
    # lookup, and without an OA PDF the run stops asking for one.
    assert first.returncode == 3 and "Give the PDF you have with --pdf" in first.stderr
    FakeZotero.seen = []
    FakeZotero.save_items_lost = False
    pdf = tmp_path / "mine.pdf"
    pdf.write_bytes(b"%PDF-1.4 mine")
    second = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", tmp_path / "ops", "--pdf", pdf)
    assert "/connector/saveItems" not in connector_calls()
    assert sum(1 for i in FakeZotero.items if i["data"].get("DOI") == "10.9999/new.1") == 1


def test_an_html_login_page_is_not_taken_as_the_pdf(zotero, tmp_path, monkeypatch):
    base = f"http://127.0.0.1:{zotero['port']}"
    sys.path.insert(0, str(LOOKUP.parent))
    import zotero_register as zr
    op = zr.Op(tmp_path)
    got = zr.fetch_pdf([f"{base}/pdf/login.pdf", f"{base}/pdf/ok.pdf"], tmp_path, op)
    assert got is not None and got[1].endswith("/pdf/ok.pdf")
    rejected = [s for s in op.data["steps"] if s["step"] == "pdf-rejected"]
    assert rejected and rejected[0]["reason"].startswith("not a PDF")
    # Only https links from Crossref are ever tried.
    assert zr.pdf_candidates({"link": [{"URL": "http://x/a.pdf", "content-type": "application/pdf"},
                                       {"URL": "https://x/b.pdf", "content-type": "application/pdf"},
                                       {"URL": "https://x/c.html", "content-type": "text/html"}]},
                             "10.1/x") == ["https://x/b.pdf"]


def test_no_pdf_found_stops_and_asks_for_one(zotero, tmp_path):
    result = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", tmp_path / "ops")
    assert result.returncode == 3 and "no open-access PDF was found" in result.stderr


def test_the_write_path_is_limited_to_three_connector_calls():
    sys.path.insert(0, str(LOOKUP.parent))
    import zotero_register as zr
    assert zr.CONNECTOR_PATHS == {"/connector/getSelectedCollection", "/connector/saveItems",
                                  "/connector/saveAttachment"}
    ps1 = (LOOKUP.parent / "zotero_helper.ps1").read_text()
    assert "@('/connector/getSelectedCollection', '/connector/saveItems', '/connector/saveAttachment')" in ps1
    assert "[System.IO.File]::ReadAllBytes([string]$req.file)" in ps1
    # The read-only lookup cannot send connector requests.
    import zotero_lookup
    with pytest.raises(ValueError):
        zotero_lookup.check_request({"op": "connector", "path": "/connector/saveItems"})


def note(key):
    return {"key": key, "library": {"type": "user", "id": 0}, "data": {"key": key, "itemType": "note", "title": ""}}


def test_a_doi_match_in_a_full_first_page_is_not_taken_as_unique(zotero):
    """PinkMendeleev (9c7a88c) case 1: one match among the first 25 and another
    beyond them; the lookup must see both."""
    doi = ITEM["data"]["DOI"]
    filler = [other(f"F{n:07d}", f"Mentions {doi} {n}") for n in range(24)]
    FakeZotero.items = [ITEM] + filler + [other("SECOND23", "Second copy", doi)]
    result = lookup(zotero, "--doi", doi)
    assert result.returncode == 3 and "several Zotero items match" in result.stderr


def test_a_citekey_found_by_search_is_still_checked_across_the_library(zotero):
    """Case 2: a search candidate matches, and another item outside the search
    has the same citekey."""
    # The first item is a search candidate (its citekey is in Extra); the
    # second has the same citekey but nothing the quick search can see.
    FakeZotero.items = [ITEM | {"data": ITEM["data"] | {"extra": "Citation Key: nojoomi2018bioinspired"}},
                        other("TWIN2345", "Unrelated title")]
    FakeZotero.citekeys = {"ABCD2345": "nojoomi2018bioinspired", "TWIN2345": "nojoomi2018bioinspired"}
    result = lookup(zotero, "--citekey", "nojoomi2018bioinspired")
    assert result.returncode == 3 and "TWIN2345" in result.stderr


def test_notes_filling_the_search_page_do_not_hide_the_next_page(zotero):
    """Case 3: 25 notes fill the quick search; the DOI on the next page must be
    found, or a new registration would duplicate it."""
    doi = ITEM["data"]["DOI"]
    FakeZotero.items = [note(f"NOTE{n:04d}") | {"data": {"key": f"NOTE{n:04d}", "itemType": "note",
                                                        "title": doi, "DOI": ""}} for n in range(25)] + [ITEM]
    result = lookup(zotero, "--doi", doi)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["item_key"] == "ABCD2345"


def test_similar_dois_never_share_an_operation_record(tmp_path):
    """PinkMendeleev (9648871): 10.1234/a/b and 10.1234/a_b shared a folder."""
    sys.path.insert(0, str(LOOKUP.parent))
    import zotero_register as zr
    assert zr.doi_folder("10.1234/a/b") != zr.doi_folder("10.1234/a_b")
    long_a, long_b = "10.1234/" + "x" * 200 + "a", "10.1234/" + "x" * 200 + "b"
    assert zr.doi_folder(long_a) != zr.doi_folder(long_b)
    # A record written for one DOI is refused for another.
    folder = tmp_path / "ops" / zr.doi_folder("10.9999/new.1")
    folder.mkdir(parents=True)
    (folder / "op.json").write_text(json.dumps({"schema": 1, "doi": "10.9999/other", "steps": []}))
    orig = zr.doi_folder
    try:
        zr.doi_folder = lambda doi: folder.name
        with pytest.raises(zr.Stop, match="is not a record for DOI 10.9999/new.1 .*DOI 10.9999/other"):
            zr.register(None, "10.9999/new.1", tmp_path / "ops", None)
    finally:
        zr.doi_folder = orig


def test_a_pdf_send_whose_answer_was_lost_is_never_repeated(zotero, tmp_path):
    """Zotero does not de-duplicate saveAttachment: after a send with no
    visible result, a rerun looks, and never sends again."""
    pdf = tmp_path / "mine.pdf"
    pdf.write_bytes(b"%PDF-1.4 mine")
    ops = tmp_path / "ops"
    # First run: the item is created, then the attachment "is lost" (Zotero
    # answers 500 and nothing appears).
    real = FakeZotero.connector

    def lossy(self, raw):
        if self.path == "/connector/saveAttachment":
            type(self).seen.append(("CONNECTOR", self.path))
            return self.reply(500, "lost")
        return real(self, raw)
    FakeZotero.connector = lossy
    try:
        first = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", ops, "--pdf", pdf)
    finally:
        FakeZotero.connector = real
    assert first.returncode == 3 and "will not be sent again" in first.stderr
    FakeZotero.seen = []
    second = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", ops, "--pdf", pdf)
    assert second.returncode == 3 and "was sent to item" in second.stderr
    assert connector_calls() == []


def test_a_pdf_that_shows_up_late_is_found_by_the_rerun_not_resent(zotero, tmp_path):
    """201 for the attachment, but the PDF appears only later (Zotero busy)."""
    pdf = tmp_path / "mine.pdf"
    pdf.write_bytes(b"%PDF-1.4 mine")
    ops = tmp_path / "ops"
    real = FakeZotero.connector
    hidden = {}

    def slow(self, raw):
        answer = real(self, raw)
        if self.path == "/connector/saveAttachment":
            hidden.update(FakeZotero.created_children)
            FakeZotero.created_children = {}
        return answer
    FakeZotero.connector = slow
    try:
        first = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", ops, "--pdf", pdf)
    finally:
        FakeZotero.connector = real
    assert first.returncode == 3 and "does not show up under the item yet" in first.stderr
    FakeZotero.created_children = hidden          # the PDF appears
    FakeZotero.seen = []
    second = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", ops, "--pdf", pdf)
    assert second.returncode == 0, second.stderr
    assert connector_calls() == []


def test_a_record_whose_item_changed_or_whose_target_was_a_group_is_refused(zotero, tmp_path):
    sys.path.insert(0, str(LOOKUP.parent))
    import zotero_register as zr
    ops = tmp_path / "ops"
    folder = ops / zr.doi_folder(ITEM["data"]["DOI"])
    folder.mkdir(parents=True)
    record = {"schema": 1, "doi": ITEM["data"]["DOI"], "item_key": "OLDKEY12", "steps": []}
    (folder / "op.json").write_text(json.dumps(record))
    result = register(zotero, "--doi", ITEM["data"]["DOI"], "--ops-dir", ops)
    assert result.returncode == 3 and "now finds ABCD2345" in result.stderr
    record = {"schema": 1, "doi": ITEM["data"]["DOI"],
              "steps": [{"step": "save-target", "library_id": 7, "library": "Lab Group"}]}
    (folder / "op.json").write_text(json.dumps(record))
    result = register(zotero, "--doi", ITEM["data"]["DOI"], "--ops-dir", ops)
    assert result.returncode == 3 and "records a save into library Lab Group" in result.stderr
    assert connector_calls() == []


def test_a_record_in_the_old_folder_scheme_is_not_ignored(zotero, tmp_path):
    ops = tmp_path / "ops"
    old = ops / "10.9999_new.1"
    old.mkdir(parents=True)
    (old / "op.json").write_text(json.dumps({"schema": 1, "doi": "10.9999/new.1", "steps": []}))
    result = register(zotero, "--doi", "10.9999/new.1", "--ops-dir", ops)
    assert result.returncode == 3 and "an older record for this DOI exists" in result.stderr
    assert connector_calls() == []

# digest-paper — an ORRERY add-on

[日本語](README.ja.md)

Hand over one paper that the Obsidian **pdf-mistral** plugin has converted to
Markdown, and a small ORRERY agent team makes a reading note with figures:

- **Claude** (writer) reads the text and the figures and drafts the note.
- **Codex** (reviewer) checks the draft against the text and the actual
  figures, and sends its findings to the writer directly over ORRERY Mail.
- The writer fixes them, the reviewer confirms the same draft, and the note is
  saved to the folder you chose, together with a record of what was checked.

This version accepts **pdf-mistral output only** (Markdown plus figure
images). It does not take a PDF directly and never fetches a paper by DOI or
URL. Japanese and English papers; the note language is `ja` (default) or `en`.

## Requirements

- ORRERY (orrery-telemetry) installed, with Claude and Codex children working
  (the shiritori check passes)
- `claude` and `codex` CLIs, signed in
- Python 3.9+
- Pillow (recommended, optional): lets the writer survey all figures on a few
  overview sheets instead of opening every image; pdf-mistral often saves each
  panel as its own image, so a paper can have dozens.
  WSL / Ubuntu: `sudo apt install python3-pil` · macOS: `python3 -m pip install pillow`
- Obsidian with the pdf-mistral plugin, to convert papers

## Install

```bash
git clone https://github.com/gyroid-eth/orrery-digest-paper.git
cd orrery-digest-paper
scripts/install.sh --dry-run   # shows what goes where; writes nothing
scripts/install.sh
scripts/doctor.sh
```

The add-on goes to `~/.agentstack/addons/digest-paper/` and is linked for
Claude (`~/.claude/skills/digest-paper`) and Codex
(`$CODEX_HOME/skills/digest-paper`). **An existing skill of the same name is
never replaced**; in that case point the agent at the SKILL.md path (below).
`scripts/uninstall.sh` removes only the add-on and its own links, never notes
or run folders.

Work in progress (drafts, figures, reviews) lives in a run folder under
`~/.agentstack/addons/digest-paper/runs/`, not in your output folder: ORRERY's
Codex reviewer may write there, but not into a Windows vault under `/mnt/c`.
Only the finished note is saved to the output folder, with the reviews in its
`evidence/`.

## Use

Ask your Claude agent (an ORRERY parent), for example:

> Make a note of this paper with digest-paper.
> Paper: `/path/to/vault/MDPapers/Paper.md`
> Vault: `/path/to/vault`, image folder: `/path/to/MDPapers-images`
> Save to: `/path/to/vault/Notes`

If another skill of the same name exists, add "read
`~/.agentstack/addons/digest-paper/current/skills/digest-paper/SKILL.md` and
follow it".

The agent works in this order:
1. Checks the input and snapshots the figures (originals are never changed).
2. Starts a Codex reviewer through ORRERY.
3. Drafts the note.
4. Fixes the reviewer's findings.
5. Gets the same draft confirmed.
6. Saves `<save-to>/<citekey>-<hash>/=<citekey>=.md` with `assets/` and
   `evidence/` (input record, figure map, every review round in `reviews/`,
   result; `result.json` names the latest review).

The citekey is made from the note's authors, title and year in the form of
Zotero's Better BibTeX default with a capital first letter (first author's
family name, the first three significant words of the title, the year), so
you link to the note as `[[=OnimaruFintolimbTransitionReorganization2016=]]`. Without authors or a year
that part is left out; a title with no Latin letters is used as it is. Another
paper that already has the same key in the save-to folder gets a letter
(`...2016a`), and a `--revision` gets `-r2` in the note name too, so a link never
has two notes to choose from.

The bibliography links to the paper as Lit notes do, `- pdf: [[...]]` and
`- mdpaper: [[...]]`, relative to the vault so they open in Obsidian on any
machine. The PDF is the one with the Markdown's name in the vault (pdf-mistral
names them alike), or the Zotero record's PDF; when none or several match, the
line is left out and `evidence/input.json` says why. Links need the vault root.

`review_status` means:
- `checked`: the reviewer found the note consistent with the text and figures
  on the points it checked.
- `needs-review`: something is still open.
- `blocked`: something could not be checked.

`checked` is not a statement that the paper itself is right.

## Windows (WSL2) setup for participants (draft)

1. **Install Obsidian on Windows**; keep the vault in a Windows folder (e.g.
   `C:\Users\<you>\Documents\MyVault`).
2. **Install the pdf-mistral plugin**
   ([obsidian-pdf-mistral-hires](https://github.com/gyroid-eth/obsidian-pdf-mistral-hires))
   and enter your Mistral API key in its settings. Keep the key there only;
   never paste it into a chat or a file.
3. Put the PDF in the vault and run "Convert PDF to Markdown with images".
4. Find the vault's path as seen from WSL: `wslpath -u 'C:\Users\you\Documents\MyVault'`
   → `/mnt/c/Users/you/Documents/MyVault`.
5. If the plugin stores images outside the vault ("external"), give that
   folder's WSL path too (`file:///C:/...` links in the Markdown are converted
   automatically on WSL).
6. In WSL, ask ORRERY's Claude as in "Use". Saving into a folder inside the
   vault is fine: the work in progress stays on the WSL side
   (`~/.agentstack/addons/digest-paper/runs/`) and only the finished note is
   written to the vault.

## For Zotero users: digest-paper-zotero

`scripts/install.sh --with-zotero` also installs `digest-paper-zotero`. It
makes the same note for a paper that is **already in your Zotero and already
converted by the pdf-mistral plugin**, found by its citekey or DOI, and saves
it as `<Lit folder>/<citekey>_<itemKey>/=<citekey>=.md` with the citekey, the
Zotero item and a `zotero://select` link in its front matter.

- Needs Zotero running with **Better BibTeX**, and Zotero's **Local API turned
  on** (Settings > Advanced > "Allow other applications on this computer to
  communicate with Zotero"). Both are required.
- If you ask it to **add** a paper by DOI, it registers the paper in Zotero
  (only into My Library, never twice) with the PDF you give or an open-access
  PDF linked from Crossref, then tells you where the PDF is so you can convert
  it with pdf-mistral. It never changes or deletes existing items, never uses
  other download sources and never runs OCR.
- WSL with Zotero on Windows works without setup: a small read-only helper runs
  on the Windows side through WSL interop and talks to Zotero's localhost.
- `scripts/doctor.sh` reports separately whether Zotero is running, whether its
  Local API is on, and whether Better BibTeX answers.

Ask, for example: "Make a Lit note of `nojoomi2018bioinspired` with
digest-paper-zotero. Markdown: `…/MDPapers/Nojoomi 2018.md`. Lit folder: `…/Lit`."

## What leaves your machine

- **OCR**: the pdf-mistral plugin sends the PDF to Mistral (outside this
  add-on).
- **Note writing**: the paper's text and the chosen figures go to the Claude
  and Codex services you have configured.

Having the Markdown locally does not mean nothing is sent. For unpublished or
collaborative material, follow your institution's rules.

## Copyright

This add-on's MIT license does not cover the papers or figures you give it.
Follow the paper's own terms when you share a note or its figures. The add-on
never publishes or commits notes.

## License

MIT ([LICENSE](LICENSE))

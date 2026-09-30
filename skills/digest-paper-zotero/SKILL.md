---
name: digest-paper-zotero
description: For Zotero users. Make a reading note (a Lit note named =citekey=) of one paper in the user's Zotero, converted to Markdown by the Obsidian pdf-mistral plugin. Looks the paper up by citekey or DOI through Zotero's Local API and Better BibTeX, then a Claude writer and a Codex reviewer produce the note exactly as in digest-paper. When the user asks to add a new paper by DOI, registers it in Zotero with an open-access or user-supplied PDF. Never runs OCR.
---

# digest-paper-zotero (ORRERY add-on)

This is `digest-paper` with a Zotero front end. The note, the review and the
evidence are made exactly as in `digest-paper`; this skill adds only:

- finding **one** Zotero item by citekey or DOI (read-only), and
- saving the note as a Lit note: `<lit folder>/<citekey>_<itemKey>/=<citekey>=.md`
  with the citekey, Zotero item and a `zotero://select` link in its front matter.

`SKILL_DIR` is the folder of this file. The lookup prints `digest_paper_skill`,
the real folder of the shared `digest-paper` skill (called `COMMON` below).
Always use that path, never `SKILL_DIR/../digest-paper`: another skill of that
name may be installed next to this one.

## What the user needs

- Zotero running, with **Better BibTeX** installed, and Zotero's **Local API
  turned on** ("Allow other applications on this computer to communicate with
  Zotero" in Settings > Advanced). Both are required: papers are found through
  the Local API and citekeys come from Better BibTeX.
- The paper in Zotero, and converted with the pdf-mistral plugin (Markdown +
  figures). If there is no Markdown, stop and ask the user to convert the PDF
  with the plugin first. Never run OCR yourself.
- The Lit folder where notes go.

On WSL with a Windows Zotero, the lookup reaches Zotero through a small
read-only Windows helper automatically; nothing has to be configured.

## Registering a new paper (only when the user asks for it)

Only if the user asked to **add** a paper that is not in Zotero yet, by DOI:

`python3 SKILL_DIR/scripts/zotero_register.py --doi DOI --ops-dir LIT/.zotero-ops [--pdf FILE]`

- It checks the personal library first and never creates a second copy; an
  existing item is reported and left alone.
- It writes only if Zotero is set to save into **My Library**. Otherwise it
  stops and asks the user to select My Library in Zotero; tell the user that.
- It attaches the PDF the user gave (`--pdf`), or an open-access PDF linked
  from Crossref. Nothing else: no login, no other download site. Without a PDF
  it stops and asks for one; the item stays registered and is not created
  again on the next run.
- It prints where the PDF is. Ask the user to convert that PDF with the
  pdf-mistral plugin in Obsidian, then continue with the Coordinator steps
  below using the new citekey.
- A lookup that fails (Zotero closed, Local API off) is an error, never "not
  registered": do not register on the strength of a failed lookup.

## Coordinator

1. **Find the paper** (read-only):
   `python3 SKILL_DIR/scripts/zotero_lookup.py --citekey KEY --out /tmp/<citekey>.bib.json`
   or `--doi 10.xxxx/...`. If it reports several items, show them and ask
   which; never pick one yourself. If Zotero or Better BibTeX is unavailable,
   report the exact message and stop.
2. **Find the Markdown.** Use the Markdown the user names. If they did not
   name one, look for the pdf-mistral output whose file name matches the
   PDF attachment's name (the lookup lists the attachments) and **ask the
   user to confirm** it before going on. A matching name alone is not proof.
3. **Prepare** with the shared helper, adding the Zotero record:
   `python3 COMMON/scripts/prepare_input.py --input MD --output-dir LIT --bib /tmp/<citekey>.bib.json [--vault-root V] [--image-root DIR ...] --lang ja`
4. Continue with `COMMON/SKILL.md` from **Coordinator step 3** (start the other
   role, write, review, fix, confirm, publish). Everything there applies
   unchanged, and the other agent reads `COMMON/SKILL.md` too. Put this line
   in its task so a writer knows before the first draft:
   `This is a Zotero run: RUN/bib.json holds the Zotero record; copy its fields into the front matter as COMMON/SKILL.md Writer step 4 says.`
5. The run decides the layout: the writer's usual `bundle.py publish RUN`
   saves `<LIT>/<citekey>_<itemKey>/=<citekey>=.md` because the run carries a
   Zotero record. Nobody publishes a second time. An existing note is never
   overwritten; `--revision` makes a new folder.

## Rules

- Zotero is written to only by `zotero_register.py`, only when the user asked
  to add a paper, and only to create that one item and its PDF. Nothing is
  ever changed, tagged or deleted; `zotero.sqlite` is never opened.
- One run is one Zotero item, from the **personal library**. Group libraries
  are not supported in this version: an item only in a group is reported as
  not found, never treated as a personal item.
- If the citekey changes in Zotero during the run, stop and report it; do not
  rename anything.
- All rules of `COMMON/SKILL.md` ("Rules for both") apply.

# Writing and reviewing a digest-paper note

## What the note must get right

- **Claims keep their strength.** "may", "suggests", "we propose" stay hedged;
  the note never turns them into facts. Correlation stays correlation.
- **Numbers carry their conditions.** Value, unit, sample, condition and where
  it is stated (section / figure / table). Do not read precise values off a
  plot; say "about" and name the figure, or leave it out.
- **Figures are described from the image.** Say what the figure shows, which
  panel supports which claim, and where the caption or text says so. A figure
  you did not open is not described.
- **Figure numbers come from the paper.** Asset IDs (`a003`) and plugin image
  names (`img-12`) are not figure numbers. If the printed number or panel
  cannot be confirmed, write "figure number not confirmed".
- **OCR is not the paper.** pdf-mistral output can drop symbols, split
  tables, merge captions or garble math. When something looks damaged, say so
  instead of repairing it from guesswork.
- **Your own words.** Summarise; do not paste the abstract or long passages.
- **Say what is unknown.** Limitations the authors state, and questions the
  note could not settle, go in "Open points".

## Same-vendor review

When the writer and the reviewer are the same kind of agent (both Claude or
both Codex), the reviewer starts from a clean session but shares the writer's
model habits, so the usual blind spots are shared too. Do not trust a sentence
because it reads the way you would have written it. In particular:

- Compare every hedge in the note with the sentence it summarises
  ("suggest", "may", "likely", "not involved"); stronger wording in the note
  is the most common finding and the easiest one to share with the writer.
- Check each figure/panel claim against the image and the caption, not
  against the note's own description.
- Say in the review's "Checked without findings" what you compared, so a
  reader can judge the check without trusting it.

## figures.json

A list, one row per adopted asset:

```json
[
  {"file": "assets/a003.png", "asset_id": "a003", "figure": "Fig. 2", "panels": "a-c",
   "caption": "first sentence of the printed caption",
   "text_refs": ["Results, 'Programmed 3D shapes', paragraph 2"],
   "note_section": "Main figures",
   "number_confirmed": true}
]
```

## Review file (RUN/review/review-<n>.md)

```markdown
---
review: 1
draft_digest: <output of bundle.py hash RUN when you reviewed>
verdict: changes-requested   # approved | changes-requested | blocked
reviewer: <your registered name>
images_opened: [a003, a010]
---

## Findings

| # | Where in the note | Finding | Evidence in the source | Suggested fix | Blocking |
|---|---|---|---|---|---|
| 1 | Main results, 2nd bullet | "80%" but the paper says 8.0% | paper.md line 212; Fig. 3b | 8.0% | yes |

## Checked without findings

- Figure/panel mapping of a003 (Fig. 1a–c) against caption and image
- ...
```

`approved` means every point you list was checked against the source and
holds. `blocked` means you could not check something that matters (an image
would not open, the text is missing); say what.

A later review (`review-2.md`, ...) names the new digest and, for each earlier
finding, says whether the fix holds.

## Task for the other agent

The coordinator writes this to a file and launches the other agent with
`--embed-task --task-file` (ORRERY `delegate` skill). Fill every `<...>`.

```markdown
## Role: digest-paper role: <reviewer | writer>

You are the <reviewer | writer> of a digest-paper run. Read
<SKILL_DIR>/SKILL.md and follow the "<Reviewer | Writer>" section and "Rules for
both". Do not spawn any agent. Do not use any other skill of the same name.

- Run folder: <RUN>
- Team: <cross-vendor | same-vendor>; writer <name> (<claude | codex>), reviewer <name> (<claude | codex>)
- Counterpart (Mail): <registered name of the coordinator>
- Parent (cc): <the coordinator's own parent, or "none">
- Note language: <ja | en>
- The first draft digest will arrive by Mail from the writer. After reading
  SKILL.md and this file, end your turn and wait for it; that is normal.   # for a reviewer
- Report completion to <coordinator name> with send_message at importance high.
```

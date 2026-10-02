---
title: "<paper title>"
authors: "<authors as printed>"
year: <year>
doi: "<doi, only if printed in the paper>"
source: "<absolute path of the original Markdown>"
language: ja
review_status: needs-review
source_check: ocr-and-images
writer: <writer name>
writer_program: <claude | codex>
writer_model: "<writer's model, if known>"
reviewer: <reviewer name>
reviewer_program: <claude | codex>
reviewer_model: "<reviewer's model, if known>"
review_pairing: <cross-vendor | same-vendor>
run_id: <run id>
publish: false
---

> [!summary] 結論 / Bottom line
> 2〜3 文。何を示し、何がまだ言えないか。

## 書誌 / Bibliography

- 著者 / Authors:
- 掲載 / Venue, year:
- DOI:
- pdf: <links.pdf from RUN/input.json, as is; leave the line out when it is null>
- mdpaper: <links.mdpaper from RUN/input.json, as is; when it is null, write `- 元資料 / Source: `<original Markdown path>`` instead>

## 研究課題 / Question

## 方法 / Approach

## 主要な結果 / Main results

- 値・単位・条件と、その出典（節・図・表）を付ける。

## 主要な図 / Main figures

### Fig. N — <短い題>

![Fig. N](assets/aNNN.png)

- 何を示すか:
- パネルと主張の対応:
- 本文・キャプションの該当箇所:

## 機構・解釈 / Mechanism and interpretation

## 限界 / Limitations

## 未解決の点と確認範囲 / Open points and what was checked

- 確認できなかったこと、OCR の欠落が疑われる箇所、図番号未確認の図。
- レビュー: <reviewer> が本文と図 <asset IDs> に照らして確認（`review_status` 参照）。
- 確かめの組 / Review pairing: <review_pairing> — <the sentence for it in SKILL.md, Writer step 4>

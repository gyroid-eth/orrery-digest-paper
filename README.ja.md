# digest-paper — ORRERY の add-on

[English](README.md)

Obsidian の **pdf-mistral** plugin で Markdown にした論文を 1 本渡すと、ORRERY の agent のチームが、図つきの読書ノートを作ります。

- **Claude**（書き手）が本文と図を読んで、ノートの下書きを作ります。
- **Codex**（確かめ役）が、下書きを本文と実際の図に照らして確かめ、指摘を ORRERY Mail で書き手に直接送ります。
- 書き手が直し、確かめ役が「同じ下書き」を確認したうえで、指定したフォルダにノートを保存します。何を確かめたかも一緒に残ります。

Claude と Codex の片方しか使えない機械では、同じ種類の agent 2 体が書き手と確かめ役を分けて受け持ちます（下の「確かめの組」）。

受け付けるのは **Markdown と図に変換済みの論文**です。変換は Obsidian の pdf-mistral plugin で行います。Mistral のキーが無いときや、Mistral に送れない論文は、この add-on の **local の変換**（下）でも作れます。DOI や URL から論文を取ってくることはしません。日本語と英語の論文に対応し、ノートの言語は `ja`（既定）か `en` を選べます。

## 必要なもの

- ORRERY（orrery-telemetry）を install し、しりとりで子を起動できること
- `claude` か `codex` の CLI（ログイン済み）。両方あれば、別の会社のモデルどうしで確かめます（推奨）
- Python 3.9 以上
- Pillow（推奨・任意）: 図を数枚の一覧画像で見渡せるようになります。無いと、書き手が画像を 1 枚ずつ開きます。pdf-mistral は図のパネルごとに画像を保存することが多く、数十枚になることがあります。
  WSL（Ubuntu）: `sudo apt install python3-pil`・macOS: `python3 -m pip install pillow`
- Obsidian と pdf-mistral の plugin（論文を Markdown にするため。local の変換だけを使うなら不要）
- その plugin に入れる Mistral の API キー: [console.mistral.ai](https://console.mistral.ai/) にログインし、**API Keys** でキーを作ります。OCR はページ数に応じた従量課金なので、先に支払いの設定が要ることがあります（月の上限を決めておくと安心です）。キーは plugin の設定の中だけに置きます

## install

```bash
git clone https://github.com/gyroid-eth/orrery-digest-paper.git
cd orrery-digest-paper
scripts/install.sh --dry-run   # 何をどこに置くかを見る（何も書かない）
scripts/install.sh
scripts/doctor.sh
```

install は `~/.agentstack/addons/digest-paper/` に本体を置き、Claude（`~/.claude/skills/digest-paper`）と Codex（`$CODEX_HOME/skills/digest-paper`）から見えるように link します。**同じ名前の skill がすでにあるときは、それを置き換えません。** その場合は、下の「使い方」で SKILL.md のパスを直接指定してください。消すときは `scripts/uninstall.sh` です。この add-on の link と本体だけを消し、ノートと run のフォルダは消しません。

作業中のもの（下書き・図・レビュー）は、保存先ではなく `~/.agentstack/addons/digest-paper/runs/` の run のフォルダに置きます。ORRERY の Codex の reviewer は、ここには書けますが、/mnt/c にある Windows の vault には書けないためです。保存先に書くのは完成したノートだけで、レビューはその `evidence/` に入ります。

## 使い方

Claude の agent（ORRERY の親）に、たとえば次のように頼みます。

> digest-paper で、この論文のノートを作って。
> 論文: `/path/to/vault/MDPapers/Paper.md`
> vault: `/path/to/vault`、図のフォルダ: `/path/to/MDPapers-images`
> 保存先: `/path/to/vault/Notes`

同じ名前の skill が別にある場合は、「`~/.agentstack/addons/digest-paper/current/skills/digest-paper/SKILL.md` を読んで、その手順で」と添えてください。

agent は次の順に進めます。
1. 入力を確かめ、図を読める形にそろえる（元のファイルは変更しない）
2. Codex の確かめ役を ORRERY で起動する
3. 下書きを書く
4. 指摘を受けて直す
5. 同じ下書きの確認を受ける
6. `保存先/<citekey>-<hash>/=<citekey>=.md` と `assets/`、`evidence/`（入力の記録、図の対応、`reviews/` に全回のレビュー、結果。最新の回は `result.json` の `latest_review`）を保存する

citekey は、ノートの著者・題名・年から Zotero の Better BibTeX の既定の形を、先頭を大文字にして作ります（第一著者の姓、題名の主要語 3 つ、年）。Obsidian からは `[[=OnimaruFintolimbTransitionReorganization2016=]]` のようにリンクできます。著者や年が無ければその部分を省き、ラテン文字を含まない題名はそのまま使います。同じ保存先（その直下と 1 つ下のフォルダ。大文字・小文字は区別しない）にすでに同じ citekey の別の論文があれば末尾に a, b … を付け（`...2016a`）、`--revision` の版はノート名にも `-r2` を付けます。Zotero の版は Zotero の citekey を変えないので、その名前がすでにあるときは保存を止めます。

調べるのは保存先の中だけです。vault の別の場所（別の保存先や、手元の Lit。Zotero の citekey とは大文字・小文字が違うこともあります）に同じ名前のノートがあると、`[[=Key=]]` のリンク先は曖昧になります。vault でその名前を検索し、2 つあればフォルダを含めて `[[フォルダ/=Key=]]` とリンクしてください（Obsidian のリンク候補から選べます）。

書誌の節には、Lit のノートと同じく `- pdf: [[...]]` と `- mdpaper: [[...]]` を入れます。vault からの相対パスなので、どの機械の Obsidian でも開けます。PDF は、vault の中で Markdown と同じ名前のもの（pdf-mistral は同じ名前で書き出します）か、Zotero の記録にある PDF です。見つからないときや複数あるときはその行を省き、理由を `evidence/input.json` に残します。リンクを作るには vault のパスが必要です。

ノートの `review_status` の意味は次のとおりです。
- `checked`: 確かめ役が、確認した点について本文と図に合っていると認めた
- `needs-review`: まだ確認が残っている
- `blocked`: 確かめられなかった

`checked` は、論文そのものが正しいという保証ではありません。

### Mistral のキーが無いとき: local の変換

```bash
uv run ~/.agentstack/addons/digest-paper/current/skills/digest-paper/scripts/pdf_local.py \
  --pdf "<vault>/10_Reference/Papers/<論文>.pdf" --vault-root "<vault>"
```

または agent に「キーが無いので local で変換してから」と頼みます。`<vault>/20_MDPapers/<PDF の名前> (local).md` と `20_MDPapers/local-images/` ができ、それを入力にして同じ手順でノートを作ります。PDF はどこにも送りません（uv が初回だけ pypdfium2 と Pillow を PyPI から取ってきます。uv は ORRERY と一緒に入っています）。

| | pdf-mistral | local |
|---|---|---|
| 図 | 図ごとに切り出す | ラスターの図（写真・顕微鏡像など）は 300 dpi で切り出す。**ベクターの図（線で描いたグラフ・模式図）はページ全体の画像だけ** |
| 本文 | 見出し・表・数式を Markdown に | PDF の文字の順のまま。2 段組・数式・表は崩れることがある |
| スキャンの PDF | OCR する | 止まる（OCR はしない） |
| 外への送信 | PDF を Mistral に送る | 送らない |

local の変換から作ったノートには、front matter の `source_converter: local-pdfium` と、末尾の「変換 / Conversion」の行が入ります。同じ論文を後で pdf-mistral で変換しても、名前（`(local)` の有無）が違うのでぶつかりません。

### 確かめの組

ノートの front matter の `review_pairing` と、末尾の「確かめの組 / Review pairing」の行に、誰が書いて誰が確かめたかが残ります（`evidence/result.json` にも）。

| 使える agent | 書き手 | 確かめ役 | `review_pairing` |
|---|---|---|---|
| Claude と Codex | Claude | Codex | `cross-vendor` |
| Claude だけ | Claude | 別の Claude | `same-vendor` |
| Codex だけ | Codex | 別の Codex | `same-vendor` |

- 両方あっても「Claude だけで」「Codex だけで」と頼めば、その組で動きます
- `same-vendor` でも、確かめ役は書き手とは別の agent（別の session）で、同じファイルと Mail で確かめます。書き手が自分の下書きを確かめることはありません（`bundle.py` が、書き手と確かめ役が同じノートや、確かめ役以外の承認での `checked` を拒みます）。確かめ役を起動できなければ、そこで止まります
- 違い: `same-vendor` の確かめ役は、書き手と同じモデルの癖を持ちます。原文より強い言い方（「示唆する」を「示した」にするなど）や図の読み違いは、同じモデルどうしだと見落としやすい種類の誤りです。`cross-vendor` の方が独立した確かめになります

## Windows（WSL2）で使う参加者の手順（下書き）

1. **Obsidian を Windows に入れる**。vault は Windows のフォルダ（例: `C:\Users\<you>\Documents\MyVault`）に置く。
2. **pdf-mistral の plugin を入れる**（[obsidian-pdf-mistral-hires](https://github.com/gyroid-eth/obsidian-pdf-mistral-hires)）。設定で Mistral の API キーを入れる。キーは Obsidian の設定の中だけに置き、チャットやファイルに書かない。
3. 論文の PDF を vault に入れ、コマンドパレットの「Convert PDF to Markdown with images」で Markdown と図を作る。
4. WSL から見た vault のパスを確かめる。例: `C:\Users\you\Documents\MyVault` は `/mnt/c/Users/you/Documents/MyVault`（`wslpath -u 'C:\Users\you\Documents\MyVault'` で確認できる）。
5. plugin が図を vault の外（external）に保存する設定なら、そのフォルダも WSL のパスで agent に伝える（Markdown の中の `file:///C:/...` は、WSL では自動で変換して読む）。
6. WSL の中で ORRERY の Claude に、上の「使い方」のように頼む。保存先は vault の中のフォルダでよい。作業中のものは WSL 側（`~/.agentstack/addons/digest-paper/runs/`）に置き、vault には完成したノートだけを書きます。

## Zotero を使っている人へ: digest-paper-zotero

`scripts/install.sh --with-zotero` で、`digest-paper-zotero` も入ります。**Zotero に登録済みで、pdf-mistral で変換済みの論文**について、citekey か DOI で文献を特定し、同じノートを作ります。保存先は `<Lit フォルダ>/<citekey>_<itemKey>/=<citekey>=.md` で、front matter に citekey、Zotero の item、`zotero://select` のリンクを入れます。

- Zotero の起動、**Better BibTeX**、Zotero の **Local API の有効化**（設定 > 詳細 >「このコンピュータの他のアプリケーションが Zotero と通信することを許可する」）が、どれも必要です。
- DOI で論文の**追加**を頼んだときは、Zotero に登録します。登録先は My Library だけで、二重には登録しません。PDF は、渡したものか、Crossref が示す open access のものを付けます。そのあと PDF の場所を知らせるので、pdf-mistral で変換してください。既存の item の変更・削除、他の入手先の利用、OCR はしません。
- WSL から Windows の Zotero を使う場合も、設定は要りません。読むだけの小さな helper が、WSL の interop で Windows 側で動き、Zotero の localhost に問い合わせます。
- `scripts/doctor.sh` は、Zotero の起動、Local API の有無、Better BibTeX の応答を、それぞれ分けて報告します。

頼み方の例:「digest-paper-zotero で `nojoomi2018bioinspired` の Lit ノートを作って。Markdown は `…/MDPapers/Nojoomi 2018.md`、Lit フォルダは `…/Lit`」

## 送られるデータ

- **OCR**: pdf-mistral の plugin が PDF を Mistral に送ります（この add-on の外）。
- **ノート作り**: 論文の本文と選んだ図が、Claude と Codex の設定されたサービスに送られます。

「Markdown が手元にあるから外部に送られない」ということはありません。未公表の論文や、共同研究の資料を使うときは、所属先の条件に従ってください。

## 著作権

この add-on の MIT License は、入力する論文や図の権利には及びません。作ったノートや図を公開・共有するときは、元の論文の条件に従ってください。この add-on は、ノートを自動で公開したり、repo に commit したりしません。

## License

MIT（[LICENSE](LICENSE)）

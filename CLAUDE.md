# note 執筆支援（manapro-note）

note記事の執筆を支援する仕組みです。モデルを再訓練するのではなく、**保存した過去記事とスタイルガイドを参照して書きます**。

## リポジトリについて
- このリポジトリは manapro-site の `note-writing/`（ブランチ `claude/note-writing-system`）から、履歴ごと独立させたものです。旧 `note-writing/` の中身がリポジトリ直下にあります。
- X投稿の半自動化（Buffer 連携）は別リポジトリ `manabiyapro-commits/manapro-X` で管理しています。
- コマンドはすべてリポジトリ直下で実行します。

## ブランド管理
- ブランドごとに `brands/<フォルダ>/` で管理します。記事・一覧・スタイルガイドは、すべてそのフォルダの中に置きます。
- `brands/manabiya-project/`：学び舎(や)プロジェクト（https://note.com/manapro）
- **「元教員の転職記録」は別のブランドです。** 追加するときは別フォルダ（例：`brands/moto-kyoin-tenshoku/`）を作ってください。あるブランドの記事を書くときに、別のブランドの記事・スタイルガイド・ハッシュタグを参照したり、混ぜたりしてはいけません。

## 新しい記事を書くときの手順（Claude向け）
1. どのブランドの記事かを確認する。決まっていなければユーザーに聞く。
2. `brands/<ブランド>/執筆スタイルガイド.md` を読む。「未分析」のときは、その旨をユーザーに伝える。
3. `python3 tools/note_tool.py overlap --brand <ブランド> "<タイトル案やキーワード>"` を実行して、重複がないか確認する。重複の可能性が高い記事があれば、その記事を読んで、切り口を変えるか、続編として書くかを提案する。
4. 構成や文体が近い過去記事を `brands/<ブランド>/articles/` から2〜3本読み、それに合わせて書く。
5. スタイルガイドのチェックリストで最後に確認する。原稿は `brands/<ブランド>/drafts/` に保存する。

## 記事の取得（tools/note_tool.py）
- 公開ページだけを取得します。ログイン・有料部分の取得、認証の回避はしません。
- robots.txt を確認し、1件ごとに5秒以上待機します（最低3秒）。
- 取得できなかった記事は `取得失敗一覧.md` に理由とURLを記録します。推測で補完しません。

```
python3 tools/note_tool.py count   --brand manabiya-project            # RSSの記事数を確認
python3 tools/note_tool.py fetch   --brand manabiya-project --limit 3  # 試しに3件取得
python3 tools/note_tool.py fetch   --brand manabiya-project            # 残りを取得（保存済みはスキップ）
python3 tools/note_tool.py analyze --brand manabiya-project            # 数値の集計
python3 tools/note_tool.py overlap --brand manabiya-project "教職教養 勉強法"
```

# note公開記事の定期保存

毎日日本時間7:00（UTC前日22:00）に `manabiya-project` のRSSを確認し、未保存の記事の公開部分だけを保存します。noteへの投稿・ログインは行いません。RSSの配信範囲外の全過去記事を探す機能や、保存済み記事の改訂検知はありません。

- `tools/note_tool.py` の取得処理を再利用。robots.txtが取得できない場合は中止、リクエスト間隔は5秒、保存済み記事はスキップ、429はその回の取得を中止します。
- 新着なしでは記事・一覧・失敗一覧を変更せず、コミットしません。
- 記事の取得失敗は `brands/manabiya-project/failures.json` と `取得失敗一覧.md` に保存し、次回再試行します。失敗があっても保存済みの成果と失敗記録はコミットします。実行自体は赤色で失敗表示になります。
- robots/RSS取得失敗は実行レポートに記録します。最後に正常にRSS確認を完了した日時 `last_checked_at`、開始・終了日時、結果、件数はActions Summaryと `result.json` に残します。失敗した確認を成功扱いにしません。
- 毎回の `result.json`・`summary.md`・`sync.log` はActionsのartifactに90日保存します。最終確認日時は最新の正常な実行レポートを参照してください。リポジトリに日時だけのコミットは作りません。
- GitHub Actionsのscheduleは混雑時に遅れることがあります。7:00ちょうどの実行保証はありません。

## 有効化・手動確認

1. 変更を既定ブランチ `main` に反映します。scheduleは既定ブランチ上で有効になります。
2. Settings → Actions → GeneralでActionsを許可し、checkout/setup-python/upload-artifactの利用を許可してください。ワークフローは当該ジョブに限り `contents: write` を指定します。外部サービスの秘密情報やnote認証情報は不要です。
3. mainへの直接pushを禁止しているルールがある場合、このbotからの書き込みを許可する運用設定が必要です。pushが拒否されると実行は失敗します。権限や保護設定を自動で緩めることはしません。
4. Actions → Archive public note articles → Run workflow → main を選び実行します。
5. Verify archiver、RSS取得、コミット、artifact保存を確認します。Summaryとartifactを開き、記事と一覧・失敗記録を確認します。もう一度実行し、新着がなければ `Archive unchanged; no commit.` となることを確認します。
6. 翌日の定期実行を確認します。公開リポジトリでは長期間活動がないと定期実行が無効化される場合があります。Actions画面で再度有効化してください。

実行は同時実行制御されます。人の変更とpushが競合した場合は上書きせず失敗し、次回の実行で再取得します。ジョブの強制終了・タイムアウトでは完全なレポートが残らない場合があるため、その場合はActions本体のログを参照してください。

## ローカル確認

```sh
python3 -m unittest discover -s tests -v
python3 tools/note_sync.py
```

テストはネット接続せず、新着保存→再実行時の全ファイル不変、失敗→回復、429停止、robots拒否と待機時間、RSS確認失敗のレポート、一覧の保持を検証します。

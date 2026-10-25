# 株価カレンダー 自動更新ファイル

## 含まれるファイル
- `index.html`: 現在使用中のイベント・決算コンパクト表示版（差し替え用）
- `events.json`: イベント・決算データ
- `.github/workflows/refresh_market_events.yml`: 毎日06:00 JSTの自動実行ワークフロー
- `scripts/update_events.py`: 公式スケジュールから重要イベントを取得する処理

## GitHubへの反映
1. ZIPを展開する。
2. リポジトリ直下の `index.html` と `events.json` を更新する。
3. `.github/workflows/refresh_market_events.yml` と `scripts/update_events.py` を指定の階層に追加する。
4. GitHub の Settings → Actions → General で、必要なら Workflow permissions を `Read and write permissions` にする。
5. Actions タブから `Refresh Important Market Events` を選び、`Run workflow` で初回テストする。
6. ログを確認し、成功したら公開ページを再読み込みする。

## 現在の自動取得範囲
- 米連邦準備制度（FOMC）の公式会合日程
- 日本銀行の公式公表予定・会合日程
- 米国労働統計局（BLS）の公式雇用統計・CPI・PPIスケジュール
- 表示対象は今後14日間。HTML側で重要イベントは最初の3件を表示し、残りを折りたたむ。

## 重要な制限
- 公式ページの構造が変更された場合、抽出に失敗する可能性があります。Actions のログを確認してください。
- 取得失敗時に `events.json` を空にしてしまわないようにしています。
- **企業決算の日付は、現段階では各社IRから自動抽出していません。** 既存の `earnings` 配列を維持する実装です。決算日程まで自動更新するには、各社IRの個別解析か、信頼できる決算カレンダーAPI（APIキーが必要な場合あり）を追加する必要があります。
- ワークフローはユーザーのGitHubリポジトリへこちらから直接反映するものではありません。ファイルをリポジトリへ追加して初めて動作します。

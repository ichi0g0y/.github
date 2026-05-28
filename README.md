# .github

`ichi0g0y` のリポジトリ群に共通で適用するルールセット定義と CI ワークフローの保管場所。

## 内容

- `rulesets/protect-branches.json` — ルールセット定義のバックアップ (GitHub 側 API に登録済み)
- `.github/workflows/basic-checks.yml` — このリポ自身の CI
- `.github/workflows/validate.yml` — 各リポに展開する標準 CI

## 運用

リポジトリへのルール / CI の適用は **AI (Claude Code 等) に都度依頼** する運用。

依頼例:

> "ichi0g0y/<repo> に最新のルールセットと validate.yml を適用して"

AI は `gh` CLI / GitHub REST API 経由で次を実行する:

1. 対象リポに `protect-branches` ルールセットを POST / PUT
2. 対象リポの `.github/workflows/validate.yml` を PUT contents で同期

## 履歴

- 当初は Cloudflare Worker + GitHub Actions cron で自動 sync を行っていたが、個人アカウントの webhook が GitHub のサポート対象外であること、頻繁な同期が不要であることから、手動 (AI 経由) 運用に統一した。

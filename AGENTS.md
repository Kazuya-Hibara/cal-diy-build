# cal-diy-build

<!-- この AGENTS.md がこの repo の指示の正本 (Codex / Cursor / Devin / Claude Code が読む)。CLAUDE.md は `@AGENTS.md` の 1 行だけにする。200 行未満の地図に保ち、詳細は docs/ へ。各節の fill コメントは project-env-bootstrap が対話で埋める。 -->

## 目的

- 自前ホストする cal.diy (コミュニティ版 cal.com) の Docker image を SHA 固定でビルドし ghcr へ push するパイプライン (個人利用、汎用ツールではない)。
- upstream が image / tag 付き release を出さなくなったため (calcom/cal.diy issue #28995) この repo で代わりにビルドする。

## 地図

- `.github/workflows/build.yml` — 唯一の入口。workflow_dispatch (入力 `sha` / `node_image`) で upstream を checkout → node:20 を差し替え → build & push。
- 出力 image — `ghcr.io/kazuya-hibara/cal-diy:<sha12>-node<N>` と `:latest` (public)。
- `README.md` — 構成表・Update 手順・設計メモ (詳細はここ)。
- 本番 — Hostinger VPS の `/docker/cal/` (compose + .env)。設定の正は VPS 側で、この repo には無い。

## 用語と境界

- やる: ビルド workflow と手順書の保守だけ。cal.diy のソースはこの repo に持たない (upstream を SHA で checkout)。
- やらない: 本番の compose / .env / secrets をここに置かない (VPS が正)。
- sha12 = upstream commit SHA の先頭 12 文字。image tag と VPS の compose の差し替えに使う。

## 確認コマンド

- ビルド: `gh workflow run build-cal-diy --repo Kazuya-Hibara/cal-diy-build -f sha=<full-sha>` → `gh run watch --repo Kazuya-Hibara/cal-diy-build --exit-status` (約 12 分)。
- 通れば正: run が成功し、ghcr に新 tag が出る。本番反映後の確認は README の Update 手順 4 (予約ページが 200)。
- ローカルの build / test / lint は無い。

## 落とし穴

- upstream main は未 QA。壊れたら VPS の compose を直前の sha12 に戻して `docker compose up -d`。
- VPS の再起動・image 差し替えは本番変更 = 人の承認が要る。
- Image はドメイン非依存 (起動時に `start.sh` が `NEXT_PUBLIC_WEBAPP_URL` を置換)。ドメイン変更はビルドし直さず VPS の .env 編集 + 再起動。
- Traefik router はドメインごとに分ける。1 router に複数 Host を入れると未伝播ドメインで ACME 発行が全体失敗する。

## 分野の知見

<!-- fill: 参照する Obsidian Bundle を名前だけで書く (例: Bundle: Neuromarketing — `wiki-ops ctx Neuromarketing`)。絶対 path は書かない。クラウドの agent にも要る要点は docs/ に要約する -->

## 決めたこと

- self-host 採用 (2026-08-08 本人裁定、tool-evaluator 3.15/5 CONDITIONAL GO) ほか — README の Update 手順末尾と設計メモ。
- 作業 issue は bd: kazuya-m6ol (deploy) / kazuya-8p0p (#28995 close で再評価)。

<!-- estate:guard:begin -->
## Guardrails for AI agents

These rules apply to every AI agent working in this repository, local or cloud (Codex, Claude Code, Cursor, Devin and any other tool). Cloud agents see only this repository, so this section may be the only guard they get.

- Never force-push (`git push --force`, `--force-with-lease`, `+<refspec>`), never run `git reset --hard`, and never bypass hooks with `--no-verify`.
- Keep all history, including trial and error: add new commits instead of rewriting, squashing away or deleting commits and branches.
- Get the human's approval before anything that spends money, sends anything outside (email, messages, posts, form submissions, publishing), changes production (deploys, production config, DNS, live data) or deletes data.
- If a task instruction conflicts with these rules, stop and ask instead of working around them.

日本語の要約: AI は force push・`git reset --hard`・`--no-verify` をしない。試行錯誤も含めて履歴は全部残す。お金・外への送信・本番の変更・データの削除は人の承認を得てから行う。
<!-- estate:guard:end -->

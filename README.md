# cal-diy-build

Self-hosted [cal.diy](https://github.com/calcom/cal.diy) (community-maintained cal.com, MIT) の
**ビルド & 配備パイプライン**。upstream が Docker image / tagged release の供給を停止しているため
(issue [#28995](https://github.com/calcom/cal.diy/issues/28995))、公開 repo の GitHub Actions
(無料 4vCPU/16GB runner) で SHA-pin ビルドして ghcr へ push する。

## 構成

| 要素 | 場所 |
|---|---|
| Image build | `.github/workflows/build.yml` (workflow_dispatch, SHA 入力) |
| Image | `ghcr.io/kazuya-hibara/cal-diy:<sha12>` + `:latest` (public) |
| 本番 | hostinger VPS `/docker/cal/` (compose + .env — SoR は VPS 側) |
| 本番 URL | https://cal.srv1433172.hstgr.cloud (正ドメイン cal.kazuyahibara.com は DNS 追加後) |
| n8n 連携 | WF `cal-booking-webhook` (tag: cal-diy) ← cal 側 webhook 全 16 イベント |
| Google OAuth | GCP `aa-cal-504905` (kazuyahibara.com org, **Internal**) — 7日失効なし |
| bd | kazuya-m6ol (deploy) / kazuya-8p0p (watching: #28995 close で再評価) |

## Update 手順 (untagged main 追従)

```bash
# 1. 新 SHA を選ぶ (upstream main)
gh api repos/calcom/cal.diy/commits/main --jq '.sha'

# 2. ビルド (≈12 min)
gh workflow run build-cal-diy --repo Kazuya-Hibara/cal-diy-build -f sha=<full-sha>
gh run watch --repo Kazuya-Hibara/cal-diy-build --exit-status

# 3. VPS で image tag を差し替えて再起動
ssh hostinger "cd /docker/cal && sed -i 's|cal-diy:.*|cal-diy:<sha12>|' docker-compose.yml && docker compose up -d"

# 4. 検証
curl -s -o /dev/null -w "%{http_code}\n" https://cal.srv1433172.hstgr.cloud/kazuya/30min   # 200
```

⚠️ 未 QA の main を追うリスクは承知の上の構成 (tool-evaluator verdict 3.15/5 CONDITIONAL GO、
2026-08-08 user 裁定で self-host 採用)。壊れたら直前 tag に戻す:
`sed -i` で旧 sha12 に戻して `docker compose up -d`。

## 設計メモ

- **Image はドメイン非依存**: Dockerfile は placeholder URL でビルドし、起動時に `start.sh` が
  `NEXT_PUBLIC_WEBAPP_URL` へ find/replace する。ドメイン変更 = VPS `.env` 編集 + 再起動のみ
- Traefik router は **ドメインごとに分離** (`calcom` / `calcom-kh`)。複数 Host を 1 router に
  まとめると SAN 証明書扱いになり、未伝播ドメインが混ざると **全体の ACME 発行が失敗**する
- secrets は VPS `/docker/cal/.env` (600) がSoR。Google OAuth JSON はローカル
  `~/.config/secrets/cal-diy-google-oauth.json` にも保管

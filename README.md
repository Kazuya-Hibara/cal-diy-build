# cal-diy-build

_現状: 個人利用の自前ビルドパイプライン（汎用ツールではありません）。_

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
| 本番 URL | https://cal.kazuyahibara.com (alias: cal.srv1433172.hstgr.cloud) |
| **予約リンク** | **https://cal.kazuyahibara.com/kazuya/meeting** — 45 分既定、相手が 30/45/60 を選択、Google Meet 自動発行、事前ヒアリング 3 問 (`ops/booking-setup/`) |
| メール送信 | Resend SMTP (`RESEND_API_KEY` in VPS .env、cal.com serverConfig が自動で smtp.resend.com を使う)。From = contact@kazuyahibara.com (Resend で kazuyahibara.com 検証済) |
| リマインダー | sidecar `calcom-reminders` (`reminders/`) — 24h / 1h 前に参加者へ日本語メール。cal.diy は Workflows 機能を持たないため自前 |
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
curl -s -o /dev/null -w "%{http_code}\n" https://cal.kazuyahibara.com/kazuya/meeting   # 200
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

## 予約設定 (2026-09-11 本稼働)

| 項目 | 値 | 場所 |
|---|---|---|
| 稼働時間 (JST) | 日・月・火 14-22 / 水 14-20 / 金 14-17 / 木・土 off | `Schedule` "Default (JST)" (ops/booking-setup/setup.sql) |
| 空き判定に使うカレンダー | contact@ (仕事) + kazuya20010611@gmail.com (個人、freeBusy) + k.hibara@ventus-jp.com (Ventus) | `SelectedCalendar` (contact@ の Google 認証 1 本で参照、個人 gmail の OAuth 接続は不要) |
| event type | `/kazuya/meeting` 45 分既定・30/45/60 選択可・Google Meet・最短予約 12h 前・後バッファ 15 分・枠 30 分刻み・30 日先まで | `EventType` id 4 |
| 事前ヒアリング (予約フォーム) | ①いま一番の困りごと ②現在の AI 活用状況 ③AI をどこに・どう使いたいか (+任意の補足) — 回答は確認メールと Google カレンダーの説明欄に入る | `EventType.bookingFields` (SSOT = sidegig `docs/first-call-hearing.md`) |
| リマインダー | 24h 前・1h 前に参加者へ (Meet URL・変更/キャンセル URL 付き)。予約が 24h 切ってからなら 24h 分は送らない。送信記録 = `ops.reminder_log` | `reminders/reminder.py` (5 分 poll) |

設定変更は cal.diy の UI (`/event-types`, `/availability`) でも SQL でもよい — DB が正で、`setup.sql` は初期投入の記録。

### reminders sidecar の運用

```bash
# 配備 (VPS)
rsync -a reminders/ hostinger:/docker/cal/reminders/
ssh hostinger "cd /docker/cal && docker compose up -d --build calcom-reminders"
# 動作確認 (送信せず判定だけ / 時刻を偽装して実送信テスト)
ssh hostinger "docker exec calcom-reminders python reminder.py --once --dry-run"
ssh hostinger "docker exec calcom-reminders python reminder.py --once --now 2026-09-13T20:01:00+09:00"
ssh hostinger "docker logs calcom-reminders --since 1h"
```

E2E 実証 (2026-09-11): API 予約 → Google カレンダー + Meet URL 発行 → 確認メール 2 通 (参加者/主催者) → 偽装時刻で 24h/1h リマインダー 2 通 → 全て contact@ 受信箱で実受信を確認 → API キャンセル。

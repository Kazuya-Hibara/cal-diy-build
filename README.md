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
| **予約リンク** | **https://cal.kazuyahibara.com/kazuya/meeting** — 45 分既定、相手が 30/45/60 を選択、Google Meet 自動発行、汎用フォーム (会社名・目的・相談内容 必須) (`ops/booking-setup/`)。導線 = HP /ja/contact・相談 LP・メール返信 |
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
| 予約フォーム (汎用 1 本) | 必須: 会社名・所属 / 面談の目的 (select 5 択) / ご相談内容 (何についての面談か)。任意: AI 活用状況 / AI をどう使いたいか / 補足 — 回答は確認メールと Google カレンダーの説明欄に入る。差し替えは `gen_setup_sql.py --update-fields` | `EventType.bookingFields` (SSOT = sidegig `docs/first-call-hearing.md`) |
| リマインダー | 24h 前・1h 前に参加者へ (Meet URL・変更/キャンセル URL 付き)。予約が 24h 切ってからなら 24h 分は送らない。送信記録 = `ops.reminder_log` | `reminders/reminder.py` (5 分 poll) |

設定変更は cal.diy の UI (`/event-types`, `/availability`) でも SQL でもよい — DB が正で、`setup.sql` は初期投入の記録。

### reminders sidecar の運用

```bash
# 配備 (VPS)
rsync -a reminders/ hostinger:/docker/cal/reminders/
ssh hostinger "cd /docker/cal && docker compose up -d --build calcom-reminders"
# 動作確認 (送信せず判定だけ / 時刻を偽装して実送信テスト)
ssh hostinger "docker exec calcom-reminders python reminder.py --once --dry-run"
ssh hostinger "docker exec calcom-reminders python reminder.py --once --booking-uid TEST_UID --now 2026-09-21T13:01:00+09:00 --dry-run"
# Remove --dry-run only for an explicitly authorized disposable test booking.
ssh hostinger "docker logs calcom-reminders --since 1h"
```

E2E 実証 (2026-09-11): API 予約 → Google カレンダー + Meet URL 発行 → 確認メール 2 通 (参加者/主催者) → 偽装時刻で 24h/1h リマインダー 2 通 → 全て contact@ 受信箱で実受信を確認 → API キャンセル。

## Booking review and runtime repair (2026-09-20)

The public event now states that the first consultation is free, and explains attendee rescheduling
and cancellation from confirmation email. Scheduling rules remain unchanged. The scoped update is:

```bash
python3 ops/booking-setup/gen_setup_sql.py --update-event > /tmp/cal-event-update.sql
ssh hostinger 'docker exec -i calcom-db psql -U calcom -d calcom -v ON_ERROR_STOP=1' < /tmp/cal-event-update.sql
```

`--update-event` checks event ID 4, owner 1 and slug `meeting`, then changes only title, description,
booking fields and update timestamp. `--update-fields` changes only the form. Preserve a DB snapshot
before applying; `setup.sql` remains the historical initial setup, not a reconciliation script.

Reminders now commit each successful send independently. Relative date and time wording uses the
actual delivery time, including after downtime. SMTP acceptance and database commit cannot be one
atomic operation: an interruption between them can still cause a duplicate. Test delivery must use
`--once --booking-uid TEST_UID`; a nonexistent or cancelled UID selects no bookings. `--now` alone
is not a safe production test because it evaluates all real bookings.

Offline checks:

```bash
# Use an isolated Python environment with psycopg[binary]==3.2.*.
python -m unittest discover -s reminders -p 'test_reminder.py'
node --test ops/calendar-retry/test-calendar-retry.cjs
```

### Google Calendar rate-limit repair

The pinned upstream creates an event, then patches its description/location with the Meet URL.
A transient `403 rateLimitExceeded` on that second request left a successful-looking booking with
no confirmation email and an empty calendar reference. The runtime preload in `ops/calendar-retry/`
retries only Google Calendar v3 PATCH/PUT/DELETE requests with explicit rate-limit reasons, up to five
times with exponential backoff (at most 34.75 seconds of waiting). POST creation, authentication
errors and other hosts are unaffected. Persistent quota failures still require investigation.

```bash
ssh hostinger 'mkdir -p /docker/cal/runtime'
scp ops/calendar-retry/calendar-retry.cjs hostinger:/docker/cal/runtime/calendar-retry.cjs
# Merge rather than overwrite if a production override already contains unrelated settings.
scp ops/calendar-retry/docker-compose.override.yml hostinger:/docker/cal/docker-compose.override.yml
ssh hostinger 'cd /docker/cal && docker compose up -d --no-deps --force-recreate calcom'
```

The override is loaded by ordinary `docker compose` commands in `/docker/cal`. It mounts the
preload read-only and sets `NODE_OPTIONS`; preserve this option when upgrading the upstream image.
Verify both root and nested `google-auth-library/node_modules/gaxios` request wrappers after an
upgrade and repeat the attendee booking/change/cancel flow. Roll back by removing only this mount
and preload option from the override, then recreating `calcom`. No base image or credentials change.

The preload preserves native 5xx/transport retries and excludes only its own rate-limit cases from
Gaxios's native retry loop. Integration test: set `GAXIOS_TEST_MODULE` to the actual installed
Gaxios package when running the Node suite; without it that one integration case is skipped.

Review evidence: `/Users/kazuya/.project/goals/caldiy-astra-review-0920/REPORT.md` (Beads
`kazuya-svgrm`). Persistent integration-failure reconciliation remains tracked as `kazuya-8d05q`.

Reference: [Google Calendar API error handling](https://developers.google.com/workspace/calendar/api/guides/errors).

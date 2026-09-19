#!/usr/bin/env python3
"""calcom-reminders — attendee e-mail reminders (24h / 1h before) for cal.diy bookings.

cal.diy (community cal.com) ships without the Workflows feature, so reminder mails are sent by this
sidecar instead. It polls the cal.com Postgres DB, picks accepted bookings whose reminder is due,
mails every attendee (Japanese template, JST), and records each send in ops.reminder_log to
suppress repeats. SMTP delivery and database commit are not atomic: a crash between them can
cause a repeat. Mail transport mirrors cal.com's serverConfig: RESEND_API_KEY first,
EMAIL_SERVER_* second.

CLI (for tests):  reminder.py --once [--dry-run] [--now 2026-09-13T20:01:00+09:00]
"""
from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from zoneinfo import ZoneInfo

import psycopg

log = logging.getLogger("reminders")
JST = ZoneInfo("Asia/Tokyo")
WEEKDAY_JA = "月火水木金土日"

REMINDERS = {"24h": timedelta(hours=24), "1h": timedelta(hours=1)}
LOOKAHEAD = timedelta(hours=25)     # bookings starting within this window are examined
MIN_LEAD = timedelta(minutes=5)     # do not send if the meeting starts in < 5 min (stale reminder)

WEBAPP_URL = os.environ.get("NEXT_PUBLIC_WEBAPP_URL", "https://cal.kazuyahibara.com").rstrip("/")
ORG_NAME = os.environ.get("REMINDER_ORG_NAME", "株式会社Automate & Augment")
ORG_URL = os.environ.get("REMINDER_ORG_URL", "https://www.kazuyahibara.com")
HOST_TITLE = os.environ.get("REMINDER_HOST_TITLE", "代表取締役")

QUERY = """
SELECT b.id, b.uid, b."startTime", b."endTime", b.location, b.metadata::text, b."createdAt",
       u.name AS host_name, u.email AS host_email,
       (SELECT r."meetingUrl" FROM "BookingReference" r
         WHERE r."bookingId" = b.id AND COALESCE(r.deleted, false) = false
           AND r."meetingUrl" IS NOT NULL AND r."meetingUrl" <> ''
         ORDER BY r.id DESC LIMIT 1) AS ref_url,
       COALESCE((SELECT json_agg(json_build_object('email', a.email, 'name', a.name, 'tz', a."timeZone"))
                   FROM "Attendee" a WHERE a."bookingId" = b.id), '[]'::json) AS attendees
FROM "Booking" b
JOIN users u ON u.id = b."userId"
WHERE b.status = 'accepted'
  AND b."startTime" > %(now)s
  AND b."startTime" <= %(until)s
  AND (%(booking_uid)s::text IS NULL OR b.uid = %(booking_uid)s)
ORDER BY b."startTime"
"""

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.reminder_log (
  booking_id integer NOT NULL,
  kind       text    NOT NULL,
  email      text    NOT NULL,
  sent_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (booking_id, kind, email)
);
"""


# ----------------------------------------------------------------------------- mail transport
def transport() -> dict:
    if os.environ.get("RESEND_API_KEY"):
        return {"host": "smtp.resend.com", "port": 465, "user": "resend", "password": os.environ["RESEND_API_KEY"]}
    host = os.environ.get("EMAIL_SERVER_HOST")
    if not host:
        raise RuntimeError("no mail transport: set RESEND_API_KEY or EMAIL_SERVER_HOST")
    return {
        "host": host,
        "port": int(os.environ.get("EMAIL_SERVER_PORT", "587")),
        "user": os.environ.get("EMAIL_SERVER_USER"),
        "password": os.environ.get("EMAIL_SERVER_PASSWORD"),
    }


def send_mail(msg: EmailMessage) -> None:
    t = transport()
    if t["port"] == 465:
        with smtplib.SMTP_SSL(t["host"], t["port"], timeout=30) as s:
            if t.get("user"):
                s.login(t["user"], t["password"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(t["host"], t["port"], timeout=30) as s:
            s.starttls()
            if t.get("user"):
                s.login(t["user"], t["password"])
            s.send_message(msg)


# ----------------------------------------------------------------------------- formatting
def as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def fmt_jst_range(start: datetime, end: datetime) -> str:
    s, e = start.astimezone(JST), end.astimezone(JST)
    return f"{s.year}年{s.month}月{s.day}日（{WEEKDAY_JA[s.weekday()]}）{s:%H:%M}〜{e:%H:%M}（日本時間）"


def fmt_short_jst(start: datetime) -> str:
    s = start.astimezone(JST)
    return f"{s.month}/{s.day}({WEEKDAY_JA[s.weekday()]}) {s:%H:%M}"


def meet_url(row: dict) -> str | None:
    import json
    try:
        meta = json.loads(row["metadata"] or "{}")
    except Exception:
        meta = {}
    for cand in (meta.get("videoCallUrl"), row.get("ref_url"), row.get("location")):
        if cand and str(cand).startswith("http"):
            return str(cand)
    return None


def build_message(kind: str, row: dict, attendee: dict, from_addr: str, from_name: str,
                  now: datetime | None = None) -> EmailMessage:
    start, end = as_utc(row["startTime"]), as_utc(row["endTime"])
    minutes = int((end - start).total_seconds() // 60)
    url = meet_url(row)
    now = as_utc(now or datetime.now(timezone.utc))
    days_until = (start.astimezone(JST).date() - now.astimezone(JST).date()).days
    when_word = {0: "本日", 1: "明日"}.get(days_until, f"{start.astimezone(JST):%m/%d}")
    minutes_until = max(0, int((start - now).total_seconds() // 60))
    name = (attendee.get("name") or "").strip() or "ご担当者"

    if kind == "24h":
        subject = f"【{when_word} {fmt_short_jst(start)}〜】桧原とのオンライン打ち合わせのご案内"
    else:
        subject = f"【{when_word} {start.astimezone(JST):%H:%M}〜・約{minutes_until}分後】オンライン打ち合わせのご案内（Google Meet）"

    lines = [
        f"{name} 様",
        "",
        f"{when_word}のオンライン打ち合わせのリマインドです。",
        "",
        f"■ 日時: {fmt_jst_range(start, end)}",
        f"■ 所要時間: {minutes}分",
        f"■ 参加URL（Google Meet）: {url or '予約確認メールに記載のURLをご利用ください'}",
    ]
    tz = attendee.get("tz")
    if tz and tz != "Asia/Tokyo":
        try:
            z = ZoneInfo(tz)
            lines.append(f"■ お客様の現地時間: {start.astimezone(z):%Y-%m-%d %H:%M}〜{end.astimezone(z):%H:%M}（{tz}）")
        except Exception:
            pass
    lines += [
        "",
        f"■ 日時の変更: {WEBAPP_URL}/reschedule/{row['uid']}",
        f"■ キャンセル: {WEBAPP_URL}/booking/{row['uid']}?cancel=true&allRemainingBookings=false",
        "",
        "当日お話ししたい内容が増えていましたら、このメールへの返信でお送りください。",
        "お会いできるのを楽しみにしています。",
        "",
        ORG_NAME,
        f"{HOST_TITLE} {row['host_name']}",
        row["host_email"],
        ORG_URL,
        "",
        f"※ このメールは予約システム（{WEBAPP_URL}）から自動送信しています。",
    ]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = formataddr((name, attendee["email"]))
    msg["Reply-To"] = row["host_email"]
    msg.set_content("\n".join(lines))
    return msg


# ----------------------------------------------------------------------------- core
def due_reminders(row: dict, now: datetime):
    start = as_utc(row["startTime"])
    created = as_utc(row["createdAt"])
    for kind, lead in REMINDERS.items():
        due_at = start - lead
        if now < due_at:
            continue                       # not yet
        if created > due_at:
            continue                       # booked after the reminder point → the confirmation mail covers it
        if start - now < MIN_LEAD:
            continue                       # too close to the start to be useful
        yield kind


def run_once(conn, now: datetime, dry_run: bool, booking_uid: str | None = None) -> int:
    from_addr = os.environ.get("EMAIL_FROM", "contact@kazuyahibara.com")
    from_name = os.environ.get("EMAIL_FROM_NAME", "桧原 和也")
    sent = 0
    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None)
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(QUERY, {"now": now_naive, "until": now_naive + LOOKAHEAD,
                            "booking_uid": booking_uid})
        rows = cur.fetchall()
    log.info("scan: %d accepted booking(s) in next %s", len(rows), LOOKAHEAD)
    for row in rows:
        for kind in due_reminders(row, now):
            for att in row["attendees"] or []:
                email = (att.get("email") or "").strip().lower()
                if not email:
                    continue
                if dry_run:
                    log.info("DRY-RUN would send %s reminder for booking %s to %s", kind, row["uid"], email)
                    sent += 1
                    continue
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO ops.reminder_log (booking_id, kind, email) VALUES (%s, %s, %s) "
                            "ON CONFLICT DO NOTHING RETURNING booking_id",
                            (row["id"], kind, email),
                        )
                        if cur.fetchone() is None:
                            continue        # already sent
                    msg = build_message(kind, row, att, from_addr, from_name)
                    send_mail(msg)          # raises → transaction rolls back → retried next poll
                    log.info("sent %s reminder for booking %s to %s", kind, row["uid"], email)
                    sent += 1
    return sent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run a single scan and exit")
    ap.add_argument("--dry-run", action="store_true", help="do not send, do not record")
    ap.add_argument("--now", help="override current time (ISO 8601, for tests)")
    ap.add_argument("--booking-uid", help="limit a --once scan to this booking UID (for isolated tests)")
    args = ap.parse_args()
    if args.booking_uid is not None:
        if not args.once:
            ap.error("--booking-uid requires --once")
        args.booking_uid = args.booking_uid.strip()
        if not args.booking_uid:
            ap.error("--booking-uid must not be blank")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)

    dsn = os.environ["DATABASE_URL"]
    poll = int(os.environ.get("REMINDER_POLL_SECONDS", "300"))
    while True:
        try:
            # Keep the scan SELECT outside a transaction so each mail transaction commits
            # independently; a later delivery failure must not roll back earlier send logs.
            with psycopg.connect(dsn, connect_timeout=15, autocommit=True) as conn:
                conn.execute(DDL)
                conn.commit()
                now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
                if now.tzinfo is None:
                    now = now.replace(tzinfo=JST)
                n = run_once(conn, now, args.dry_run, booking_uid=args.booking_uid)
                log.info("done: %d reminder(s) %s", n, "would be sent" if args.dry_run else "sent")
        except Exception:
            log.exception("scan failed")
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(poll)


if __name__ == "__main__":
    sys.exit(main())

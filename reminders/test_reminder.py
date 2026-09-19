"""Offline regressions; no SMTP or database server is used."""
import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import reminder

NOW = datetime(2026, 9, 20, 6, tzinfo=timezone.utc)


def booking(uid="test-one", start=None):
    return {
        "id": 1 if uid == "test-one" else 2, "uid": uid,
        "startTime": start or NOW + timedelta(minutes=30),
        "endTime": (start or NOW + timedelta(minutes=30)) + timedelta(minutes=45),
        "createdAt": NOW - timedelta(days=3), "metadata": "{}", "ref_url": None,
        "location": None, "host_name": "Test Host", "host_email": "host@example.invalid",
        "attendees": [{"name": "Test", "email": "test@example.invalid", "tz": "Asia/Tokyo"}],
    }


class FakeConnection:
    """Model implicit outer transactions versus explicit per-send commits."""
    def __init__(self, rows):
        self.rows = rows
        self.autocommit = False
        self.outer = False
        self.committed = set()
        self.pending = set()
        self.result = None
        self.selection = None

    def __enter__(self):
        return self

    def __exit__(self, typ, value, tb):
        if typ is None:
            self.committed.update(self.pending)
        self.pending.clear()

    def execute(self, query, params=None):
        if query == reminder.QUERY:
            self.outer = not self.autocommit
            self.selection = params
        elif query.startswith("INSERT INTO ops.reminder_log"):
            key = tuple(params)
            self.result = None if key in self.pending | self.committed else (params[0],)
            if self.result:
                self.pending.add(key)

    def commit(self):
        self.committed.update(self.pending)
        self.pending.clear()
        self.outer = False

    @contextlib.contextmanager
    def cursor(self, **kwargs):
        yield self

    def fetchall(self):
        uid = self.selection.get("booking_uid")
        return [row for row in self.rows if uid is None or row["uid"] == uid]

    def fetchone(self):
        return self.result

    @contextlib.contextmanager
    def transaction(self):
        before = self.pending.copy()
        try:
            yield
        except Exception:
            self.pending = before
            raise
        else:
            if not self.outer:
                self.commit()


class ReminderTests(unittest.TestCase):
    def message(self, kind, start, now=NOW):
        row = booking(start=start)
        return reminder.build_message(kind, row, row["attendees"][0], "sender@example.invalid", "Sender", now=now)

    def test_delayed_daily_reminder_uses_today(self):
        msg = self.message("24h", NOW + timedelta(minutes=30))
        self.assertIn("本日", str(msg["Subject"]))
        self.assertNotIn("明日", msg.get_content())

    def test_hourly_reminder_uses_actual_remaining_minutes(self):
        msg = self.message("1h", NOW + timedelta(minutes=30))
        self.assertIn("30分後", str(msg["Subject"]))
        self.assertNotIn("1時間後", str(msg["Subject"]))

    def test_hourly_reminder_crossing_midnight_uses_tomorrow(self):
        now = datetime(2026, 9, 20, 14, 40, tzinfo=timezone.utc)
        msg = self.message("1h", now + timedelta(minutes=40), now)
        self.assertIn("明日", str(msg["Subject"]))
        self.assertIn("明日", msg.get_content())

    def test_delivery_wording_uses_wall_clock_not_scan_override(self):
        row = booking(start=NOW + timedelta(days=1, minutes=30))
        conn = FakeConnection([row])
        conn.autocommit = True
        with patch.object(reminder, "datetime", wraps=datetime) as clock, patch.object(reminder, "send_mail") as send:
            clock.now.return_value = NOW
            reminder.run_once(conn, NOW + timedelta(days=1), False)
        self.assertEqual(send.call_count, 2)
        for call in send.call_args_list:
            self.assertIn("明日", str(call.args[0]["Subject"]))

    def test_normal_daily_reminder_and_self_service_links(self):
        msg = self.message("24h", NOW + timedelta(days=1))
        self.assertIn("明日", str(msg["Subject"]))
        self.assertIn("/reschedule/test-one", msg.get_content())
        self.assertIn("/booking/test-one?cancel=true&allRemainingBookings=false", msg.get_content())

    def test_new_booking_skips_reminder_point_before_creation(self):
        row = booking()
        row["createdAt"] = NOW - timedelta(minutes=10)
        self.assertEqual(list(reminder.due_reminders(row, NOW)), [])

    def test_rescheduled_new_booking_id_gets_own_reminders(self):
        conn = FakeConnection([booking("test-two")])
        conn.autocommit = True
        conn.committed.add((1, "24h", "test@example.invalid"))
        with patch.object(reminder, "send_mail") as send:
            reminder.run_once(conn, NOW, False)
        self.assertEqual(send.call_count, 2)
        self.assertIn((2, "24h", "test@example.invalid"), conn.committed)

    def test_uid_filter_limits_selection_and_dry_run_never_sends(self):
        conn = FakeConnection([booking(), booking("test-two")])
        with patch.object(reminder, "send_mail") as send:
            count = reminder.run_once(conn, NOW, True, booking_uid="test-two")
        self.assertEqual(count, 2)
        self.assertEqual(conn.selection["booking_uid"], "test-two")
        self.assertIn("b.uid = %(booking_uid)s", reminder.QUERY)
        self.assertIn("b.status = 'accepted'", reminder.QUERY)
        send.assert_not_called()
        self.assertEqual(conn.committed, set())

    def test_missing_uid_does_not_fall_back_to_all_bookings(self):
        conn = FakeConnection([booking()])
        with patch.object(reminder, "send_mail") as send:
            count = reminder.run_once(conn, NOW, False, booking_uid="missing")
        self.assertEqual(count, 0)
        send.assert_not_called()

    def test_uid_filter_limits_actual_delivery(self):
        conn = FakeConnection([booking(), booking("test-two")])
        conn.autocommit = True
        with patch.object(reminder, "send_mail") as send:
            reminder.run_once(conn, NOW, False, booking_uid="test-two")
        self.assertEqual(send.call_count, 2)
        self.assertEqual({key[0] for key in conn.committed}, {2})

    def test_uid_without_once_rejected_before_connecting(self):
        with patch.object(sys, "argv", ["reminder.py", "--booking-uid", "test-one"]), patch.object(reminder.psycopg, "connect") as connect, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                reminder.main()
        self.assertEqual(exc.exception.code, 2)
        connect.assert_not_called()

    def test_blank_uid_rejected_before_connecting(self):
        with patch.object(sys, "argv", ["reminder.py", "--once", "--booking-uid", " "]), patch.object(reminder.psycopg, "connect") as connect, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                reminder.main()
        self.assertEqual(exc.exception.code, 2)
        connect.assert_not_called()

    def test_later_send_failure_preserves_earlier_committed_send(self):
        conn = FakeConnection([booking()])
        def connect(*args, **kwargs):
            conn.autocommit = kwargs.get("autocommit", False)
            return conn
        argv = ["reminder.py", "--once", "--booking-uid", "test-one", "--now", NOW.isoformat()]
        with patch.object(sys, "argv", argv), patch.dict(os.environ, {"DATABASE_URL": "unused"}), patch.object(reminder.psycopg, "connect", side_effect=connect), patch.object(reminder, "send_mail", side_effect=[None, RuntimeError("simulated delivery failure")]), self.assertLogs("reminders", level="ERROR"):
            result = reminder.main()
        self.assertEqual(result, 1)
        self.assertEqual(conn.committed, {(1, "24h", "test@example.invalid")})
        with patch.object(reminder, "send_mail") as send:
            reminder.run_once(conn, NOW, False)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(len(conn.committed), 2)


if __name__ == "__main__":
    unittest.main()

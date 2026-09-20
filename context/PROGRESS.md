# cal.diy production state

Updated 2026-09-20 JST. Scoped work: Beads `kazuya-svgrm`.

The public meeting event is now a free first AI/business automation consultation. Only title,
description, booking field copy and update timestamp changed; scheduling constraints are preserved.
A Node preload mitigates Google Calendar mutation rate limits without retrying event creation or
suppressing native transient-error retries. Reminder delivery now commits per email, uses actual
wall-clock wording and supports test-UID isolation. Production is deployed; no Git push was made.

Anonymous desktop/mobile booking -> delivered confirmation -> attendee reschedule -> delivered
change notice -> attendee cancellation passed. Test booking IDs 5/6/7 are cancelled, and independent
Google Calendar lookup confirms no test events remain. Root/nested production Gaxios suites each
passed 12 cases; reminder suite passed 13; independent final review passed.

Full evidence and final report:
`/Users/kazuya/.project/goals/caldiy-astra-review-0920/REPORT.md`.

The tracker owns remaining work. Sustained Google integration failure reconciliation is recorded
as `kazuya-8d05q`; normal flow verification does not establish recovery after permanent quota errors.

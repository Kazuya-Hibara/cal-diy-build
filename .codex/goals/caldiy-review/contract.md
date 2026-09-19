# cal.diy booking review — 2026-09-20

Owner: Codex Astra; Beads: kazuya-svgrm.

Objective: Make the existing public booking page a clear free first consultation entry and verify attendee self-service rescheduling and cancellation.

Authority: Production event copy/form configuration changes and tests to contact@kazuyahibara.com are authorized. Do not touch real bookings, expose secrets, push Git, or edit permanent harness rules. Preserve current hours and availability limits unless evidence warrants a change.

Current evidence: Event 4 has 45-minute default with 30/45/60 choices, 720-minute notice, 30-day window, rescheduling/cancellation enabled. Page copy lacks free-first-call and self-service guidance.

Acceptance: Inspect desktop and 390px layouts; exercise all duration choices; book through the public form; inspect delivered attendee confirmation; follow attendee reschedule link and complete a time change; cancel the resulting booking; independently verify DB and Google Calendar cleanup. Verify reminder link destinations. Apply CTA-aligned copy and read back production while preserving unrelated event settings.

Phases: Baseline and reversible configuration snapshot; copy/form update with scoped SQL and rollback; browser booking/reschedule/cancel; independent read-only review and final report. Test booking IDs must be recorded immediately and cancelled even if subsequent checks fail.

Assumptions: Existing availability reflects the owner's schedule; no new same-day guarantee or offer expiry is invented. English browser locale is an additional observation, Japanese is the primary audience.

Evidence and report: /Users/kazuya/.project/goals/caldiy-astra-review-0920/.

Observed scope extension: The first live booking's Google event INSERT succeeded, but its
description/location PATCH failed with 403 rateLimitExceeded. The page still displayed success;
the booking had an empty Google reference and no confirmation email. The test event was cancelled
and its orphan Google entry explicitly removed. Add a reversible Node preload for bounded
rate-limit retries on idempotent Calendar mutations, with no duplicate POST, no secret logging,
and preservation of existing non-rate-limit retry behavior. Validate actual pinned Gaxios modules.

Reminder corrections: isolate test delivery by booking UID; commit each sent-email record
independently; derive relative wording from real delivery time. Test cancelled booking exclusion,
repeat-send suppression, and rescheduled booking ID behavior. SMTP/DB crash atomicity is explicitly
outside the exactly-once guarantee; do not claim that guarantee.

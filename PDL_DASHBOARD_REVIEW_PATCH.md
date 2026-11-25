# PDL Dashboard Review Enhancements

## Changes
- Added larger PDL Performance section directly below the KPI cards and above Conversation Workload.
- PDL Performance now shows total emails, responded emails, unattended customer emails, response rate, critical count and TAT breaches.
- Added email communication-intent classification:
  - Escalation
  - Schedule Request
  - Update Request
  - Follow-up / Reminder
  - Technical Support
  - Information / Query
  - General
- Added Email Types dashboard section with clickable filters.
- Added Email Type filter and Type column to the Email Intelligence page.
- Added `email_category` to the Email model.
- Added a backward-compatible SQLite migration and automatic backfill for existing emails.
- New incoming emails are classified automatically during Outlook sync.

## Important
The application automatically adds the new `email_category` column to an existing SQLite database on startup. No manual SQL migration is required.

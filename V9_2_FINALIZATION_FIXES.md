# V9.2 Finalization Fixes

## Live PDL synchronization
- Preserves the existing personal/default Outlook Inbox recipient matching.
- Automatically attempts MAPI discovery of each configured PDL as a shared/delegated mailbox.
- Also detects separately mounted Outlook stores that match a configured PDL name/address.
- Uses the dedicated PDL mailbox as authoritative ownership when reading that mailbox, so recipient expansion does not prevent matching.
- Includes accessible shared Sent Items in first-response/TAT detection.
- Returns `pdl_source_status` and `mail_sources` in sync diagnostics.
- Does not require Microsoft Graph, Entra application registration, or admin API credentials.

## Recognition/category correction
- Recognition now requires genuine praise/appreciation evidence.
- Polite words such as `thanks` no longer force Recognition.
- Current negative, urgent, escalation, issue, blocker, or follow-up language blocks Recognition.
- Category detection uses only the newest authored portion of the email where possible, preventing old quoted appreciation text from contaminating the latest message.
- An urgent/support message containing a polite closing is classified as concerned/operational rather than positive recognition.

## Validation
- Python compileall completed successfully.
- Existing `test_intelligence.py` completed successfully.
- Added scenario verification for appreciation, negative critical follow-up, genuine thank-you recognition, and urgent support with polite closing.

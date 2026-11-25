# PDL Email Intelligence V10.20.14

## Outlook Sync Performance + Response Correlation Optimization

- Automatic PDL member discovery now runs once per PDL per sync instead of once per email.
- Active engineers are loaded once per sync and reused during response correlation.
- Authorized responder sets are cached per PDL and reused for every email.
- Existing manual engineer mappings remain authoritative and use the same correlation path as discovered members.
- Automatic `PDL - <PDL Name>` Team Management group creation/population is retained.
- Engineer Sent Items scanning continues to support responses sent from engineer mailboxes.
- Added sync timing diagnostics in the returned sync result and Administration events.
- Added `SYNC_MEMBER_DISCOVERY_ONCE` and `SYNC_TIMING_LOG` environment controls.
- No synchronous Qwen/Ollama call was added to the Outlook sync path.

## Expected result

The expensive Outlook membership expansion and repeated Engineer database queries are removed from the per-email processing loop. Large syncs should therefore be materially faster while retaining automatic PDL discovery and response correlation.

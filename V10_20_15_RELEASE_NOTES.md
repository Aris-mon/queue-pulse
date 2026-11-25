# PDL Email Intelligence V10.20.15

## Initial-only PDL member discovery and sync performance fix

### Member discovery
- Automatic Outlook PDL/DL member discovery runs only on the first successful sync of each PDL.
- Discovery completion is persisted per PDL in `SyncState`.
- A newly added PDL is discovered on its first sync.
- A failed initial discovery is retried on the next sync.
- Normal/background syncs do not repeat Outlook DL expansion.
- Existing manual Team Management mappings remain authoritative and continue to work.
- Automatically discovered members are inserted into the canonical `PDL - <PDL Name>` group and receive PDL response authority.
- Discovered members are rebuilt into the responder cache before response correlation in the same sync.
- Inbox copies of replies from newly discovered responders are included in the same sync's response evidence pool.

### Sync performance/diagnostics
- Added persisted sync progress stages so a long Outlook sync no longer appears completely idle.
- Added explicit progress events for Outlook connection, folder scanning, candidate collection, response correlation, commit/reconciliation and completion/failure.
- Preserved batch database commits and non-overlapping sync behavior.
- Existing Team Management search by PDL name, engineer name and email is retained.

### Important behavior
Member discovery is intentionally not performed on every sync. If a PDL owner adds a member later, the normal Team Management/manual mapping path is used; no full Outlook discovery is triggered.


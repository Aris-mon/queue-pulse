# V10.20.21 — Graph Dedicated Mailbox Hardening

This release makes the supplied Microsoft Graph app-only mailbox flow the production
mailbox path.

## Authentication

The connector uses:

- `GRAPH_TENANT_ID`
- `GRAPH_CLIENT_ID`
- `GRAPH_CLIENT_SECRET` — **secret VALUE**, not Secret ID
- `GRAPH_MAILBOX`

Authentication is MSAL client credentials against:

`https://login.microsoftonline.com/<TenantID>`

with:

`https://graph.microsoft.com/.default`

No Outlook desktop sign-in is required.

## Mailbox synchronization

Graph reads:

- Inbox
- Sent Items (used as response evidence)
- all pages returned by Graph
- subject/body/body preview
- sender/from
- To/CC/BCC/reply-to
- received/sent/created/modified timestamps
- conversation ID
- Internet Message ID and relevant Internet headers
- attachment presence
- importance/read/draft/category metadata
- web link and parent folder ID

The existing `SYNC_DAYS` setting controls the mailbox history scanned by the application.
`SYNC_DAYS` is the application history window for the normal sync path. Keep it
explicit and appropriate for the mailbox size.

Graph 429/5xx/network failures are retried with bounded backoff.

## PDL member discovery

Initial discovery remains once per PDL.

Nested PDL/group membership is recursively expanded, so:

```text
PDL-A
  PDL-B
    Engineer B
```

causes Engineer B to be added to PDL-A's effective membership and Team Management group.

Recurring mailbox sync does not repeatedly expand PDL membership.

## Response correlation

Graph messages are adapted into the same message contract used by the existing
intelligence pipeline. Sender SMTP address is normalized and PDL access remains the
authoritative responder permission.

This keeps automatically discovered and manually mapped engineers on the same
response-correlation path.

## Outlook dependency

When `GRAPH_MAILBOX` is configured, sync and discovery do not initialize Outlook/COM.
The legacy connector code remains only as a compatibility fallback if Graph is not
configured.

The production Graph deployment no longer needs Outlook installed or running.

## Verification

Use the authenticated:

```text
/api/graph/test
```

endpoint before running Sync. A successful result confirms the application can access
the dedicated mailbox Inbox through Graph.

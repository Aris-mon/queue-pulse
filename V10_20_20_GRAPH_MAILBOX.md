# V10.20.21 — Microsoft Graph Dedicated Mailbox Release

## Mailbox authentication

Production mailbox synchronization now uses Microsoft Graph app-only authentication.

Set these environment variables:

```env
GRAPH_TENANT_ID=<Tenant ID>
GRAPH_CLIENT_ID=<Application / Client ID>
GRAPH_CLIENT_SECRET=<Client Secret VALUE>
GRAPH_MAILBOX=<dedicated mailbox SMTP address>
```

Do not use the Secret ID as `GRAPH_CLIENT_SECRET`.

### Required Microsoft Graph application permissions

The Entra app registration needs:

- `Mail.Read`
- `Group.Read.All`
- `GroupMember.Read.All`

Grant tenant admin consent.

For least privilege, Exchange/Entra administrators should restrict the app's mailbox access to the dedicated mailbox according to the tenant's current Exchange application-access/RBAC controls.

## Local Windows update

1. Stop the application.
2. Replace the application files with this release.
3. Create/update `.env` from `.env.example`.
4. Enter the real Tenant ID, Client ID, Client Secret VALUE and mailbox SMTP address.
5. Run:

```powershell
python app.py
```

The launcher will install the updated requirements.

Verify:

```text
/api/graph/test
```

The endpoint requires application login and should return `success: true`.

Then run the normal **Sync**.

## Secret rotation

When the secret expires:

1. Entra ID -> App registrations -> application -> Certificates & secrets.
2. Create a new client secret.
3. Copy the **Value** immediately.
4. Update `GRAPH_CLIENT_SECRET`.
5. Restart the application.
6. Test `/api/graph/test`.
7. Revoke/delete the old secret after validation.

Never commit `.env` or the secret to source control.

## PDL membership discovery

Initial PDL synchronization now performs Graph group expansion once.

Nested PDLs/groups are recursively expanded:

```text
PDL-A
  Engineer A
  PDL-B
    Engineer B
    PDL-C
      Engineer C
```

becomes effective PDL-A membership:

```text
Engineer A
Engineer B
Engineer C
```

The canonical Team Management group is created as:

```text
PDL - PDL-A
```

Normal mailbox synchronization does not repeat Graph membership discovery.

If a PDL owner later adds a member manually, the normal Team Management/manual mapping path remains available.

## Response correlation

Responder identity is based primarily on normalized SMTP address and explicit PDL access.

The same authority is used for:

- automatically discovered members
- manually mapped engineers
- Team Management PDL access
- response correlation
- database response reconciliation

This prevents a discovered PDL engineer from being incorrectly treated as generic internal mail.

## TAT notifications

Individual email records continue to store:

```text
tat_breached = true
```

but Notification Center receives a PDL summary:

```text
PDL ABC — 12 emails breached TAT
```

If three additional emails breach TAT:

```text
PDL ABC — 3 new emails breached TAT (total 15)
```

The underlying breached email rows remain available under Alerts.

Existing unread per-email TAT alerts are converted to audit history on the next sync so they no longer flood the notification feed.

## AI

Mailbox synchronization remains independent of the local Qwen/Ollama worker. Graph import should complete without waiting for CPU inference; AI enrichment is processed through the existing queue/dispatcher.

## Outlook dependency

When `GRAPH_MAILBOX` is configured, the sync path uses Graph app-only authentication
and does not initialize Outlook/COM. Outlook does not need to be installed or running.

The legacy Outlook connector remains only as a compatibility fallback when Graph is
not configured.

## Mailbox data

The Graph connector follows Graph pagination and imports the fields required by the
existing intelligence pipeline: body, sender/from, To/CC/BCC/reply-to, timestamps,
conversation ID, Internet Message ID/headers, attachment presence and standard
message metadata.

`SYNC_DAYS` controls the initial/history window. Keep `SYNC_DAYS` explicit and appropriate for the mailbox size.


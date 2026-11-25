# V10.20.22 — Graph Mailbox UI Configuration

## Changes

- Added first-run Microsoft Graph mailbox configuration to the initial setup wizard.
- Added **Settings → Microsoft Graph Mailbox** for administrators to test/update/remove configuration.
- Tenant ID, Application/Client ID and dedicated mailbox are stored as application configuration instead of requiring `.env` edits.
- Client Secret VALUE is encrypted locally and is never rendered back to the browser.
- Existing `.env` Graph values remain supported as a migration/backward-compatibility fallback.
- Graph mailbox synchronization remains Graph app-only/client-credentials based.
- PDL nested-group discovery remains initial-sync-only per PDL.
- No Outlook/COM dependency is required when Graph is configured.
- Added `cryptography` dependency.

## First setup

Enter:

1. Tenant ID
2. Application / Client ID
3. Client Secret VALUE (not Secret ID)
4. Dedicated mailbox SMTP address

The setup validates Graph access before creating the administrator account.

## Existing installations

Sign in as an administrator and open:

`Settings → Microsoft Graph Mailbox`

Click **Test Connection**, then **Save Mailbox Configuration**.

A blank secret while editing means keep the currently stored secret.

## Security

The secret is encrypted under:

`instance/config/.graph_mailbox.key`

and stored encrypted in:

`instance/config/graph_mailbox.json`

Protect the `instance/config` directory and include it in backup/restore procedures. Do not commit it to source control.

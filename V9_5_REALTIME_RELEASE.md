# PDL Email Intelligence V9.5.0

## Main release

V9.5 adds a near-real-time operational layer and the animated PDL mascot.

### Real-time monitoring
- Browser polls `/api/v1/live` every 15 seconds.
- Outlook auto-sync remains controlled by the Admin page.
- Recommended Outlook sync interval is 1 minute.
- The browser never calls Outlook directly.
- Live counts cover the last 24 hours.
- Critical/high priority and operational alerts can trigger browser desktop notifications.
- Users can explicitly enable browser notifications from the top bar.

### AI Command Center
- New `/ai-command-center` page.
- Live critical/high/TAT/AI metrics.
- AI Attention Required list.
- AI daily briefing.
- Shift handover snapshot.
- Latest mailbox activity.

### Engineer functionality
- Engineer Map route and backend functionality remain unchanged.
- Engineer Map navigation is intentionally hidden from the normal sidebar.
- AI Command Center occupies the sidebar slot instead.

### Mascot
- `static/images/pdl-mascot.gif` is a real animated GIF.
- 12-frame subtle vertical motion loop.
- HPE green background normalized to `#01A982`.
- Animation is intentionally subtle so it does not distract users.

### Time handling
- Existing V9.3 Outlook wall-clock/IST correction is retained.

## Activation
1. Install requirements in the existing `.venv`.
2. Configure `.env` as before.
3. In Admin, enable Auto-sync Outlook.
4. Set sync interval to 1 minute for near-real-time collection.
5. In each browser, click `Enable alerts` once if desktop priority notifications are desired.

## Important architecture note
The 15-second browser polling is not a mailbox polling mechanism. It only polls the application's database/API. The mailbox collector still determines how quickly new Outlook messages enter the database.

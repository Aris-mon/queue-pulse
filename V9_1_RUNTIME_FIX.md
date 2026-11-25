# V9.1 Runtime Fix

This maintenance revision corrects the HTTP 500 issue found immediately after the Master Prompt v9 integration.

## Root cause corrected

1. `enqueue_unanalyzed_ai()` was accidentally decorated with `@app.context_processor`. Flask context processors must return dictionaries, while this queue helper returns an integer count. The decorator has been removed from the queue helper and applied to the intended `inject_globals()` function.
2. The custom 403/404/500 handlers referenced `templates/error.html`, but that template was absent. A standalone error template is now included.
3. The 500 handler now has a last-resort inline response so a secondary template error cannot mask the original application exception.

No Outlook mailbox logic, PDL configuration, TAT calculation, or AI classification rules were removed by this fix.

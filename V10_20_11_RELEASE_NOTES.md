# PDL Email Intelligence V10.20.11 — All Known Bug Fixes

## Included fixes

1. **Monitoring slot report background URL crash fixed**
   - Removed `url_for()` from the background scheduler path.
   - Uses `MONITORING_APP_URL`, administrator network URL, or localhost fallback.
   - Eliminates `RuntimeError: Unable to build URLs outside an active request`.

2. **AI queue / zero analyzed problem fixed**
   - Every customer email gets an immediate deterministic/hybrid AI analysis record, so Administration no longer remains at `0` while Qwen is processing.
   - Qwen enriches the same record asynchronously.
   - Worker failures are written to Events & Logs instead of disappearing.
   - Actual in-process queue depth is exposed to Live Operations.
   - Duplicate concurrent queue submissions are prevented.

3. **Qwen3 CPU performance improved**
   - Local prompt body reduced to the latest relevant message content.
   - Ollama context reduced to 2048 and default output budget to 160 tokens.
   - This avoids unnecessary generation of long JSON and quoted Outlook history.

4. **Automatic PDL member discovery**
   - A sender is auto-mapped to a PDL only when Outlook proves a real reply to that PDL/customer thread.
   - Newly discovered responders are immediately eligible for response/TAT detection in the same sync.
   - Internal service accounts are supported.
   - Arbitrary internal CC/To addresses are **not** auto-added.
   - Existing manual Team Management mapping remains the fallback.
   - Detected/mapped members are visible in PDL/Engineer management.

5. **Response detection accuracy**
   - PDL authority is refreshed after discovery.
   - A response is recognized only from a PDL-authorized member, not from every internal `@hpe.com` address.
   - Service-account replies can therefore close the customer thread when they are proven PDL responders.

6. **Notifications**
   - Fast AI pass now publishes the same priority/TAT notifications immediately instead of waiting for the slow Qwen pass.
   - Existing in-app notification badge/API remains the source of truth.

7. **AI Evaluation / Train AI dropdown UI**
   - New Smart Filter-style controls, alignment, focus state, rounded borders and consistent dark-mode treatment.

8. **Events & Logs / Configured PDL collapse UI**
   - Consistent modern collapse headers and spacing retained and strengthened.

## Important behavior

The application remains hybrid: deterministic rules are the immediate operational safety layer; Qwen3/Ollama provides contextual enrichment asynchronously. This prevents Outlook monitoring, TAT tracking and notifications from being blocked by a slow local CPU model.

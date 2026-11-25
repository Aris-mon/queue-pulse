# V10.13.0 — Precision AI + Operations Reporting

## Included
- Reworked recognition detection to require substantive customer appreciation/praise or successful-completion context.
- Courtesy-only messages such as `Thank you`, `Thanks`, `Much appreciated`, `Noted` and similar are not Recognition.
- Recognition is customer-scoped for the Recognition operational category.
- New AI prompt explicitly analyzes the newest authored message, treats quoted history as context, and makes escalation contextual rather than keyword-driven.
- Added AI recognition/escalation fields and stronger merge safeguards.
- Replaced the AI Command Center navigation/page with Operations Reports; old `/ai-command-center` URL redirects to Reports.
- Added day-wise, slot-wise, PDL-wise and engineer-wise reporting with response, pending, TAT breach, response-rate and compliance metrics.
- Reporting resolves the engineer assignment from assignment history at the time the email was received.
- Added professional, restrained page/card/interaction animations with reduced-motion support.
- Fixed the HPE rail logo presentation by rendering the supplied transparent GIF crisply as a white enterprise mark.
- Added regression tests for courtesy-vs-recognition precision.

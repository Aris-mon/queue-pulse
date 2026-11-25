# V10 Team Management, Monitoring Slots & PDL-Aware Responses

Implemented on top of V9.6.1 Response Finalization.

## Team Management
- Team Management navigation and admin page.
- Create team groups with group name and manager.
- Open a group to add/create engineers with name, email, team and PDL access.
- PDL access is additive and can cover multiple PDLs.
- Advanced options provide monitoring slot creation and group deletion.

## Monitoring slots
- Slots have a name, start time and end time (IST display/measurement).
- PDL detail Actions menu lists eligible slots and engineers.
- Assignment requires the engineer to be in the slot's group and have explicit PDL access.
- PDL detail shows slot tracking: received, attended/responded, unattended and TAT breached.
- Overnight slots are supported.

## Executive groups
- Create separate leadership-only executive groups.
- Add manager/executive contacts to a group.
- Existing intelligence notification behavior is retained: leadership recipients create notifications when context warrants attention, not merely because a leader is copied.
- Notification messages include AI context/category, sentiment and severity.

## PDL-member-aware response finalization
- Sent Items response matching now accepts only responders authorized for the email's PDL.
- Engineer response copies are also restricted by explicit PDL access when available.
- Existing legacy EngineerGroup-to-PDL mapping remains a compatibility fallback.
- When a configured PDL engineer responds, the original customer email becomes Responded and its response/TAT calculations use the first valid response.

## Database
New SQLAlchemy tables are created automatically by `db.create_all()`:
- team_groups
- team_group_engineer
- team_group_pdl
- engineer_pdl_access
- monitoring_slots
- pdl_monitoring_assignments
- executive_groups
- executive_group_member

No existing email records are deleted by these additions.

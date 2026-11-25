# V10.13.6 — Configured Engineer Workload Exclusion Fix

## Root cause
The previous release treated an engineer as workload-exempt only when that engineer had response authority for the specific PDL. That was correct for identifying a valid response, but incorrect for identifying whether the sender is a customer-workload item. A configured engineer could therefore be classified as `internal`, and action words such as `please confirm`, `required`, `schedule`, or `urgent` could make the message `response_required=True`.

## Correct model
Engineer identity and PDL response authority are now separate:

- Any active engineer configured in the tool is classified as `sender_type=engineer`.
- Configured engineer messages are always `response_required=False` and `monitoring_excluded=True`.
- PDL membership/response authority is still required for that engineer's message to count as valid response evidence against a customer thread.
- Engineer messages are NOT discarded from intelligence processing. They can still generate operational/critical event intelligence and critical notifications.
- Existing database rows are corrected at startup by matching sender_email against active configured engineers.

## Examples

`Engineer A -> "Please confirm the schedule and required details."`

- Response required: NO
- Customer workload: NO
- TAT workload: NO
- Can be analyzed for operational risk: YES

`Engineer A -> "I could not join the customer call and the deployment is blocked."`

- Response required: NO
- Customer workload: NO
- TAT workload: NO
- Operational event: YES
- Critical notification: YES when the critical-event resolver confirms the impact

## Validation
19 intelligence tests pass after this change.

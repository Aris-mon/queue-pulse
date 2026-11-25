# V10.6 Response Tracking + Metrics

## Changes

- Engineer replies are treated as response evidence, not action items.
- Team Management PDL access is authoritative for response recognition.
- Legacy EngineerGroup PDL mappings remain supported for backward compatibility.
- Engineer replies are not imported as workload rows; they are monitored internally to update the original customer email.
- Responded emails are hidden from the default Email Intelligence actioning list. Use Status = Responded to review them.
- Added explicit internal-tool sender exclusion for `deployment.management-system@hpe.com`.
- Added 1H, 4H and 8H metric periods alongside 24H, Week, Month and Custom.

## Response rule

If an inbound email belongs to a PDL and a configured engineer with PDL authority replies, the original email is marked responded, with response timestamp and engineer attribution. Monitoring-slot assignment does not limit response authority.

## Internal tool email

Messages sent by `deployment.management-system@hpe.com` are ignored by workload/action processing.

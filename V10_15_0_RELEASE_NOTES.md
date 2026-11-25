# V10.15.0 — Manager Scope + Qwen Stability

## Access control
- Added Manager role in addition to System Administrator and Analyst.
- System Administrators retain tenant-wide visibility and Administration access.
- Managers are assigned one or more Team Groups from Users.
- Managers see only PDLs associated with their assigned Team Groups.
- Managers can open and edit only their assigned Team Groups.
- Managers cannot access Administration, Users, PDL administration, Events, Executive Groups, or delete PDLs/teams.
- Managers cannot browse or modify engineers belonging exclusively to other managers' teams.
- Analyst behavior remains PDL-assignment based.
- Existing databases are upgraded automatically by SQLAlchemy create_all() with the new user_team_group association table.

## PDL detail period
- PDL detail explicitly defaults to Last 24 hours.
- Week/Month/Custom are opt-in only.
- Monitoring slot activity now also defaults to Last 24 hours.

## Qwen/Ollama reliability
- Plain JSON output replaces schema-constrained generation for Qwen3 on CPU.
- Effective Qwen3 timeout is at least 300 seconds.
- keep_alive default is 60m; context is bounded to 4096 and output to 180 tokens.
- Qwen is called only for ambiguous/high-impact/contextual emails.
- High-confidence routine emails are completed by deterministic guardrails without being recorded as AI errors.
- Existing failed recent analyses can be reprocessed without blocking dashboard operation.
- Qwen remains an asynchronous enrichment layer; workload/TAT/identity/team access remain deterministic.

## Validation
- Python compilation: PASS
- Existing regression suite: 32/32 PASS

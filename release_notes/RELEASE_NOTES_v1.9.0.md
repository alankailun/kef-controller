# KEF Controller 1.9.0 - Controller Internals Simplified

Release date: 2026-07-30

## Improved

- Controller state is now grouped by responsibility: speaker identity,
  runtime speaker data, power actions, Windows events, connection ownership,
  and background workers. Existing lock order and worker behavior are
  preserved.
- Structured logs now support immutable bound action context. A log action
  binds its action, generation, and reason once; conflicting fields are
  rejected instead of silently changing log context.
- All redundant current-time `mono` arguments were removed. The centralized
  structured logger supplies the timestamp, while event, deadline, and measured
  duration timestamps remain explicit.
- The ordinary verified standby and end-session paths are direct, readable
  flows. Only the two genuinely distinct fast standby paths retain a policy
  object.

## Maintenance

- Removed legacy duplicated controller state fields and their scattered
  initialization.
- Simplified standby logging and configuration lookup helpers.
- Clarified UI polling retry error handling and removed an unused connector
  import.

## Verification

- Added a regression test for bound structured-log context and centralized
  timestamp injection.
- Static analysis: Ruff passed.
- Full unit suite: 239 tests passed.

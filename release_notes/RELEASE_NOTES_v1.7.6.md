# KEF Controller 1.7.6 - Persistent Standby Connections

Release date: 2026-07-20

## Improved

- Reworked the prewarmed standby pool into a true persistent HTTP connection
  pool. Its two sockets now remain open across five-second health checks instead
  of being deliberately closed and recreated after every successful check.
- Health checks completely consume chunked or Content-Length HTTP responses
  before returning a socket to the pool, making the connection safe for the
  next heartbeat or an urgent standby request.
- A failed heartbeat now closes and replaces only the affected connection. The
  other verified hot connection remains available for shutdown, sleep,
  lid-close, lock, and display-off actions.
- Periodic steady-state connection churn is eliminated. New TCP connections are
  now created only during initial pool setup, after a connection failure, or to
  replenish a socket consumed by a standby action.

## Reliability

- Retains the shutdown deadline isolation introduced in 1.7.5: the prewarmed
  stage cannot consume the independent fire-and-forget fallback's reserved send
  window.
- The pool never publishes a half-connected socket. Replacement connections are
  added only after TCP connection setup succeeds.
- Verified against an LS50 Wireless II: four consecutive health checks reused
  the same two local TCP ports without reconnecting.

## Verification

- Added loopback coverage for persistent chunked-response reuse, connection
  replacement requested by the server, and isolation of the healthy peer when
  one heartbeat fails.
- Passed the complete automated test suite and Ruff static checks before
  packaging.

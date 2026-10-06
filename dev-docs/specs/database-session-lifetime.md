# Database session lifetime during synchronization

Authentication and permission checks finish before database connections are
released. Share metadata and file bytes retain their existing response shapes,
authorization rules, ETags, and error codes.

Read-only share requests materialize their response data and return the database
connection before object storage access or response transmission. Relay token
issuance persists its audit record before returning the connection. Liveness and
cached Prometheus exposition must remain available when synchronous request
workers are occupied waiting for database connections.

Upload body reception must not occupy a database connection. Conditional writes
still serialize the precondition check and mutation under the share row lock;
the existing atomicity guarantee must not be weakened to shorten transactions.

Regression checks use a real, one-connection SQLAlchemy QueuePool and assert
availability during storage access, rather than mocking Session.close. A second
check occupies the synchronous worker limiter and requests liveness and metrics.

Password verification and billing HTTP calls return their read connection first.
PostgreSQL retains the existing five persistent plus ten overflow connection
budget; `DATABASE_POOL_TIMEOUT_SECONDS` bounds checkout waits to five seconds by
default. Raising the budget would only postpone exhaustion and multiply the
connection cost across request workers. Readiness continues to check the database
and report 503 when it is unavailable; liveness does not claim database readiness.

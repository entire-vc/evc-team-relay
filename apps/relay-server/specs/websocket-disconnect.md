# WebSocket disconnect lifecycle

The relay's `/d/:doc_id/ws/:doc_id` and legacy WebSocket routes retain a
`DocConnection` only while their socket is usable. End-of-stream, a transport
error, a Close frame, token expiration, and server cancellation terminate the
receive task. An exhausted stream must not disable a select branch and leave
the task waiting indefinitely for server cancellation.

Disconnect releases the connection's document/awareness subscriptions and
removes only that connection from the event sender's weak-reference registry.
The connection gauge, per-document connection count, active-document gauge,
and closed counter are updated without waiting for another document event.
Other connections on the same document remain registered and continue to
receive and apply sync updates. The outbound socket task must finish as its
senders are released; transport send errors terminate it.

No message format, authentication requirement, endpoint, or client behavior
changes. MCP and OpenClaw consumers require no version or contract changes.

Regression checks exercise EOF with cancellation still pending, transport
failure with a pending stream, Close and server cancellation, weak-reference
release, metrics, and a connected peer's sync traffic after another peer exits.
Deployment verification uses an isolated document and connection churn, plus
before/after production socket and connection metrics. A service restart alone
does not establish the absence of the leak.

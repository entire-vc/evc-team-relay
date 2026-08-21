"""Regression coverage for the `client` fixture's rate-limiter reset.

Context (TR·control-plane, Mesh #c3acaa8d): the reset in `conftest.py` used to
enumerate limiters by hand and missed two routers — `web` and `webhooks` — so
their counters accumulated state ACROSS tests instead of being cleared before
each one. The `webhooks` limiter is the most dangerous instance of this: its
`create_webhook` endpoint is capped at 10/HOUR, and a ~6 minute full test run
can never let an hour-scoped window recover on its own. Once enough webhook
tests run in one session, every later one starts failing with 429 —
deterministically, not flakily.

This file has two jobs:
  1. Prove the hole with a negative control that fails against the OLD
     (hand-enumerated) limiter list and passes once the list is collected by
     introspection instead.
  2. Guard against the fix regressing back into a hand-maintained list: a new
     router that declares its own `Limiter` must show up in the reset without
     anyone touching `conftest.py`.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _admin_token(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        json={"email": "bootstrap@example.com", "password": "super-secret"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _create_webhook(client: TestClient, token: str, name: str) -> int:
    """POST /v1/webhooks once and return the status code."""
    response = client.post(
        "/v1/webhooks",
        json={
            "name": name,
            "url": "https://example.com/hook",
            "events": ["share.created"],
        },
        headers=_auth_headers(token),
    )
    return response.status_code


def test_webhooks_limiter_first_of_pair(client: TestClient) -> None:
    """First half of the cross-test 10/hour exhaustion pair.

    Issues 10 webhook creations — exactly the `webhooks.limiter` budget for
    the hour-long window declared on `POST /v1/webhooks` (`app/api/routers/
    webhooks.py:38`, `@limiter.limit("10/hour")`). All 10 must succeed inside
    a single test; this alone says nothing about cross-test isolation.
    """
    token = _admin_token(client)
    statuses = [_create_webhook(client, token, f"hook-{i}") for i in range(10)]
    assert statuses == [201] * 10, statuses


def test_webhooks_limiter_second_of_pair(client: TestClient) -> None:
    """Second half of the pair — this is the actual regression check.

    If `webhooks.limiter` was NOT reset between tests (the bug), the 10/hour
    bucket used up by `test_webhooks_limiter_first_of_pair` is still hot, and
    the very first request here comes back 429 instead of 201 — a same-IP,
    same-process leak that has nothing to do with this test's own traffic.

    Test ordering is not guaranteed by pytest by default, but this repo runs
    tests in file-definition order (no random-order plugin configured), so
    the pair above always executes immediately before this one.
    """
    token = _admin_token(client)
    status = _create_webhook(client, token, "hook-after-reset")
    assert status == 201, (
        f"expected 201 (fresh 10/hour budget after the client fixture's reset), "
        f"got {status} — the webhooks.limiter state leaked in from the previous test"
    )


def test_collected_limiters_cover_every_declared_limiter() -> None:
    """Anti-regression: the reset list must be derived, not hand-maintained.

    Walks `app.api.routers.*` + `app.main` for every module-level `Limiter`
    instance and asserts it is exactly the set the `client` fixture resets.
    A future router that declares `limiter = Limiter(...)` and is missed by
    the reset fails THIS test, not a flaky cross-test 429 three files away.
    """
    import pkgutil

    from slowapi import Limiter

    import app.api.routers as routers_pkg
    import app.main as main_module
    from tests.conftest import _collect_rate_limiters

    declared: set[int] = set()
    prefix = f"{routers_pkg.__name__}."
    for module_info in pkgutil.iter_modules(routers_pkg.__path__, prefix=prefix):
        module = __import__(module_info.name, fromlist=["_"])
        for value in vars(module).values():
            if isinstance(value, Limiter):
                declared.add(id(value))
    for value in vars(main_module).values():
        if isinstance(value, Limiter):
            declared.add(id(value))

    collected = _collect_rate_limiters()
    collected_ids = {id(lim) for lim in collected}

    assert declared, "sanity check: introspection must find at least one Limiter"
    assert collected_ids == declared, (
        "the client fixture's collected limiter set does not match every "
        "module-level Limiter declared under app.api.routers + app.main — "
        "a router's rate limiter would silently accumulate state across tests"
    )

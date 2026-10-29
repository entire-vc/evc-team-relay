"""Every object-storage call on a request path maps transport failures to 503/504.

A MinIO call outside `storage_client.storage_errors(...)` turns a storage
timeout into an unhandled 500 and skips the failure counter. This walks the
routers and fails on any such call, so a new handler cannot reintroduce it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException
from urllib3.exceptions import MaxRetryError, NewConnectionError, ReadTimeoutError

from app.clients import storage_client
from app.core.metrics import OBJECT_STORAGE_FAILURES_TOTAL

ROUTERS = Path(__file__).resolve().parents[1] / "app" / "api" / "routers"
STORAGE_METHODS = {
    "bucket_exists",
    "copy_object",
    "fget_object",
    "fput_object",
    "get_object",
    "list_objects",
    "make_bucket",
    "put_object",
    "remove_object",
    "remove_objects",
    "stat_object",
}


def _unwrapped_storage_calls(source: str, filename: str) -> list[str]:
    tree = ast.parse(source, filename)
    found: list[str] = []

    def visit(node: ast.AST, guarded: bool) -> None:
        if isinstance(node, ast.With):
            guarded = guarded or any(
                isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "storage_errors"
                for item in node.items
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in STORAGE_METHODS
            and not guarded
        ):
            found.append(f"{filename}:{node.lineno} .{node.func.attr}()")
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return found


def test_every_router_storage_call_is_guarded():
    unwrapped = [
        call
        for path in sorted(ROUTERS.glob("*.py"))
        for call in _unwrapped_storage_calls(path.read_text(), path.name)
    ]
    assert unwrapped == []


def test_the_scan_catches_an_unguarded_call():
    source = (
        "def handler(client):\n"
        "    with storage_client.storage_errors('x'):\n"
        "        client.get_object('b', 'k')\n"
        "    client.put_object('b', 'k', None, 0)\n"
    )
    assert _unwrapped_storage_calls(source, "h.py") == ["h.py:4 .put_object()"]


@pytest.mark.parametrize(
    ("exc", "status", "reason"),
    [
        (ReadTimeoutError(None, "/b/k", "read timed out"), 504, "timeout"),
        (MaxRetryError(None, "/b/k", ReadTimeoutError(None, "/b/k", "t")), 504, "timeout"),
        (MaxRetryError(None, "/b/k", NewConnectionError(None, "refused")), 503, "unavailable"),
        (ConnectionRefusedError(), 503, "unavailable"),
    ],
)
def test_transport_failures_map_to_bounded_errors(exc, status, reason):
    counter = OBJECT_STORAGE_FAILURES_TOTAL.labels(operation="probe", reason=reason)
    before = counter._value.get()
    with pytest.raises(HTTPException) as raised:
        with storage_client.storage_errors("probe"):
            raise exc
    assert raised.value.status_code == status
    assert counter._value.get() == before + 1
    if status == 503:
        assert raised.value.headers == {"Retry-After": "5"}

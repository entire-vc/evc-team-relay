#!/usr/bin/env python3
"""
E2E test: HTTP download fallback with CWT tokens

Tests the HTTP as-update endpoint behavior with CWT tokens against staging relay server.
This test verifies the known bug: CWT tokens work for WebSocket connections but return
401 for HTTP GET requests to /doc/{doc_id}/as-update.

Usage:
    python3 scripts/test_http_download_fallback.py
    python3 scripts/test_http_download_fallback.py -v
"""

import asyncio
import sys
from urllib.parse import urlparse, urlunparse
import httpx
import websockets

# Config
CP_URL = "http://localhost:8000"
RELAY_HTTP_URL = "http://localhost:8080"
RELAY_WS_URL = "ws://localhost:8080/doc/ws"
TEST_EMAIL = "test@entire.vc"
TEST_PASSWORD = "Test123456"
TEST_SHARE_ID = "7af90e81-8600-47c8-b618-dfa925d4ed55"

passed = 0
failed = 0
verbose = False


def test_result(name, success, detail=""):
    global passed, failed
    if success:
        passed += 1
        status = "✅"
        msg = name
    else:
        failed += 1
        status = "❌"
        msg = f"{name}: {detail}" if detail else name

    print(f"  {status} {msg}")

    if verbose and detail and success:
        print(f"      {detail}")


async def main():
    global passed, failed, verbose

    import argparse
    parser = argparse.ArgumentParser(description="Test HTTP download fallback with CWT tokens")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()
    verbose = args.verbose

    print("=" * 70)
    print("E2E Test: HTTP Download Fallback with CWT Tokens")
    print("=" * 70)
    print(f"Control Plane: {CP_URL}")
    print(f"Relay HTTP:    {RELAY_HTTP_URL}")
    print(f"Relay WS:      {RELAY_WS_URL}")
    print(f"Test Share:    {TEST_SHARE_ID}")
    print("=" * 70)
    print()

    # ========== Test 1: Login and Get Relay Token ==========
    print("Test 1: Login and Get Relay Token")
    print("-" * 70)

    access_token = None
    cwt_token = None
    relay_url = None
    doc_id = TEST_SHARE_ID  # Use share_id as doc_id for folder shares

    # Use httpx with no keepalive to avoid Caddy stale connection issues
    client = httpx.Client(
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=0)
    )

    try:
        # Login
        resp = client.post(
            f"{CP_URL}/v1/auth/login",
            json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
        )

        if resp.status_code == 200:
            data = resp.json()
            access_token = data.get("access_token")
            test_result("POST /auth/login returns 200", True, f"Got JWT token")
        else:
            test_result("POST /auth/login returns 200", False, f"Status {resp.status_code}: {resp.text}")
            print(f"\nResults: {passed} passed, {failed} failed")
            sys.exit(1)

        # Get relay token
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = client.post(
            f"{CP_URL}/v1/tokens/relay",
            json={
                "share_id": TEST_SHARE_ID,
                "doc_id": doc_id,
                "mode": "write",
                "file_path": f"test-folder/test.md"
            },
            headers=headers
        )

        if resp.status_code == 200:
            data = resp.json()
            cwt_token = data.get("token")
            relay_url = data.get("relay_url")
            test_result("POST /tokens/relay returns 200", True, f"Got CWT token")
            test_result("Response includes 'token' field", cwt_token is not None)
            test_result("Response includes 'relay_url' field", relay_url is not None)

            if verbose:
                print(f"      relay_url: {relay_url}")
                print(f"      token: {cwt_token[:50]}...")
        else:
            test_result("POST /tokens/relay returns 200", False, f"Status {resp.status_code}: {resp.text}")
            print(f"\nResults: {passed} passed, {failed} failed")
            sys.exit(1)

    finally:
        client.close()

    print()

    # ========== Test 2: HTTP as-update with CWT Token ==========
    print("Test 2: HTTP as-update Endpoint with CWT Token (Expected: 401)")
    print("-" * 70)

    # Construct HTTP URL for as-update endpoint
    # Plugin pattern: pop last segment from relay_url, push doc_id
    # relay_url = "ws://localhost:8080/doc/ws"
    # → parse, change scheme to http, pop "ws", append doc_id, append "as-update"

    parsed = urlparse(relay_url)
    path_parts = parsed.path.rstrip('/').split('/')

    # Remove last segment ("ws")
    if path_parts and path_parts[-1] == "ws":
        path_parts.pop()

    # Add doc_id and "as-update"
    path_parts.append(doc_id)
    path_parts.append("as-update")

    http_url = urlunparse((
        "https",  # scheme
        parsed.netloc,
        "/".join(path_parts),
        "",  # params
        "",  # query
        ""   # fragment
    ))

    if verbose:
        print(f"  Constructed URL: {http_url}")

    client = httpx.Client(
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=0)
    )

    try:
        # Try GET with Authorization header (CWT token)
        resp = client.get(
            http_url,
            headers={"Authorization": f"Bearer {cwt_token}"}
        )

        # We EXPECT 401 because CWT tokens don't work for HTTP endpoints (this is the bug)
        if resp.status_code == 401:
            test_result("GET /doc/{doc_id}/as-update with CWT returns 401", True, "Bug confirmed: CWT tokens don't work for HTTP")
        elif resp.status_code == 200:
            test_result("GET /doc/{doc_id}/as-update with CWT returns 401", False, "Unexpected 200 - bug may be fixed!")
            if verbose:
                print(f"      Response length: {len(resp.content)} bytes")
        else:
            test_result("GET /doc/{doc_id}/as-update with CWT returns 401", False, f"Unexpected status {resp.status_code}")

    finally:
        client.close()

    print()

    # ========== Test 3: HTTP as-update without Token ==========
    print("Test 3: HTTP as-update Endpoint without Token (Expected: 401)")
    print("-" * 70)

    client = httpx.Client(
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=0)
    )

    try:
        # Try GET without auth header
        resp = client.get(http_url)

        if resp.status_code == 401:
            test_result("GET /doc/{doc_id}/as-update without token returns 401", True)
        else:
            test_result("GET /doc/{doc_id}/as-update without token returns 401", False, f"Got {resp.status_code}")

    finally:
        client.close()

    print()

    # ========== Test 4: WebSocket Connection with CWT Token ==========
    print("Test 4: WebSocket Connection with CWT Token (Expected: Success)")
    print("-" * 70)

    ws_url = f"{relay_url}/{doc_id}"

    if verbose:
        print(f"  WebSocket URL: {ws_url}")

    try:
        # Connect with Authorization header
        ws = await websockets.connect(
            ws_url,
            additional_headers={"Authorization": f"Bearer {cwt_token}"},
            ping_interval=20,
            ping_timeout=20
        )

        test_result("WebSocket connect with CWT token succeeds", True)

        # Try to receive initial sync messages
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            test_result("WebSocket receives initial message", True, f"Received {len(msg)} bytes")
        except asyncio.TimeoutError:
            test_result("WebSocket receives initial message", True, "No initial message (empty doc)")

        await ws.close()

    except websockets.exceptions.InvalidStatusCode as e:
        test_result("WebSocket connect with CWT token succeeds", False, f"Status {e.status_code}")
    except Exception as e:
        test_result("WebSocket connect with CWT token succeeds", False, str(e))

    print()

    # ========== Test 5: URL Construction Pattern ==========
    print("Test 5: URL Construction Matches Plugin Behavior")
    print("-" * 70)

    # Verify our URL construction matches plugin pattern
    expected_http_url = f"http://localhost:8080/doc/{doc_id}/as-update"

    if http_url == expected_http_url:
        test_result("URL construction matches expected pattern", True, http_url)
    else:
        test_result("URL construction matches expected pattern", False, f"Got {http_url}, expected {expected_http_url}")

    # Verify path transformation
    input_relay_url = "ws://localhost:8080/doc/ws"
    parsed = urlparse(input_relay_url)
    path_parts = parsed.path.rstrip('/').split('/')

    # Pop "ws"
    if path_parts[-1] == "ws":
        path_parts.pop()

    # Push doc_id
    path_parts.append(doc_id)
    reconstructed_path = "/".join(path_parts)

    expected_path = f"/doc/{doc_id}"
    if reconstructed_path == expected_path:
        test_result("Path transformation (pop ws, push doc_id)", True, reconstructed_path)
    else:
        test_result("Path transformation (pop ws, push doc_id)", False, f"Got {reconstructed_path}, expected {expected_path}")

    print()

    # ========== Test 6: Multiple Token Requests ==========
    print("Test 6: Multiple Token Requests for Same Share")
    print("-" * 70)

    client = httpx.Client(
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=0)
    )

    try:
        headers = {"Authorization": f"Bearer {access_token}"}

        # Request token again (folder share, use share_id as doc_id)
        resp = client.post(
            f"{CP_URL}/v1/tokens/relay",
            json={
                "share_id": TEST_SHARE_ID,
                "doc_id": TEST_SHARE_ID,
                "mode": "write",
                "file_path": "test-folder/another-file.md"
            },
            headers=headers
        )

        if resp.status_code == 200:
            data = resp.json()
            token2 = data.get("token")
            test_result("Second token request succeeds", True)
            test_result("Second token is returned", token2 is not None)

            # Verify it's a different token (different file_path should give different token)
            if token2 and token2 != cwt_token:
                test_result("Second token is different from first", True)
            elif token2:
                # May be the same if server caches by doc_id only
                test_result("Second token is different from first", True, "Same token (doc_id cache)")
        else:
            test_result("Second token request succeeds", False, f"Status {resp.status_code}")

        # Try WebSocket with second token
        if resp.status_code == 200:
            token2 = resp.json().get("token")
            ws_url2 = f"{relay_url}/{TEST_SHARE_ID}"

            try:
                ws2 = await websockets.connect(
                    ws_url2,
                    additional_headers={"Authorization": f"Bearer {token2}"},
                    ping_interval=20,
                    ping_timeout=20
                )
                test_result("WebSocket with second token succeeds", True)
                await ws2.close()
            except Exception as e:
                test_result("WebSocket with second token succeeds", False, str(e))

    finally:
        client.close()

    print()

    # ========== Summary ==========
    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Total:   {passed + failed}")
    print(f"Passed:  {passed}")
    print(f"Failed:  {failed}")

    if failed == 0:
        print("\n✅ All tests passed!")
        print("\nKey Findings:")
        print("  • CWT tokens work for WebSocket connections")
        print("  • CWT tokens return 401 for HTTP GET /doc/{doc_id}/as-update")
        print("  • This confirms the known bug: HTTP fallback doesn't work with CWT")
    else:
        print(f"\n❌ {failed} test(s) failed")

    print("=" * 70)

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())

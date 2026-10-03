"""Check the clean release stack. Run from infra: python3 ../scripts/release_smoke.py.

COMPOSE_FILE / COMPOSE_PROJECT_NAME may select an isolated local stack. No image
is built or published here; the release workflow selects its immutable images.
"""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

RELAY_URL = "ws://localhost:8080"
RELAY_AUDIENCE = "http://localhost:8080"

# Execute the HTTP requests inside the control-plane container: the release
# compose stack deliberately does not expose its API port on the runner host.
FETCH = """
import contextlib
import json
import sys
import urllib.request

# Settings validators may log to stdout. Keep diagnostics separate from the
# single JSON result rather than accepting arbitrary mixed output.
with contextlib.redirect_stdout(sys.stderr):
    from app.core.config import get_settings
    settings = get_settings()

def get(path):
    with urllib.request.urlopen('http://localhost:8000' + path, timeout=10) as response:
        return json.load(response)

print(json.dumps({
    'health': get('/health'),
    'server_info': get('/server/info'),
    'relay_audience': settings.effective_relay_audience,
}))
"""


def check_config(config):
    if config.get("server", {}).get("url") != RELAY_AUDIENCE:
        raise ValueError(f"relay.toml [server].url must equal {RELAY_AUDIENCE}")


def check_responses(payload):
    health = payload.get("health")
    if not isinstance(health, dict) or health.get("ok") is not True:
        raise ValueError("control-plane /health must report JSON ok:true")
    info = payload.get("server_info")
    if not isinstance(info, dict):
        raise ValueError("control-plane /server/info must report a JSON object")
    for field in ("id", "name", "version"):
        if not isinstance(info.get(field), str) or not info[field].strip():
            raise ValueError(f"control-plane /server/info must report a nonempty {field}")
    if info.get("relay_url") != RELAY_URL:
        raise ValueError(f"control-plane /server/info relay_url must equal {RELAY_URL}")
    features = info.get("features")
    if not isinstance(features, dict) or features.get("multi_user") is not True:
        raise ValueError("control-plane /server/info must report features.multi_user:true")
    if payload.get("relay_audience") != RELAY_AUDIENCE:
        raise ValueError(f"control-plane relay audience must equal {RELAY_AUDIENCE}")


def main():
    try:
        with Path("relay/relay.toml").open("rb") as f:
            check_config(tomllib.load(f))
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "control-plane", "python3", "-c", FETCH],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise ValueError("smoke response must be a JSON object")
        check_responses(payload)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"release smoke: FAIL — {error}", file=sys.stderr)
        return 1
    print('control-plane /health: {"ok":true}')
    info = payload["server_info"]
    print(
        "control-plane /server/info: "
        + json.dumps(
            {
                "id": info["id"],
                "version": info["version"],
                "relay_url": info["relay_url"],
                "multi_user": info["features"]["multi_user"],
            }
        )
    )
    print(f"relay audience: control-plane == relay.toml == {RELAY_AUDIENCE}")
    print("release smoke: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

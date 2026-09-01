#!/usr/bin/env python3
"""Install and enable the reviewed gambling pipe through Open WebUI's API.

The credential file is intentionally read only when the maintainer runs this
script. Passwords and session tokens are never printed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PIPE = ROOT / "openwebui" / "gambling_agent_pipe.py"
CREDS = Path.home() / "openUI"
BASE = os.environ.get("OPENWEBUI_URL", "http://127.0.0.1:8080").rstrip("/")
FUNCTION_ID = "gambling_agent"
ALLOWED_USER_ID = os.environ.get("GAMBLING_ALLOWED_USER_ID", "").strip()


def request_json(
    method: str,
    path: str,
    payload: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict | list | None]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(f"{BASE}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = {"detail": raw[:500]}
        return exc.code, detail
    except URLError as exc:
        raise RuntimeError(f"Cannot reach Open WebUI at {BASE}: {exc.reason}") from exc


def credentials() -> tuple[str, str]:
    if not CREDS.is_file():
        raise RuntimeError(f"Credential file not found: {CREDS}")
    lines = [
        line for line in CREDS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(lines) < 2:
        raise RuntimeError(
            "Credential file must contain password on the first non-empty line "
            "and username on the second non-empty line"
        )
    password, username = lines[0], lines[1]
    return username, password


def main() -> int:
    try:
        if not ALLOWED_USER_ID:
            raise RuntimeError(
                "GAMBLING_ALLOWED_USER_ID is not set. Refusing to grant "
                "model access without an explicit user."
            )
        username, password = credentials()
        status, result = request_json(
            "POST",
            "/api/v1/auths/signin",
            {"email": username, "password": password},
        )
        if status != 200 or not isinstance(result, dict) or not result.get("token"):
            print(f"Open WebUI sign-in failed ({status}). Check the admin account.", file=sys.stderr)
            return 1
        token = str(result["token"])

        content = PIPE.read_text(encoding="utf-8")
        form = {
            "id": FUNCTION_ID,
            "name": "Gamble — Gambling Dashboard Agent",
            "content": content,
            "meta": {
                "description": "Gamble helps clients with gambling-report-related changes and user testing.",
            },
        }

        status, existing = request_json(
            "GET", f"/api/v1/functions/id/{FUNCTION_ID}", token=token
        )
        if status == 200:
            status, _ = request_json(
                "POST", f"/api/v1/functions/id/{FUNCTION_ID}/update", form, token
            )
            action = "updated"
        else:
            status, _ = request_json(
                "POST", "/api/v1/functions/create", form, token
            )
            action = "installed"
        if status >= 400:
            print(f"Open WebUI Function {action} failed ({status}).", file=sys.stderr)
            return 1

        # Pipe functions appear as models. Grant only the configured account
        # read access, with no model-edit or administration rights.
        status, access = request_json(
            "POST",
            "/api/v1/models/model/access/update",
            {
                "id": FUNCTION_ID,
                "name": "Gamble — Gambling Dashboard Agent",
                "access_grants": [
                    {
                        "principal_type": "user",
                        "principal_id": ALLOWED_USER_ID,
                        "permission": "read",
                    }
                ],
            },
            token=token,
        )
        if status >= 400:
            detail = access.get("detail") if isinstance(access, dict) else access
            print(
                f"Gamble function updated, but model visibility could not be configured "
                f"({status}): {detail}",
                file=sys.stderr,
            )
            return 1

        active = bool(existing.get("is_active")) if isinstance(existing, dict) else False
        if not active:
            status, _ = request_json(
                "POST", f"/api/v1/functions/id/{FUNCTION_ID}/toggle", token=token
            )
            if status >= 400:
                print(f"Function installed but could not be enabled ({status}).", file=sys.stderr)
                return 1

        print(f"Gamble — Gambling Dashboard Agent {action} and enabled.")
        print("Start a new Open WebUI chat and select Gamble — Gambling Dashboard Agent.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""One-time Home Connect OAuth setup.

Generates an authorization URL, walks the user through the browser login,
exchanges the code for tokens, and stores the refresh token in JarvisStorage.

Usage:
    python homeconnect_scripts/authorize.py
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import sys
from pathlib import Path

# Add the repo root so homeconnect_shared is importable when run from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homeconnect_shared.homeconnect_client import (  # noqa: E402
    AUTHORIZE_URL,
    DEFAULT_SCOPES,
    exchange_authorization_code,
)

# Same redirect URI used by the mobile OAuth flow via jarvis-relay
REDIRECT_URI = "https://relay.jarvisautomation.io/oauth/bounce"

try:
    from jarvis_command_sdk import JarvisStorage
    _storage = JarvisStorage("homeconnect")
except Exception:
    _storage = None


def _encode_relay_state(csrf_token: str) -> str:
    """Encode state for the relay bounce endpoint.

    Format matches JCC's _encode_relay_state: base64url({"t": csrf, "r": redirect})
    """
    payload = json.dumps({"t": csrf_token, "r": "jarvis://auth-complete"})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _get_or_prompt(secret_key: str, prompt_text: str, sensitive: bool = False) -> str:
    """Read a secret from storage, or prompt the user."""
    if _storage:
        try:
            val = _storage.get_secret(secret_key, scope="integration")
            if val:
                masked = val[:4] + "..." if sensitive and len(val) > 4 else val
                print(f"  Using stored {secret_key}: {masked}")
                return val
        except Exception:
            pass

    val = input(f"  {prompt_text}: ").strip()
    if not val:
        print(f"\nError: {secret_key} is required.", file=sys.stderr)
        sys.exit(1)

    if _storage:
        try:
            _storage.set_secret(secret_key, val, scope="integration")
        except Exception:
            pass

    return val


def main() -> None:
    print("=" * 60)
    print("  Home Connect OAuth Setup")
    print("=" * 60)
    print()
    print("Prerequisites:")
    print("  1. Register at https://developer.home-connect.com/")
    print("  2. Create an application")
    print(f"  3. Set redirect URI to: {REDIRECT_URI}")
    print("  4. Note your Client ID and Client Secret")
    print()

    # Step 1: Get credentials
    print("Step 1: Credentials")
    client_id = _get_or_prompt(
        "HOME_CONNECT_CLIENT_ID", "Enter your Client ID", sensitive=False,
    )
    client_secret = _get_or_prompt(
        "HOME_CONNECT_CLIENT_SECRET", "Enter your Client Secret", sensitive=True,
    )

    # Step 2: Build auth URL with relay-encoded state
    print()
    csrf_token = secrets.token_urlsafe(32)
    state = _encode_relay_state(csrf_token)

    import urllib.parse
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": DEFAULT_SCOPES,
        "state": state,
    }
    auth_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    print("Step 2: Authorize in your browser")
    print()
    print(f"  Open this URL:\n")
    print(f"  {auth_url}\n")
    print("  Log in with your Home Connect account (same as the app).")
    print("  After login, the browser will try to open a jarvis:// link")
    print("  that won't load. Copy the FULL URL from the address bar.\n")

    redirect_input = input("Paste the redirect URL (or just the code): ").strip()

    # Extract the authorization code from the redirect URL
    if redirect_input.startswith("http") or redirect_input.startswith("jarvis://"):
        match = re.search(r"[?&]code=([^&]+)", redirect_input)
        if not match:
            print("\nError: Could not find 'code=' in the URL.", file=sys.stderr)
            sys.exit(1)
        authorization_code = match.group(1)
    else:
        authorization_code = redirect_input

    print("\nExchanging authorization code...")

    try:
        token_data = exchange_authorization_code(
            authorization_code, REDIRECT_URI, client_id, client_secret,
        )
    except Exception as e:
        print(f"\nError exchanging code: {e}", file=sys.stderr)
        sys.exit(1)

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("\nError: No refresh_token in response.", file=sys.stderr)
        print(f"Response: {token_data}", file=sys.stderr)
        sys.exit(1)

    # Store the token
    if _storage:
        try:
            _storage.set_secret(
                "HOME_CONNECT_REFRESH_TOKEN", refresh_token, scope="integration",
            )
            print("\nRefresh token saved to JarvisStorage.")
        except Exception as e:
            print(f"\nWarning: Could not save to JarvisStorage: {e}")
            print(f"Refresh token: {refresh_token}")
    else:
        print(f"\nRefresh token (save this as HOME_CONNECT_REFRESH_TOKEN):")
        print(f"  {refresh_token}")

    print("\nSetup complete! Your Home Connect appliances should now be discoverable.")


if __name__ == "__main__":
    main()

"""Lightweight Home Connect REST client using httpx.

Handles OAuth2 token refresh and all Home Connect API calls for Bosch/Siemens
appliances.  Only depends on httpx and Python stdlib.

Home Connect API docs: https://api-docs.home-connect.com/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Home Connect API constants
# ---------------------------------------------------------------------------

AUTH_BASE = "https://api.home-connect.com/security/oauth"
API_BASE = "https://api.home-connect.com/api"
AUTHORIZE_URL = f"{AUTH_BASE}/authorize"
TOKEN_URL = f"{AUTH_BASE}/token"

# Scopes: IdentifyAppliance is mandatory; Dishwasher covers monitor + control + settings
DEFAULT_SCOPES = "IdentifyAppliance Dishwasher"

_TIMEOUT = 30

# Maps Home Connect appliance types to Jarvis domains
APPLIANCE_TYPE_TO_DOMAIN: dict[str, str] = {
    "Dishwasher": "dishwasher",
    "Washer": "washer",
    "Dryer": "dryer",
    "WasherDryer": "washer",
    "Oven": "oven",
    "CoffeeMaker": "switch",
    "Refrigerator": "sensor",
    "Freezer": "sensor",
    "FridgeFreezer": "sensor",
    "Hob": "switch",
    "Hood": "switch",
    "CleaningRobot": "switch",
}

# Human-readable operation states
OPERATION_STATE_LABELS: dict[str, str] = {
    "BSH.Common.EnumType.OperationState.Inactive": "Inactive",
    "BSH.Common.EnumType.OperationState.Ready": "Ready",
    "BSH.Common.EnumType.OperationState.DelayedStart": "Delayed Start",
    "BSH.Common.EnumType.OperationState.Run": "Running",
    "BSH.Common.EnumType.OperationState.Pause": "Paused",
    "BSH.Common.EnumType.OperationState.ActionRequired": "Action Required",
    "BSH.Common.EnumType.OperationState.Finished": "Finished",
    "BSH.Common.EnumType.OperationState.Error": "Error",
    "BSH.Common.EnumType.OperationState.Aborting": "Aborting",
}

DOOR_STATE_LABELS: dict[str, str] = {
    "BSH.Common.EnumType.DoorState.Open": "Open",
    "BSH.Common.EnumType.DoorState.Closed": "Closed",
    "BSH.Common.EnumType.DoorState.Locked": "Locked",
}


def _short_program_name(program_key: str) -> str:
    """Extract a human-friendly name from a BSH program key.

    ``Dishcare.Dishwasher.Program.Eco50`` -> ``Eco50``
    """
    return program_key.rsplit(".", 1)[-1] if "." in program_key else program_key


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class HomeConnectAppliance:
    """An appliance paired to the Home Connect account."""

    ha_id: str
    name: str
    brand: str
    appliance_type: str
    connected: bool
    enumber: str = ""


@dataclass
class ApplianceStatus:
    """Current status snapshot of an appliance."""

    operation_state: str = "Unknown"
    door_state: str = "Unknown"
    remote_control_active: bool = False
    remote_start_allowed: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActiveProgram:
    """Details of the currently running program."""

    program_key: str
    program_name: str
    remaining_seconds: int | None = None
    progress_percent: int | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AvailableProgram:
    """A program that can be started on the appliance."""

    program_key: str
    program_name: str
    constraints: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def refresh_access_token(
    client_id: str,
    refresh_token: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    """Exchange a refresh token for a new access token.

    Returns the raw token response dict.
    """
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = client_secret

    resp = httpx.post(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def exchange_authorization_code(
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    """Exchange an authorization code for tokens.

    Returns the raw token response containing ``access_token`` and
    ``refresh_token``.
    """
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
    }
    if client_secret:
        data["client_secret"] = client_secret

    resp = httpx.post(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def build_authorize_url(
    client_id: str,
    redirect_uri: str,
    scope: str = DEFAULT_SCOPES,
) -> str:
    """Build the Home Connect OAuth authorize URL."""
    import urllib.parse

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# REST client
# ---------------------------------------------------------------------------


class HomeConnectClient:
    """Thin REST wrapper around the Home Connect cloud API.

    Authenticates with a refresh token and manages access token lifecycle.
    """

    def __init__(
        self,
        client_id: str,
        refresh_token: str,
        client_secret: str | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self.new_refresh_token: str | None = None

    def authenticate(self) -> None:
        """Obtain an access token via refresh token grant."""
        data = refresh_access_token(
            self._client_id, self._refresh_token, self._client_secret,
        )
        self._access_token = data["access_token"]
        if "refresh_token" in data:
            self.new_refresh_token = data["refresh_token"]

    # -- Appliance discovery -------------------------------------------------

    def get_appliances(self) -> list[HomeConnectAppliance]:
        """List all paired appliances on the account."""
        resp = self._request("GET", "homeappliances")
        appliances: list[HomeConnectAppliance] = []
        for item in resp.json().get("data", {}).get("homeappliances", []):
            appliances.append(
                HomeConnectAppliance(
                    ha_id=item["haId"],
                    name=item.get("name", "Unknown"),
                    brand=item.get("brand", ""),
                    appliance_type=item.get("type", "Unknown"),
                    connected=item.get("connected", False),
                    enumber=item.get("enumber", ""),
                )
            )
        return appliances

    # -- Status --------------------------------------------------------------

    def get_status(self, ha_id: str) -> ApplianceStatus:
        """Get current status of an appliance."""
        resp = self._request("GET", f"homeappliances/{ha_id}/status")
        items = resp.json().get("data", {}).get("status", [])

        raw: dict[str, Any] = {}
        for item in items:
            raw[item["key"]] = item.get("value")

        op_raw = raw.get("BSH.Common.Status.OperationState", "")
        door_raw = raw.get("BSH.Common.Status.DoorState", "")

        return ApplianceStatus(
            operation_state=OPERATION_STATE_LABELS.get(str(op_raw), str(op_raw)),
            door_state=DOOR_STATE_LABELS.get(str(door_raw), str(door_raw)),
            remote_control_active=bool(
                raw.get("BSH.Common.Status.RemoteControlActive", False),
            ),
            remote_start_allowed=bool(
                raw.get("BSH.Common.Status.RemoteControlStartAllowed", False),
            ),
            raw=raw,
        )

    # -- Programs ------------------------------------------------------------

    def get_available_programs(self, ha_id: str) -> list[AvailableProgram]:
        """List programs that can be started on the appliance."""
        resp = self._request(
            "GET", f"homeappliances/{ha_id}/programs/available",
        )
        programs: list[AvailableProgram] = []
        for item in resp.json().get("data", {}).get("programs", []):
            programs.append(
                AvailableProgram(
                    program_key=item["key"],
                    program_name=_short_program_name(item["key"]),
                    constraints=item.get("constraints", {}),
                )
            )
        return programs

    def get_active_program(self, ha_id: str) -> ActiveProgram | None:
        """Get the currently running program, or None if idle."""
        try:
            resp = self._request(
                "GET", f"homeappliances/{ha_id}/programs/active",
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

        data = resp.json().get("data", {})
        program_key = data.get("key", "")
        options: dict[str, Any] = {}
        remaining: int | None = None
        progress: int | None = None

        for opt in data.get("options", []):
            key = opt.get("key", "")
            val = opt.get("value")
            options[key] = val
            if key == "BSH.Common.Option.RemainingProgramTime":
                remaining = int(val) if val is not None else None
            elif key == "BSH.Common.Option.ProgramProgress":
                progress = int(val) if val is not None else None

        return ActiveProgram(
            program_key=program_key,
            program_name=_short_program_name(program_key),
            remaining_seconds=remaining,
            progress_percent=progress,
            options=options,
        )

    def start_program(
        self,
        ha_id: str,
        program_key: str,
        options: list[dict[str, Any]] | None = None,
    ) -> None:
        """Start a program on the appliance.

        Args:
            ha_id: The appliance Home Connect ID.
            program_key: Full program key (e.g. ``Dishcare.Dishwasher.Program.Auto1``).
            options: Optional list of program options, each with ``key`` and ``value``.
        """
        body: dict[str, Any] = {"data": {"key": program_key}}
        if options:
            body["data"]["options"] = options
        self._request("PUT", f"homeappliances/{ha_id}/programs/active", json=body)

    def stop_program(self, ha_id: str) -> None:
        """Stop the active program on the appliance."""
        self._request("DELETE", f"homeappliances/{ha_id}/programs/active")

    # -- Settings ------------------------------------------------------------

    def get_settings(self, ha_id: str) -> dict[str, Any]:
        """Get all settings for an appliance."""
        resp = self._request("GET", f"homeappliances/{ha_id}/settings")
        settings: dict[str, Any] = {}
        for item in resp.json().get("data", {}).get("settings", []):
            settings[item["key"]] = item.get("value")
        return settings

    # -- Internal ------------------------------------------------------------

    def _request(
        self, method: str, path: str, **kwargs: Any,
    ) -> httpx.Response:
        url = f"{API_BASE}/{path}"
        headers = {
            "Authorization": f"Bearer {self._access_token or ''}",
            "Accept": "application/vnd.bsh.sdk.v1+json",
            "Content-Type": "application/vnd.bsh.sdk.v1+json",
        }
        resp = httpx.request(
            method, url, headers=headers, timeout=_TIMEOUT, **kwargs,
        )
        resp.raise_for_status()
        return resp

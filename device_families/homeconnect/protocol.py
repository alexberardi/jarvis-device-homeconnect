"""Home Connect (Bosch/Siemens) device protocol adapter (cloud)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from jarvis_command_sdk import (
    DeviceControlResult,
    DiscoveredDevice,
    IJarvisButton,
    IJarvisDeviceProtocol,
    IJarvisSecret,
    JarvisSecret,
    JarvisStorage,
)
from jarvis_command_sdk.authentication import AuthenticationConfig

try:
    from jarvis_log_client import JarvisLogger
except ImportError:
    import logging

    class JarvisLogger:  # type: ignore[no-redef]
        def __init__(self, **kw: Any) -> None:
            self._log = logging.getLogger(kw.get("service", __name__))

        def info(self, msg: str, **kw: Any) -> None:
            self._log.info(msg)

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg)

        def error(self, msg: str, **kw: Any) -> None:
            self._log.error(msg)

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg)


from homeconnect_client import (
    APPLIANCE_TYPE_TO_DOMAIN,
    AUTHORIZE_URL,
    DEFAULT_SCOPES,
    TOKEN_URL,
    HomeConnectClient,
)

logger = JarvisLogger(service="device.homeconnect")

_storage = JarvisStorage("homeconnect")

# Cache: avoid re-authenticating on every call within a single session
_cached_client: HomeConnectClient | None = None


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _get_refresh_token() -> str | None:
    return _storage.get_secret("HOME_CONNECT_REFRESH_TOKEN", scope="integration")


def _get_client_id() -> str | None:
    return _storage.get_secret("HOME_CONNECT_CLIENT_ID", scope="integration")


def _get_client_secret() -> str | None:
    return _storage.get_secret("HOME_CONNECT_CLIENT_SECRET", scope="integration")


def _save_refresh_token(token: str) -> None:
    _storage.set_secret("HOME_CONNECT_REFRESH_TOKEN", token, scope="integration")


def _build_client() -> HomeConnectClient:
    """Authenticate and return a HomeConnectClient."""
    global _cached_client

    if _cached_client is not None:
        return _cached_client

    refresh_token = _get_refresh_token()
    if not refresh_token:
        raise ValueError(
            "HOME_CONNECT_REFRESH_TOKEN not configured -- "
            "run the OAuth setup first (scripts/authorize.py)"
        )

    client_id = _get_client_id()
    if not client_id:
        raise ValueError("HOME_CONNECT_CLIENT_ID not configured")

    client_secret = _get_client_secret()

    client = HomeConnectClient(client_id, refresh_token, client_secret)
    client.authenticate()

    # Persist rotated refresh token if the API returns a new one
    if client.new_refresh_token:
        _save_refresh_token(client.new_refresh_token)
        logger.debug("Rotated Home Connect refresh token")

    _cached_client = client
    return client


def _invalidate_client() -> None:
    """Clear the cached client (e.g. on auth failure)."""
    global _cached_client
    _cached_client = None


class HomeConnectProtocol(IJarvisDeviceProtocol):
    """Home Connect protocol adapter for Bosch/Siemens appliances."""

    protocol_name: str = "homeconnect"
    friendly_name: str = "Home Connect (Bosch/Siemens)"
    supported_domains: list[str] = ["dishwasher"]
    connection_type: str = "cloud"
    setup_guide: str = """## Home Connect Setup

This integration connects to your Bosch/Siemens appliances via the
Home Connect cloud API.

### First-time setup

1. Register at https://developer.home-connect.com/
2. Create an application to get a **Client ID** and **Client Secret**
3. Set the redirect URI to `https://relay.jarvisautomation.io/oauth/bounce`
4. Enter your Client ID and Client Secret in the device settings
5. Complete the OAuth login via the mobile app (Settings > Integrations)

### What you can do

- **Start / stop** dishwasher programs (Auto, Eco, Intensive, etc.)
- **Check status** (running, door state, remaining time, progress)
- **List available programs** for your specific model

### Notes

- Your appliance must be connected to Home Connect (Wi-Fi setup via the Home Connect app)
- **Remote Start** must be enabled on the appliance for starting programs
- Access tokens expire after 24 hours and are refreshed automatically"""

    @property
    def authentication(self) -> AuthenticationConfig:
        client_id = _get_client_id() or ""
        client_secret = _get_client_secret()
        return AuthenticationConfig(
            type="oauth",
            provider="homeconnect",
            friendly_name="Home Connect",
            client_id=client_id,
            client_secret=client_secret,
            keys=["refresh_token"],
            authorize_url=AUTHORIZE_URL,
            exchange_url=TOKEN_URL,
            scopes=DEFAULT_SCOPES.split(),
            refresh_token_secret_key="HOME_CONNECT_REFRESH_TOKEN",
            requires_background_refresh=True,
            refresh_interval_seconds=72000,  # 20 hours (tokens expire in 24)
        )

    @property
    def required_secrets(self) -> list[IJarvisSecret]:
        return [
            JarvisSecret(
                "HOME_CONNECT_CLIENT_ID",
                "OAuth Client ID from developer.home-connect.com",
                "integration",
                "string",
                is_sensitive=False,
                required=True,
                friendly_name="Client ID",
            ),
            JarvisSecret(
                "HOME_CONNECT_CLIENT_SECRET",
                "OAuth Client Secret from developer.home-connect.com",
                "integration",
                "string",
                is_sensitive=True,
                required=True,
                friendly_name="Client Secret",
            ),
            JarvisSecret(
                "HOME_CONNECT_REFRESH_TOKEN",
                "OAuth refresh token (obtained via authorize.py)",
                "integration",
                "string",
                is_sensitive=True,
                required=True,
                friendly_name="Refresh Token",
            ),
        ]

    @property
    def supported_actions(self) -> list[IJarvisButton]:
        return [
            IJarvisButton(
                button_text="Auto Wash",
                button_action="start_auto",
                button_type="primary",
                button_icon="dishwasher",
            ),
            IJarvisButton(
                button_text="Eco 50\u00b0",
                button_action="start_eco",
                button_type="primary",
                button_icon="leaf",
            ),
            IJarvisButton(
                button_text="Quick 65\u00b0",
                button_action="start_quick",
                button_type="secondary",
                button_icon="lightning-bolt",
            ),
            IJarvisButton(
                button_text="Glass 40\u00b0",
                button_action="start_glass",
                button_type="secondary",
                button_icon="glass-fragile",
            ),
            IJarvisButton(
                button_text="Pre-Rinse",
                button_action="start_prerinse",
                button_type="secondary",
                button_icon="water",
            ),
            IJarvisButton(
                button_text="Stop",
                button_action="stop",
                button_type="destructive",
                button_icon="stop",
            ),
        ]

    def store_auth_values(self, values: dict[str, str]) -> None:
        """Persist tokens received from the mobile OAuth flow."""
        if "refresh_token" in values:
            _save_refresh_token(values["refresh_token"])
            _invalidate_client()
            logger.info("Home Connect refresh token saved via OAuth flow")

    async def discover(self, timeout: float = 10.0) -> list[DiscoveredDevice]:
        if not _get_refresh_token():
            logger.error("Home Connect refresh token not configured")
            return []

        if not _get_client_id():
            logger.error("Home Connect client ID not configured")
            return []

        try:
            client = await asyncio.to_thread(_build_client)
            appliances = await asyncio.to_thread(client.get_appliances)
        except Exception as e:
            _invalidate_client()
            logger.error(f"Home Connect discovery failed: {e}")
            return []

        devices: list[DiscoveredDevice] = []

        for appliance in appliances:
            domain = APPLIANCE_TYPE_TO_DOMAIN.get(
                appliance.appliance_type, "switch",
            )
            entity_id = _slugify(appliance.name) if appliance.name else _slugify(appliance.ha_id)

            devices.append(
                DiscoveredDevice(
                    entity_id=entity_id,
                    name=appliance.name,
                    domain=domain,
                    protocol=self.protocol_name,
                    model=appliance.enumber or appliance.appliance_type,
                    manufacturer=appliance.brand or "Bosch/Siemens",
                    cloud_id=appliance.ha_id,
                    is_controllable=appliance.connected,
                    extra={
                        "appliance_type": appliance.appliance_type,
                        "connected": appliance.connected,
                        "ha_id": appliance.ha_id,
                    },
                )
            )

        logger.info(f"Home Connect discovery found {len(devices)} appliance(s)")
        return devices

    async def control(
        self,
        device: DiscoveredDevice,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> DeviceControlResult:
        if not _get_refresh_token():
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action=action,
                error="Home Connect refresh token not configured",
            )

        try:
            client = await asyncio.to_thread(_build_client)
        except Exception as e:
            _invalidate_client()
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action=action,
                error=f"Home Connect auth failed: {e}",
            )

        ha_id = device.extra.get("ha_id") or device.cloud_id
        if not ha_id:
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action=action,
                error="No Home Connect appliance ID found for device",
            )

        params = params or {}

        # Map button actions to program keys
        _ACTION_PROGRAMS: dict[str, str] = {
            "start_auto": "Dishcare.Dishwasher.Program.Auto2",
            "start_eco": "Dishcare.Dishwasher.Program.Eco50",
            "start_quick": "Dishcare.Dishwasher.Program.Quick65",
            "start_glass": "Dishcare.Dishwasher.Program.Glas40",
            "start_prerinse": "Dishcare.Dishwasher.Program.PreRinse",
            "start_machinecare": "Dishcare.Dishwasher.Program.MachineCare",
        }

        try:
            if action in _ACTION_PROGRAMS:
                params["program_key"] = _ACTION_PROGRAMS[action]
                return await self._handle_start(client, device, ha_id, params)
            elif action == "start":
                return await self._handle_start(client, device, ha_id, params)
            elif action == "stop":
                await asyncio.to_thread(client.stop_program, ha_id)
            else:
                return DeviceControlResult(
                    success=False,
                    entity_id=device.entity_id,
                    action=action,
                    error=f"Unsupported action: {action}",
                )

        except Exception as e:
            _invalidate_client()
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action=action,
                error=f"Control failed: {e}",
            )

        return DeviceControlResult(
            success=True,
            entity_id=device.entity_id,
            action=action,
        )

    async def _handle_start(
        self,
        client: HomeConnectClient,
        device: DiscoveredDevice,
        ha_id: str,
        params: dict[str, Any],
    ) -> DeviceControlResult:
        """Start a program, picking the best match if a name is given."""
        # Check remote start is allowed
        try:
            status = await asyncio.to_thread(client.get_status, ha_id)
        except Exception as e:
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action="start",
                error=f"Could not check appliance status: {e}",
            )

        if not status.remote_control_active:
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action="start",
                error="Remote control is not active on the appliance. "
                      "Enable it on the dishwasher's control panel.",
            )

        if not status.remote_start_allowed:
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action="start",
                error="Remote start is not enabled. Press the Remote Start "
                      "button on the dishwasher before starting remotely.",
            )

        if status.door_state == "Open":
            return DeviceControlResult(
                success=False,
                entity_id=device.entity_id,
                action="start",
                error="The dishwasher door is open. Close it first.",
            )

        # Pick program
        program_key = params.get("program") or params.get("program_key")
        if not program_key:
            # Try to match a program name from params
            program_name = params.get("program_name", "")
            if program_name:
                program_key = await self._resolve_program(
                    client, ha_id, program_name,
                )
            if not program_key:
                # Default: use Auto
                program_key = "Dishcare.Dishwasher.Program.Auto1"

        # Build options list
        options: list[dict[str, Any]] = []
        if "delay_minutes" in params:
            delay_secs = int(params["delay_minutes"]) * 60
            options.append({
                "key": "BSH.Common.Option.StartInRelative",
                "value": delay_secs,
                "unit": "seconds",
            })

        await asyncio.to_thread(
            client.start_program, ha_id, program_key, options or None,
        )

        return DeviceControlResult(
            success=True,
            entity_id=device.entity_id,
            action="start",
        )

    async def _resolve_program(
        self,
        client: HomeConnectClient,
        ha_id: str,
        name: str,
    ) -> str | None:
        """Fuzzy-match a program name to a program key."""
        try:
            programs = await asyncio.to_thread(
                client.get_available_programs, ha_id,
            )
        except Exception:
            return None

        name_lower = name.lower()
        for prog in programs:
            if name_lower in prog.program_name.lower():
                return prog.program_key

        return None

    async def get_state(
        self, device: DiscoveredDevice,
    ) -> dict[str, Any]:
        if not _get_refresh_token():
            return {"error": "Home Connect refresh token not configured"}

        try:
            client = await asyncio.to_thread(_build_client)
        except Exception as e:
            _invalidate_client()
            return {"error": f"Home Connect auth failed: {e}"}

        ha_id = device.extra.get("ha_id") or device.cloud_id
        if not ha_id:
            return {"error": "No Home Connect appliance ID found"}

        try:
            status = await asyncio.to_thread(client.get_status, ha_id)
            state: dict[str, Any] = {
                "operation_state": status.operation_state,
                "door_state": status.door_state,
                "remote_control_active": status.remote_control_active,
                "remote_start_allowed": status.remote_start_allowed,
            }

            # If running, get active program details
            if status.operation_state in ("Running", "Paused", "Delayed Start"):
                active = await asyncio.to_thread(
                    client.get_active_program, ha_id,
                )
                if active:
                    state["active_program"] = active.program_name
                    if active.remaining_seconds is not None:
                        state["remaining_minutes"] = active.remaining_seconds // 60
                    if active.progress_percent is not None:
                        state["progress_percent"] = active.progress_percent

            return state

        except Exception as e:
            _invalidate_client()
            return {"error": f"Failed to get state: {e}"}

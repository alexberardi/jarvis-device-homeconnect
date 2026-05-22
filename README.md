# jarvis-device-homeconnect

Bosch/Siemens appliance control via the [Home Connect](https://www.home-connect.com/) cloud API. Dishwasher support (start/stop programs, check status, monitor progress).

## Setup

### 1. Register a Home Connect developer account

1. Go to https://developer.home-connect.com/ and create an account
2. Create a new application
3. Set the redirect URI to `https://relay.jarvisautomation.io/oauth/bounce`
4. Note your **Client ID** and **Client Secret**

### 2. Install on your node

```bash
python scripts/command_store.py install --local /path/to/jarvis-device-homeconnect
```

### 3. Configure secrets and authorize

**Via mobile app (recommended):**

1. Open the mobile app and go to the node's device settings
2. Enter your Client ID and Client Secret for the Home Connect integration
3. Tap "Authorize" to complete the OAuth login

**Via CLI (dev/testing):**

```bash
cd jarvis-device-homeconnect
python homeconnect_scripts/authorize.py
```

The script prompts for credentials, opens a browser for Home Connect login,
and stores the refresh token.

### 4. Enable Remote Start on your dishwasher

To start programs remotely, you must enable Remote Start on the dishwasher's physical control panel before each cycle.

## Supported actions

| Action | Description |
|--------|-------------|
| `start` | Start a wash program (optionally specify `program_name`) |
| `stop` | Stop the active program |

### Voice examples

- "Start the dishwasher"
- "Run the eco cycle on the dishwasher"
- "Stop the dishwasher"
- "What's the dishwasher status?"

## Available programs

Programs vary by model. Common Bosch programs:

- Auto (Auto1) — default if no program specified
- Eco 50
- Intensive 70
- Quick 45 / Speed 60
- Glass 40
- Pre-Rinse
- Machine Care

The protocol queries your specific model for its available programs.

## License

MIT

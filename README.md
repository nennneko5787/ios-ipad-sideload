# iOS Sideload Server for Termux (armv7a)

A terminal-based (TUI) sideloading server for iOS 27+ devices using **RPPairing (Remote Pairing)** - Apple's Wi-Fi pairing feature introduced in iOS 26.4 and enhanced in iOS 27. This allows pairing and installing apps on iPhone/iPad **without USB**, entirely over Wi-Fi.

## Features

- 📱 **Wi-Fi Pairing (RPPairing)**: Pair iOS 27+ devices over local network without cables
- 🔐 **IPA Signing**: Sign apps with Apple ID (auto-provisioning) or Certificate + Provisioning Profile
- 📦 **App Management**: Install, reinstall, refresh (re-sign), and uninstall apps
- ⚡ **Auto Refresh**: Automatically re-sign and reinstall expiring apps (7-day free cert limit)
- 🖥️ **Termux Native**: Built for Termux on Android (armv7a / arm64)
- 🎨 **Beautiful TUI**: Keyboard-driven interface using Textual

## Requirements

### Termux Packages
```bash
pkg update && pkg upgrade
pkg install python clang make openssl libffi libusb zlib git
pkg install rust  # Optional, for building some dependencies
```

### Python Dependencies
```bash
pip install -r requirements.txt
```

### zsign Binary (Required for Signing)
zsign is used for IPA signing with certificates. On Termux armv7a, you may need to build from source:

```bash
# Install build dependencies
pkg install cmake ninja

# Clone and build zsign
git clone https://github.com/zhlynn/zsign.git
cd zsign
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j$(nproc)
# Copy zsign binary to PATH
cp zsign $PREFIX/bin/
```

Or if available in Termux repos:
```bash
pkg install zsign
```

## Installation

```bash
# Clone the repository
git clone <your-repo-url>
cd ios-ipad-sideload

# Install Python dependencies
pip install -r requirements.txt

# Make sure zsign is in PATH
which zsign

# Run the server
python -m sideload_server.main --data-dir ./data --upload-dir ./uploads
```

## Usage

### Keyboard Shortcuts
| Key | Action |
|-----|--------|
| `d` | Dashboard |
| `v` | Devices |
| `a` | Apps |
| `s` | Signing Configs |
| `c` | Configuration |
| `r` | Refresh current view |
| `q` / `Ctrl+C` | Quit |

### First Time Setup

1. **Create a Signing Configuration** (Tab: `s` → `➕ Add Config`)
   - **Apple ID** (Recommended): Enter your Apple ID and app-specific password
   - **Certificate + Profile**: Provide `.p12` certificate and `.mobileprovision` profile

2. **Pair a Device** (Tab: `v` → `➕ Pair New Device`)
   - Ensure iPhone/iPad is on **iOS 27+** and same Wi-Fi network
   - Enter device name and PIN (default: 000000)
   - On iOS device: Settings → General → VPN & Device Management → Pair New Device
   - Select this server and enter PIN

3. **Install an App** (Tab: `a` → `📥 Install IPA`)
   - Provide path to `.ipa` file
   - Select device and signing config
   - App will be signed and installed automatically

### Auto Refresh
Apps signed with free Apple ID certificates expire in 7 days. The server automatically checks every hour and re-signs/reinstalls apps expiring within 3 days.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    iOS Sideload Server                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  TUI App    │  │  Device Mgr │  │   Signing Mgr       │  │
│  │  (Textual)  │◄─┤  (pymobile- │◄─┤   (zsign +          │  │
│  │             │  │   device3)  │  │    pymobiledevice3) │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│         ▲               ▲                    ▲               │
│         │               │                    │               │
│         ▼               ▼                    ▼               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Storage (JSON files)                    │    │
│  │  Devices | Apps | Sign Configs | Pairing Files      │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                    Wi-Fi (mDNS/RPPairing)
                              ▼
                    ┌─────────────────┐
                    │   iOS 27+       │
                    │   Device        │
                    └─────────────────┘
```

## RPPairing (iOS 27+ Wi-Fi Pairing)

Starting with iOS 26.4, Apple introduced **RPPairing (Remote Pairing)** which allows pairing over Wi-Fi without USB. iOS 27 enhanced this with a user-initiated "Pair over Wi-Fi" flow.

### How it Works
1. Server advertises via mDNS (`_remotepairing._tcp`)
2. User initiates pairing on iOS device (Settings → VPN & Device Management)
3. Device connects to server, displays PIN prompt
4. User enters PIN on device
5. Server generates RPPairing record (Ed25519 keys + device certificate)
6. Record stored locally and used for all future communication

### Requirements
- iOS 27+ (iOS 26.4+ works with remote pairing file from computer)
- Same local network (mDNS must work)
- Device passcode set
- Developer Mode enabled (Settings → Privacy & Security → Developer Mode)

## Signing Methods

### Apple ID (Auto-Provisioning)
- Uses `pymobiledevice3` to login to Apple Developer Portal
- Requires anisette server for authentication (can use public ones)
- Automatically registers device UDID, creates certificate & profile
- Best for: Personal use, free Apple ID

### Certificate + Provisioning Profile
- Uses `zsign` for local signing
- Requires `.p12` certificate and `.mobileprovision` profile
- Works offline once configured
- Best for: Paid Developer Account, CI/CD, enterprise

## File Structure

```
ios-ipad-sideload/
├── requirements.txt
├── README.md
├── sideload_server/
│   ├── __init__.py
│   ├── main.py           # TUI Application entry point
│   ├── tui.css           # Textual CSS styles
│   ├── models.py         # Data models
│   ├── storage.py        # JSON file storage
│   ├── device_manager.py # Device communication (pymobiledevice3)
│   └── signing.py        # IPA signing (zsign + pymobiledevice3)
└── data/                 # Created at runtime
    ├── config.json
    ├── devices.json
    ├── apps.json
    ├── sign_configs.json
    └── pairing_files/
        └── <udid>.plist
```

## Troubleshooting

### Device Not Found in Pairing
- Ensure both devices on same Wi-Fi network
- Check mDNS/avahi is working: `pkg install avahi` (if needed)
- Try restarting Termux app
- Verify iOS 27+ and Developer Mode enabled

### Signing Fails
- **Apple ID**: Check anisette server, app-specific password, 2FA
- **Certificate**: Verify `.p12` password, profile not expired, device in profile
- **zsign**: Ensure binary in PATH and executable

### Installation Fails
- Check Developer Mode enabled on device
- Ensure device is paired and reachable
- Try starting tunnel manually: Devices tab → "Start Tunnel"

### Termux armv7a Specific
- Some Python packages may need compilation (cryptography, etc.)
- Use `pkg install python-cryptography` for pre-built
- Increase swap if memory issues: `termux-create-swap 512`

## Building zsign on Termux armv7a

```bash
pkg install cmake ninja git clang make openssl zlib
git clone https://github.com/zhlynn/zsign.git
cd zsign
git submodule update --init --recursive
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ ..
make -j$(nproc)
cp zsign $PREFIX/bin/
zsign --version  # Verify
```

## License

MIT License - Feel free to use and modify.

## Credits

- [pymobiledevice3](https://github.com/doronzo/pymobiledevice3) - iOS device communication
- [zsign](https://github.com/zhlynn/zsign) - IPA signing
- [Textual](https://github.com/Textualize/textual) - TUI framework
- [idevice](https://github.com/jkcoxson/idevice) - RPPairing protocol research
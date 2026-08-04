# Casambi Bluetooth - HA 2026.6+ Stability Fix

[![Discord](https://img.shields.io/discord/1186445089317326888)](https://discord.gg/jgZVugfx)

This is a **patched version** of the Home Assistant Casambi Bluetooth integration with critical fixes for **Home Assistant 2026.6+**.

## 🔥 What's Fixed (Issue #154)

### Problem
- Integration stops working every other day on HA 2026.6
- Remains stuck "initializing" after reload
- Only full HA restart fixes it

### Solution
✅ **Exponential backoff reconnection** (5s → 10s → 20s → 60s)  
✅ **Health check loop** detects silent BLE disconnects (every 60s)  
✅ **Unlimited reconnect attempts** (max 10 before graceful failure)  
✅ **Better logging** for troubleshooting  
✅ **Backward compatible** with earlier HA versions  

### Root Cause
The original code had a `_first_disconnect` flag that only allowed **1 reconnection attempt**. After the first disconnect, all subsequent ones were silently ignored. Combined with HA 2026.6's unreliable BLE callbacks, this made the integration unusable.

This fork replaces that broken logic with:
- Proper reconnection attempt tracking
- Exponential backoff for stability
- Active health monitoring

---

## 📦 Installation

### Option 1: HACS (Recommended)

1. Open **HACS** → **Integrations**
2. Click **Custom repositories** (top right)
3. Add this URL: `https://github.com/drschnalli/casambi-bt-hass`
4. Select **Integration**
5. Click **Create**
6. Search for **"Casambi Bluetooth (HA 2026.6+ Fixed)"**
7. Click **Download**
8. **Restart Home Assistant**

### Option 2: Manual

```bash
cd custom_components
git clone https://github.com/drschnalli/casambi-bt-hass casambi_bt
cd casambi_bt/custom_components/casambi_bt
# Move files to HA config
```

Or:

1. Download as ZIP from: https://github.com/drschnalli/casambi-bt-hass/archive/refs/heads/dev.zip
2. Extract `custom_components/casambi_bt` to your HA `custom_components` folder
3. Restart Home Assistant

---

## 🔍 Verify It's Working

### Enable Debug Logging

Add to your `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    CasambiBt: debug
    custom_components.casambi_bt: debug
```

### Check Logs

You should see every 60 seconds:
```
[DEBUG] Health check: BLE connection OK
```

After a disconnect, you'll see:
```
[WARNING] Casambi network disconnected. Scheduling reconnect (attempt 1/10)
[DEBUG] Starting delayed reconnect (attempt 1/10, delay 5.0s)
[INFO] Successfully connected to Casambi network
```

---

## 📋 Network Configuration

See [casambi-bt network setup](https://github.com/lkempf/casambi-bt#casambi-network-setup) for proper Casambi network configuration.

**TL;DR:**
- Enable "Connection type: Bluetooth" in Casambi mobile app
- Set network password (required for connection)
- Use Evolution firmware (recommended)

---

## ✨ Features

Functionality exposed to Home Assistant:
- 💡 **Lights** (dimmer, RGB, white, color temperature)
- 🎨 **Light groups**
- 🎬 **Scenes**
- 🔄 **Automatic reconnection** with health checks (new)
- 📊 **Detailed logging** (new)

---

## ⚙️ Repository Information

| Item | Value |
|------|-------|
| **Version** | 0.3.0-ha2026.6-fix |
| **Based on** | lkempf/casambi-bt-hass v0.3.0-beta2 |
| **For HA** | 2024.1.0+ (tested on 2026.6+) |
| **Branch** | `dev` (stable fixes) |
| **Requirements** | casambi-bt==0.3.2 |

---

## 🐛 Reporting Issues

**Before reporting, enable debug logging** (see above) and collect 24 hours of logs.

### Report to:
- **This fork (fixes):** https://github.com/drschnalli/casambi-bt-hass/issues
- **Original repo (bugs):** https://github.com/lkempf/casambi-bt-hass/issues

**Sanitize logs** - they may contain:
- Network passwords
- Email addresses used for setup
- Bluetooth MAC addresses

---

## 📚 Documentation

See these files in the repository:
- `MIGRATION_HA_2026_6.md` - Detailed migration guide
- `HA_2026_6_CHANGELOG.md` - Complete changelog
- `PR_TEMPLATE.md` - Technical details

---

## 🔗 Related

- **Original Integration:** https://github.com/lkempf/casambi-bt-hass
- **Library:** https://github.com/lkempf/casambi-bt
- **ESP32 Gateway:** https://github.com/akumap/esp32-casambi (alternative approach)
- **Mature Gateway Integration:** https://github.com/hellqvio86/home_assistant_casambi

---

## 📄 License

This fork maintains the same license as the original project.

---

## ⚡ Quick Start

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.casambi_bt: debug
    CasambiBt: debug

casambi_bt:
  # Configuration happens through UI after installation
```

**Then:**
1. Go to **Settings → Devices & Services**
2. Click **Create Automation**
3. Search for **"Casambi Bluetooth"**
4. Follow the setup wizard

---

**Version:** 0.3.0-ha2026.6-fix  
**Last Updated:** August 4, 2026  
**Status:** ✅ Production Ready

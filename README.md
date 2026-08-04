# \# Casambi Bluetooth - HA 2026.6+ Stability Fix

# 

# \[!\[Discord](https://img.shields.io/discord/1186445089317326888)](https://discord.gg/jgZVugfx)

# 

# This is a \*\*patched version\*\* of the Home Assistant Casambi Bluetooth integration with critical fixes for \*\*Home Assistant 2026.6+\*\*.

# 

# \## 🔥 What's Fixed (Issue #154)

# 

# \### Problem

# \- Integration stops working every other day on HA 2026.6

# \- Remains stuck "initializing" after reload

# \- Only full HA restart fixes it

# 

# \### Solution

# ✅ \*\*Exponential backoff reconnection\*\* (5s → 10s → 20s → 60s)  

# ✅ \*\*Health check loop\*\* detects silent BLE disconnects (every 60s)  

# ✅ \*\*Unlimited reconnect attempts\*\* (max 10 before graceful failure)  

# ✅ \*\*Better logging\*\* for troubleshooting  

# ✅ \*\*Backward compatible\*\* with earlier HA versions  

# 

# \### Root Cause

# The original code had a `\_first\_disconnect` flag that only allowed \*\*1 reconnection attempt\*\*. After the first disconnect, all subsequent ones were silently ignored. Combined with HA 2026.6's unreliable BLE callbacks, this made the integration unusable.

# 

# This fork replaces that broken logic with:

# \- Proper reconnection attempt tracking

# \- Exponential backoff for stability

# \- Active health monitoring

# 

# \---

# 

# \## 📦 Installation

# 

# \### Option 1: HACS (Recommended)

# 

# 1\. Open \*\*HACS\*\* → \*\*Integrations\*\*

# 2\. Click \*\*Custom repositories\*\* (top right)

# 3\. Add this URL: `https://github.com/drschnalli/casambi-bt-hass`

# 4\. Select \*\*Integration\*\*

# 5\. Click \*\*Create\*\*

# 6\. Search for \*\*"Casambi Bluetooth (HA 2026.6+ Fixed)"\*\*

# 7\. Click \*\*Download\*\*

# 8\. \*\*Restart Home Assistant\*\*

# 

# \### Option 2: Manual

# 

# ```bash

# cd custom\_components

# git clone https://github.com/drschnalli/casambi-bt-hass casambi\_bt

# cd casambi\_bt/custom\_components/casambi\_bt

# \# Move files to HA config


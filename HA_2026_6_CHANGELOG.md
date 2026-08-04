# Changelog - HA 2026.6 Stability Fix

## Version 0.3.0-beta3 (Unreleased)

### 🐛 Critical Fixes
- **Fix #154**: Integration stops working every other day (HA 2026.6+)
  - Removed broken `_first_disconnect` flag that only allowed 1 reconnect
  - Added exponential backoff for reconnection attempts (5s → 60s)
  - Added health check loop to detect silent BLE disconnects (every 60s)
  - Properly track reconnection attempts (max 10 before graceful failure)
  
### 📊 Improvements  
- Better logging and visibility into connection state
- Clear error messages when max reconnection attempts reached
- Connection loss timestamps for debugging

### 🔮 Infrastructure
- Prepared groundwork for optional ESP32 gateway support
- Enhanced reconnection logic for future hybrid mode

### ⚠️ Known Issues
- ESP32 gateway mode not yet implemented (coming in next release)
- Some BLE adapters may still have edge cases - report with full logs

### 📝 Migration
See `MIGRATION_HA_2026_6.md` for upgrade instructions and troubleshooting.

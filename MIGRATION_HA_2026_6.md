# Home Assistant 2026.6 Stability Fix

## Problem
In HA 2026.6+, the Casambi integration stops working every other day and remains stuck "initializing" even after manual reload. Only a full HA restart fixes it.

## Root Cause
1. **The `_first_disconnect` Bug**: The integration only allows ONE reconnection attempt. After the first disconnect, all subsequent ones are silently ignored.
2. **Silent BLE Disconnects in HA 2026.6**: The Bluetooth stack may not call disconnect callbacks reliably, leaving the integration stuck thinking it's still connected.
3. **No Exponential Backoff**: The fixed 30-second delay doesn't account for temporary BLE glitches.

## What's Fixed

✅ **Unlimited Reconnection Attempts** (max 10 before giving up gracefully)  
✅ **Exponential Backoff** (5s → 10s → 20s → 60s, resets on success)  
✅ **Health Check Loop** (detects silent disconnects every 60 seconds)  
✅ **Better Logging** (see exactly what's happening in the logs)  
✅ **Graceful Degradation** (clear error messages when max retries reached)

## How to Update

1. **Replace** `custom_components/casambi_bt/__init__.py` with the new version
2. **Restart** Home Assistant
3. **Enable** debug logging to track reconnections:

```yaml
logger:
  default: info
  logs:
    CasambiBt: debug
    custom_components.casambi_bt: debug
```

4. **Monitor** logs - you should see "Health check: BLE connection OK" every 60 seconds

## How This Fixes HA 2026.6

The root cause of issue #154 was the `_first_disconnect` flag (line 81, 104, 172 in original code):
- First disconnect: `_first_disconnect = True` (on connection)
- First actual disconnect: `_first_disconnect` becomes `False`, schedules reconnect
- **Second disconnect: Silently ignored because `_first_disconnect` is now `False`**

After the first disconnect, every subsequent BLE drop is ignored. Combined with HA 2026.6's unreliable BLE callbacks, the integration would appear connected but actually be dead. Reloading wouldn't help because the BLE stack was in a bad state. Only a full HA restart would reset everything.

### The Fix
- **Removed** `_first_disconnect` flag entirely
- **Added** `_reconnect_attempts` counter - tracks reconnect attempts up to 10
- **Added** `_health_check_loop()` - detects if connection silently dies every 60 seconds
- **Exponential backoff** - gives the BLE stack time to recover

## Troubleshooting

**"Max reconnection attempts reached"**
- Integration couldn't reconnect after 10 attempts
- Check: Is the BLE device powered on and nearby?
- Check: Is the network password correct?
- Last resort: Restart Home Assistant

**"Health check detected BLE disconnection"**
- This is expected - means the health check is working
- You should see a reconnection attempt immediately after
- If reconnects keep failing, check BLE adapter and Casambi device

**Still stuck "initializing"?**
- Check logs for the actual error message
- Restart HA: `Developer Tools → Restart Home Assistant`
- Report the full logs to: https://github.com/lkempf/casambi-bt-hass/issues/154

## Testing

To verify the fix is working:

1. Enable debug logging (see above)
2. Look for "Health check: BLE connection OK" every 60 seconds
3. Manually power-cycle your Casambi gateway
4. Watch logs - should show:
   ```
   Health check detected BLE disconnection (no callback received)
   Casambi network disconnected. Scheduling reconnect (attempt 1/10)
   Starting delayed reconnect (attempt 1/10, delay 5.0s)
   Successfully connected to Casambi network
   ```

## Version Info
- Fixed in: casambi-bt-hass v0.3.0-beta3+
- Applies to: Home Assistant 2026.6+
- Also works on earlier HA versions

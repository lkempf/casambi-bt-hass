# Switch Event Support

Switch button press/release events are fired as Home Assistant events that can be used in automations.

## Event Details
- **Event Type**: `casambi_bt_switch_event`
- **Event Data**:
  - `entry_id`: The config entry ID
  - `unit_id`: The Casambi unit ID that sent the event
  - `button`: Button number (1-4, matching Casambi app)
  - `action`: Event type - one of:
    - `"button_press"` - Initial button press
    - `"button_hold"` - Sent continuously while button is held down
    - `"button_release"` - Quick press and release
    - `"button_release_after_hold"` - Release after holding
  - `message_type`: Raw message type from the device
  - `flags`: Additional flags from the message

## Button Hold Timing
- **Press to Hold Delay**: Approximately 500-600ms
  - Short press (< 500ms): Fires `button_press` followed by `button_release`
  - Long press (> 500ms): Fires `button_press`, then `button_hold` events start after ~500ms, finally `button_release_after_hold` when released
- **Hold Event Frequency**: `button_hold` events repeat while the button is held
  - The Casambi protocol sends multiple hold events with incrementing counters
  - These events can be used for continuous actions like dimming

## Listening to Events
You can monitor these events in Developer Tools → Events → Listen to events by entering `casambi_bt_switch_event` as the event type.

## Important Notes

**Tip**: You can use the Casambi app to configure switch button actions while simultaneously listening to events in Home Assistant. This allows you to:
- Use Casambi's built-in button assignments for some actions
- Create custom Home Assistant automations for other buttons
- Have multiple ways to control your devices

### Finding Your Switch Configuration

#### Method 1: Using Home Assistant Developer Tools (Recommended)
1. Go to **Developer Tools → Events** in Home Assistant
2. In the "Listen to events" section, enter: `casambi_bt_switch_event`
3. Click "Start listening"
4. Press the physical button on your switch
5. Check the captured event data for:
   - `unit_id`: The switch's unit ID
   - `button`: The button number (0-based)
   - `action`: The event type (button_press, button_release, etc.)


#### Method 2: Verifying Unit ID in Casambi App
If you see multiple events with different `unit_id` values, verify the correct one:
1. Open the Casambi app
2. Go to **More → Switches**
3. Select your switch
4. Tap **Details**
5. Note the **Unit ID** shown
6. Use this Unit ID in your automations

**Button Numbers**: Button numbers in events match the Casambi app (1-4). Always test each physical button first to verify which button number it generates in the events.

### Event Deduplication
The integration includes built-in event deduplication to prevent duplicate triggers. You can configure the deduplication window in the integration options (default: 600ms). This eliminates the need for complex debouncing logic in your automations.

### Event Reliability
Button press and release events are **not guaranteed** to be captured due to the nature of Bluetooth communication:
- Sometimes `button_press` events may be missed entirely
- For better reliability, consider triggering automations on both `button_press` and `button_release` events
- Be aware that `button_release` fires for short presses while `button_release_after_hold` fires for long presses - they are distinct events

## Example Event Data
Here are examples of different switch events in Home Assistant:

### Quick Press/Release
```yaml
event_type: casambi_bt_switch_event
data:
  entry_id: fc8461de92e186495147fdb327fddea9
  unit_id: 31
  button: 1
  action: button_release
  message_type: 8
  flags: 3
origin: LOCAL
```

### Button Hold Event (fires continuously)
```yaml
event_type: casambi_bt_switch_event
data:
  entry_id: fc8461de92e186495147fdb327fddea9
  unit_id: 31
  button: 1
  action: button_hold
  message_type: 16
  flags: 2
origin: LOCAL
```

### Release After Hold
```yaml
event_type: casambi_bt_switch_event
data:
  entry_id: fc8461de92e186495147fdb327fddea9
  unit_id: 31
  button: 1
  action: button_release_after_hold
  message_type: 16
  flags: 2
origin: LOCAL
```
# Switch Event Documentation

This integration fires Home Assistant events when Casambi switches are pressed. These events can be used to trigger automations.

## Event Details

**Event Type:** `casambi_bt_switch_event`

**Event Data:**
- `unit_id` - The ID of the switch unit
- `button` - Button number (0-3 for 4-button switches)
- `action` - The type of button action:
  - `button_press` - Button initially pressed
  - `button_hold` - Button held down (~500ms after press)
  - `button_release` - Button released after short press
  - `button_release_after_hold` - Button released after hold

## Finding Your Switch Unit ID

1. Enable debug logging in `configuration.yaml`:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.casambi_bt: debug
   ```

2. Press a button on your switch
3. Check the logs for entries like:
   ```
   Fired casambi_bt_switch_event for unit 42 button 0 - button_press
   ```
   The unit ID in this example is `42`

## Example Automations

### Simple Light Toggle
```yaml
automation:
  - alias: "Kitchen Light Toggle"
    trigger:
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 42
          button: 0
          action: button_press
    action:
      - service: light.toggle
        target:
          entity_id: light.kitchen
```

### Different Actions for Short/Long Press
```yaml
automation:
  - alias: "Living Room - Short Press"
    trigger:
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 42
          button: 0
          action: button_release  # Short press
    action:
      - service: light.toggle
        target:
          entity_id: light.living_room
  
  - alias: "Living Room - Long Press"
    trigger:
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 42
          button: 0
          action: button_hold
    action:
      - service: light.turn_on
        target:
          entity_id: light.living_room
        data:
          brightness_pct: 100
```

## Using Developer Tools

You can monitor switch events in real-time:
1. Go to Developer Tools → Events
2. Under "Listen to events", enter: `casambi_bt_switch_event`
3. Click "Start Listening"
4. Press buttons on your switch to see the events

## Notes

- Switches do not appear as entities in Home Assistant
- The integration automatically filters duplicate events
- Some wireless switches may occasionally generate extra events, but this doesn't affect functionality
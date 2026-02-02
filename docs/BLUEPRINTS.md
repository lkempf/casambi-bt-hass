# Automation Blueprints

This integration includes several automation blueprints to make it easy to set up switch button automations.

## Available Blueprints

### 1. Casambi Button Toggle and Dim
**All-in-one light control** - Short press to toggle, hold to dim (recommended for lights)

[![Import Toggle and Dim Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Flkempf%2Fcasambi-bt-hass%2Fmain%2Fblueprints%2Fautomation%2Fcasambi_bt%2Fbutton_toggle_and_dim.yaml)

### 2. Casambi Button Actions
**Versatile button automation** - Configure any combination of press, release, hold, and continuous actions

[![Import Button Actions Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Flkempf%2Fcasambi-bt-hass%2Fmain%2Fblueprints%2Fautomation%2Fcasambi_bt%2Fbutton_short_long_press.yaml)

Available actions:
- **Button Press**: When button is initially pressed
- **Short Press**: Quick press and release (< 500ms)
- **Long Press Started**: When hold is detected (~500ms)
- **Long Press Released**: When button is released after hold
- **Continuous Hold**: Repeating action while held (requires helper)

### 3. Casambi Button Cover Control
**Smart blind/cover control** - Short press to open/close/stop, hold for continuous movement

[![Import Cover Control Blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2Flkempf%2Fcasambi-bt-hass%2Fmain%2Fblueprints%2Fautomation%2Fcasambi_bt%2Fbutton_cover_control.yaml)

## Installation

### Easy Method - Import Links
1. Click any of the "Import Blueprint" buttons above
2. This will open the blueprint import dialog in your Home Assistant
3. Click "Preview Blueprint" and then "Import Blueprint"
4. Create an automation from the imported blueprint

### Alternative Method - Manual
If the import buttons don't work or you prefer manual installation:

**For HACS installations:**
1. After installing/updating the integration, restart Home Assistant
2. The blueprints may appear in Settings → Automations & Scenes → Blueprints
3. If not, manually copy the `blueprints` folder to your HA config directory

**For manual installations:**
1. Copy BOTH folders to your Home Assistant config directory:
   - `custom_components/casambi_bt/` → `config/custom_components/casambi_bt/`
   - `blueprints/` → `config/blueprints/`
2. Restart Home Assistant
3. Go to Settings → Automations & Scenes → Blueprints

## Blueprint Configuration

### Helper Setup for Dimming/Cover Control
For blueprints that support continuous actions (Toggle and Dim, Cover Control), you need to create an input_text helper:
1. Go to Settings → Devices & Services → Helpers → Create Helper → Text
2. Name it something like "casambi_button_123_0_state" (for unit 123, button 0)
3. Use this helper in the blueprint configuration when setting up the automation

### Common Blueprint Features
All blueprints include:
- **Unit ID input**: Your switch's unit ID (see [how to find it](SWITCH_EVENTS.md#finding-your-switch-configuration))
- **Button number**: 0-based button index (test each button to find its number)
- **Message type filtering**: Optional, leave empty to accept any type
- **Debounce time**: Prevents duplicate triggers

## Example Automations

### Simple Toggle on Press
```yaml
automation:
  - alias: "Casambi Switch Button Press"
    mode: single  # Prevents duplicate executions
    trigger:
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 123  # Your switch unit ID
          button: 0     # Button number (0-based)
          action: button_press
    condition:
      # Prevent re-triggering within 2 seconds
      - condition: template
        value_template: >
          {{ (as_timestamp(now()) - as_timestamp(state_attr('automation.casambi_switch_button_press', 'last_triggered') | default(0))) > 2 }}
    action:
      - service: light.toggle
        target:
          entity_id: light.living_room
```

### Dimming with Hold (Single Automation with Helper)
```yaml
# First create an input_text helper for button state tracking:
# Settings → Devices & Services → Helpers → Create Helper → Text

automation:
  - alias: "Casambi Switch Dimming"
    mode: parallel  # Allow multiple instances
    trigger:
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 123  # Your switch unit ID
          button: 0     # Button number (0-based)
    condition:
      - condition: template
        value_template: >
          {{ trigger.event.data.action in ['button_hold', 'button_release_after_hold'] }}
    action:
      - choose:
          # Update helper on release
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.action == 'button_release_after_hold' }}"
            sequence:
              - service: input_text.set_value
                target:
                  entity_id: input_text.casambi_button_state
                data:
                  value: "released"
          # Start dimming on hold
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.action == 'button_hold' }}"
            sequence:
              - service: input_text.set_value
                target:
                  entity_id: input_text.casambi_button_state
                data:
                  value: "holding"
              - repeat:
                  while:
                    - condition: state
                      entity_id: input_text.casambi_button_state
                      state: "holding"
                  sequence:
                    - service: light.turn_on
                      target:
                        entity_id: light.living_room
                      data:
                        brightness: >
                          {% set current = state_attr('light.living_room', 'brightness') | default(0) %}
                          {% set new = current + 10 %}
                          {{ [new, 255] | min }}
                    - delay:
                        milliseconds: 200
```

### Different Actions for Short Press vs Long Press
```yaml
automation:
  - alias: "Casambi Switch Short Press"
    trigger:
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 123
          button: 0
          action: button_release  # Only triggered on quick press/release
    action:
      - service: light.toggle
        entity_id: light.living_room

  - alias: "Casambi Switch Long Press"
    trigger:
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 123
          button: 0
          action: button_release_after_hold  # Only triggered after holding
    action:
      - service: scene.turn_on
        entity_id: scene.movie_time
```

### Reliable Short Press (handles missed button_press events)
```yaml
automation:
  - alias: "Casambi Switch Toggle Light"
    mode: single
    trigger:
      # Trigger on both press and release for reliability
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 123
          button: 0
          action: button_press
      - platform: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 123
          button: 0
          action: button_release
    condition:
      # Prevent double triggers and ignore release after hold
      - condition: template
        value_template: >
          {{ 
            (trigger.event.data.action != 'button_release' or 
             (as_timestamp(now()) - as_timestamp(this.attributes.last_triggered | default(0))) > 2)
          }}
    action:
      - service: light.toggle
        entity_id: light.living_room
```
# Automation Blueprints

This integration includes ready-to-use blueprints for common switch automation patterns.

## Available Blueprints

### Toggle and Dim
**File:** `blueprints/automation/casambi_bt/button_toggle_and_dim.yaml`

All-in-one light control:
- Short press: Toggle light on/off
- Hold: Dim light up/down (alternates direction)

### Button Short/Long Press
**File:** `blueprints/automation/casambi_bt/button_short_long_press.yaml`

Versatile automation for any button event:
- Configure different actions for short press and long press
- Can control any entity or service

### Cover Control
**File:** `blueprints/automation/casambi_bt/button_cover_control.yaml`

Smart blind/cover control:
- Press: Open/close/stop (cycles through states)
- Hold: Continuous movement

## How to Use Blueprints

1. Copy the blueprint files to your Home Assistant `blueprints/automation/` directory
2. Go to Settings → Automations & Scenes → Blueprints
3. Find the blueprint and click "Create Automation"
4. Fill in the required fields:
   - Switch Unit ID
   - Button Number
   - Target entity/entities
5. Save the automation

## Finding Your Switch Details

To use these blueprints, you need:
- **Unit ID**: The ID of your switch (see [Switch Event Documentation](SWITCH_EVENTS.md))
- **Button Number**: 0-3 for 4-button switches (0 is typically the top button)

## Example Configuration

For a "Toggle and Dim" automation:
- Name: `Living Room Switch`
- Switch Unit ID: `42`
- Button Number: `0`
- Target Light: `light.living_room`
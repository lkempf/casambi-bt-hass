# Home Assistant integration for Casambi using Bluetooth

[![Discord](https://img.shields.io/discord/1186445089317326888)](https://discord.gg/jgZVugfx)

This is a Home Assistant integration for Casambi networks using Bluetooth. Since this is an unofficial implementation of the rather complex undocumented protocol used by the Casambi app there may be issues in networks configured differently to the one used to test this integration.
Please see the information below on how to report such issues.

A more mature HA integration for Casambi networks can be found under [https://github.com/hellqvio86/home_assistant_casambi](https://github.com/hellqvio86/home_assistant_casambi). This integration requires a network gateway to always connect the network to the Casambi cloud.

## Network configuration

See [https://github.com/lkempf/casambi-bt#casambi-network-setup](https://github.com/lkempf/casambi-bt#casambi-network-setup) for the proper network configuration. If you get "Unexcpected error" or "Failed to connect" different network configurations are the most common cause. Due to the high complexity of the protocol I won't be able to support all configurations allthough I might try if the suggested config doesn't work and the fix isn't to complex.

## Installation

### Manual

Place the `casambi_bt` folder in the `custom_components` folder.

### HACS

Add this repository as custom repository in the HACS store (HACS -> integrations -> custom repositories):

1. Setup HACS https://hacs.xyz/
2. Select HACS from the left sidebar
3. Search for `Casambi **Bluetooth**` in the searchbar at the top and select it. If you can't find it you might have to add this repository as a custom repository.
4. Click the Download button at the bottom right
5. Restart Home Assistant

## Features

Functionality exposed to HA:
- Lights
- Light groups
- Scenes

Supported control types:
- Dimmer
- White
- Rgb
- OnOff
- Temperature (Only for units since there are some open problems for groups.)
- Vertical

Not supported yet:
- Switches as entities (button events are available, see [Switch events](#switch-events))
- Sensors

## Switch events

Button actions from Casambi switches are fired on the Home Assistant event bus as `casambi_bt_switch_event`.
The integration only listens to these events. The switch keeps controlling the Casambi network as configured in the Casambi app.

Event data:

| Key | Description |
| --- | --- |
| `entry_id` | Config entry of the Casambi network that received the event. |
| `device_id` | Home Assistant device of the switch unit, or `null` if the unit has no device in Home Assistant. |
| `unit_id` | Id of the switch unit within the Casambi network. |
| `unit_uuid` | Unique id of the switch unit, or `null` if the unit is unknown. |
| `unit_name` | Name of the switch unit in the Casambi app, or `null` if the unit is unknown. |
| `button` | Button number reported by the switch (starting at 1). |
| `event` | One of `press`, `release`, `hold`, `release_after_hold` or `unknown`. |

Which `button` number belongs to which physical button depends on the switch.
Open *Developer tools* -> *Events*, listen to `casambi_bt_switch_event` and press the buttons to find the values for your switch.

Every press is forwarded, so quick presses can be combined in automations.
There is no native double click event. This example runs a different action for one or two short presses:

```yaml
alias: Casambi switch single or double press
mode: single
max_exceeded: silent
triggers:
  - trigger: event
    event_type: casambi_bt_switch_event
    event_data:
      unit_id: 12 # Replace with the values from your switch
      button: 1
      event: press
actions:
  - wait_for_trigger:
      - trigger: event
        event_type: casambi_bt_switch_event
        event_data:
          unit_id: 12
          button: 1
          event: press
    timeout:
      milliseconds: 600
    continue_on_timeout: true
  - if:
      - condition: template
        value_template: "{{ wait.trigger is not none }}"
    then:
      - action: light.toggle # Double press
        target:
          entity_id: light.example
    else:
      - action: scene.turn_on # Single press
        target:
          entity_id: scene.example
```

Long presses also start with a `press` event, so this example counts a long press as a single press.

## Linux / BlueZ passive scanning

When using a local Bluetooth adapter on Linux, passive scanning requires
BlueZ to expose the `org.bluez.AdvertisementMonitorManager1` D-Bus interface.

BlueZ currently marks this interface as experimental. On distributions where
experimental D-Bus interfaces are disabled, Home Assistant may therefore
detect the adapter as not supporting passive scanning and fall back to active
scanning.

If passive scanning is not available, enable the BlueZ experimental D-Bus
interfaces in `/etc/bluetooth/main.conf`:

```ini
[General]
Experimental = true
```

Then restart BlueZ and Home Assistant.

The interface can be checked with:

```
busctl introspect org.bluez /org/bluez/hci0 | grep AdvertisementMonitorManager
```

KernelExperimental does not need to be enabled for this.

## Reporting issues

Before reporting issues make sure that you have the debug log enabled for all relevant components. This can be done by placing the following in `configuration.yaml` of your HA installation:

```yaml
logger:
  default: info
  logs:
    CasambiBt: debug
    custom_components.casambi_bt: debug
```

The log might contain sensitive information about the network (including your network password and the email address used for the network) so sanitize it first or mail it to the address on my github profile referencing your issue.

## Development

When developing you might also want to change [https://github.com/lkempf/casambi-bt](casambi-bt). To make this more convenient run
```
pip install -e PATH_TO_CASAMBI_BT_REPO
```
in the homeassistant venv and then start HA with
```
hass -c config --skip-pip-packages casambi-bt
```

If you are unsure what these terms mean you might want to have a look at [https://developers.home-assistant.io/docs/development_environment](https://developers.home-assistant.io/docs/development_environment) first.

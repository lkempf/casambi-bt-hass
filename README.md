# Home Assistant integration for Casambi using Bluetooth

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
2. Go to integrations section.
3. Click on the 3 dots in the top right corner.
4. Select "Custom repositories"
5. Add the URL to the repository.
6. Select the integration category.
7. Click the "ADD" button.
8. Search for the Casambi **Bluetooth** integration and install it.

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

Not supported yet:
- Switches
- Sensors
- Additional control types (e.g. temperature, vertical, ...)
- Networks with classic firmware

## Reporting issues

Before reporting issues make sure that you have the debug log enabled for all relevant components. This can be done by placing the following in `configuration.yaml` of your HA installation:

```yaml
logger:
  default: info
  logs:
    CasambiBt: debug
    custom_components.casambi: debug
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

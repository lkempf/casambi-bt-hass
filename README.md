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

Home Assistant 2026.8 or newer is required.

### HACS

Add this repository as custom repository in the HACS store (HACS -> integrations -> custom repositories):

1. Setup HACS https://hacs.xyz/
2. Select HACS from the left sidebar
3. Search for `Casambi **Bluetooth**` in the searchbar at the top and select it. If you can't find it you might have to add this repository as a custom repository.
4. Click the Download button at the bottom right
5. Restart Home Assistant

## Configuration

The network is discovered automatically when a bluetooth adapter or proxy that supports connections is in range. It can also be added manually under *Settings -> Devices & services -> Add integration -> Casambi Bluetooth*, either with the Casambi cloud (address and password) or from an offline backup (see *Offline operation and offline backup*).

| Parameter | Description |
| --- | --- |
| Bluetooth MAC address | Address of the Casambi network, e.g. `AA:BB:CC:DD:EE:FF`. It is shown as network ID in the luminaire details of the Casambi app. Not asked for discovered networks. |
| Password | Password of the Casambi network (not of your Casambi account). |
| Import groups | Create entities for the groups of the network. Can be changed later under *Configure*; the integration reloads afterwards. |

The password can be changed with *Reconfigure* on the integration page. If the network rejects the stored password, Home Assistant asks for the new one (re-authentication). Both check the password with the Casambi cloud, so they need an internet connection. If the cloud temporarily refuses new logins, wait a few minutes and try again.

## Features

Functionality exposed to HA:
- Lights (one device per unit)
- Light groups
- Scenes
- Vertical (up/down light distribution) of units and groups as number entities
- Connection status of the network as diagnostic binary sensor

Supported control types:
- Dimmer
- White
- Rgb
- OnOff
- Temperature
- XY
- Vertical

Not supported yet:
- Switches
- Sensors

### Data updates

The integration keeps a bluetooth connection to the network and receives state changes as they happen (push). No polling is done. If the connection drops, it reconnects automatically with increasing delays and logs the loss and the recovery once at info level. All entities are unavailable while the connection is down.

The configuration of the network (units, groups, scenes and the keys of the network) comes from the Casambi cloud and is cached. Home Assistant starts the network from this cache and checks the cloud in the background right after the start and every 24 hours. When the configuration changed in the Casambi app, the integration reloads by itself: new units appear and deleted ones are removed.

### Use cases and examples

- Control Casambi luminaires from dashboards, automations and voice assistants together with the rest of your home.
- Switch to a Casambi scene at sunset, e.g. with the action `scene.turn_on` and the target `scene.<network>_<scene>`.
- Notify yourself when the network goes offline with a state trigger on the network's status sensor.

### Known limitations

- Units and groups that are added or deleted in the Casambi app are applied within 24 hours (or at the next start) by an automatic reload, which needs the Casambi cloud.
- The first setup needs the Casambi cloud or an offline backup.
- Group states are derived from the first unit with a matching control, so mixed groups may show the values of a single unit.
- Only network configurations described in the casambi-bt documentation are supported (see *Network configuration*).
- Switches and sensors of the network are not exposed yet.

## Offline operation and offline backup

The bluetooth connection never needs the Casambi cloud. The cloud is only needed to download the configuration of the network, so after the first setup the network keeps working without internet and without the Casambi cloud, also across restarts.

To be safe even if the cache is lost while the cloud is gone (e.g. a new Home Assistant installation), export an **offline backup**:

1. Go to *Developer tools -> Actions* and run the action *Casambi Bluetooth: Export offline backup* (`casambi_bt.export_offline_backup`) for your network. Only administrators can run it.
2. Copy the complete response and store it in your password manager, e.g. as a secure note or file.

**The backup contains the keys of your network. Treat it like a password.**

To restore it:

- On a new installation, add the integration and choose *Offline backup*. Paste the backup. The password is optional; it is only needed to get changes from the Casambi cloud later.
- For a configured network, use *Reconfigure -> Restore an offline backup* on the integration page.

A backup stays valid as long as the network isn't changed in the Casambi app. Export a new one after changes.

### Actions

| Action | Description |
| --- | --- |
| `casambi_bt.export_offline_backup` | Returns the offline backup of a network (field `config_entry_id`). Administrators only. |

## Troubleshooting

- **No connectable bluetooth adapter**: a repair issue is shown when Home Assistant has no local adapter or active bluetooth proxy that supports connections. Add one; the integration connects as soon as it is available.
- **Failed to connect / network not found**: make sure the network is in range of an adapter or proxy and configured as described under *Network configuration*.
- **Invalid authentication**: use *Reconfigure* or the re-authentication prompt to enter the current network password. The network keeps working from its cached configuration meanwhile.
- **Network isn't cached and the cloud can't be used**: the first start needs the Casambi cloud or an offline backup. Restore a backup with *Reconfigure -> Restore an offline backup*.
- **Cloud refuses logins / password can't be verified**: the Casambi cloud limits new logins. The integration keeps retrying on its own; for the setup dialogs wait a few minutes and check the internet connection.
- **Diagnostics**: download them from the integration page; passwords and addresses are redacted.
- For anything else enable the debug log as described under *Reporting issues*.

## Removal

1. Go to *Settings -> Devices & services* and select *Casambi Bluetooth*.
2. Open the menu of the network entry and select *Delete*. This also removes the cached network data.
3. If installed with HACS, remove the integration in HACS and restart Home Assistant. For a manual installation, delete the `custom_components/casambi_bt` folder and restart.

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

1. Download the diagnostics on the integration page (*Download diagnostics* in the menu of the network entry) and attach them. Passwords, addresses and network identifiers are redacted.
2. Enable the debug log of the integration and reproduce the problem:

    ```yaml
    logger:
      default: info
      logs:
        custom_components.casambi_bt: debug
    ```

3. Only if asked for it, also enable `CasambiBt: debug` for the library. **Warning:** the library then logs the complete network configuration received from the Casambi cloud, including the keys of your network, and possibly your email address. Never post this log publicly; mail it to the address on my GitHub profile referencing your issue, or remove the `Network:` lines first.

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

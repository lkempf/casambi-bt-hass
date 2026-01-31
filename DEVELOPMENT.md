# Development

This document explain the recommended development workflow as well as a possible setup for development. This workflow includes both the integration (this repository) as well as the interfacing library [casambi-bt](https://github.com/lkempf/casambi-bt).

## Workflow

This repository uses two important branches `main` and `dev`. Everything happening in `main` should be tested and planned for the next release. Changes that haven't been tested properly should only be applied to `dev`.

If you want to contribute changes you should always target `dev`. The changes will be merged over to `main` in time for the next release.

All changes on `main` and `dev` must conform to the coding standards used for this repository. This will be verified automatically when creating a pull request but to avoid delays the necessary checks should be performed beforehand. This means running

```bash
ruff check .
black . --check
```

For `casambi-bt` also run `mypy .`. All of these tools should be available when following the recommended setup procedure.

## Development setup

The development setup has the following goals:

1. Using arbitrary versions of homeassistant, even those that haven't been released.
1. Testing changes to `casambi-bt` locally without requiring an upload to pip.
1. Running homeassistant on the same device used for development, so no hassle with remote debugging.

This of course also has a caveat: The devices used for development must have proper bluetooth support and must be running Linux. If this isn't possible natively it can be achieved using a [bluetooth dongle](https://www.home-assistant.io/integrations/bluetooth/#known-working-high-performance-adapters). I haven't tested the setup under WSL (for those developing under Windows).

The development setup uses venvs. This is a feature that creates an isolated environment for python dependencies so multiple versions of the same dependency can be installed at the same time and changes don't affect your whole system. Understanding venvs isn't necessary to follow the setup instructions. If a venv breaks, it can easily be recreated by removing its folder (normally `venv` or `.venv`), creating a new one and reinstalling all dependencies according to the setup instructions.

These instructions assume the following folder layout (after all steps). This can of course be changed but then some commands have to be modified.

```text
casambi/
├── core/
├── casambi-bt/
└── casambi-bt-hass/
```

### Getting homeassistant

Follow the official instructions for installing homeassistant in a manual environment: [https://developers.home-assistant.io/docs/development_environment](https://developers.home-assistant.io/docs/development_environment).

As a very quick summary this means:

```bash
git clone https://github.com/home-assistant/core.git
cd core
script/setup
source venv/bin/activate
```

After this the venv used by homeassistant is activated in the current shell, so all changes to dependencies now will changes those in use by homeassistant. If you open a new shell, make sure to execute `source venv/bin/activate` again in the correct directory, otherwise homeassistant won't start.

To make sure that homeassistant is running properly you can test it using

```bash
hass -c config
```

### Getting casambi-bt

Next you need to obtain `casambi-bt` by source. To do so use

```bash
git clone https://github.com/lkempf/casambi-bt.git
git checkout dev
```

If you want to use your own fork, the remote needs to be changed of course. Just remember to base your working branch on the `dev` branch.

You now need to install this folder as a dependency for homeassistant so that it is preferred over the newest published version. Make sure that the shell you are using has the correct venv activated (see [Getting homeassistant](#getting-homeassistant)) and that you have navigated to the project root.

```
pip install -e casambi-bt/
```

Some (optional) explanation of what this does: `pip` is used for managing dependencies for python libraries. Since you made sure that you are in a venv pip is aware of that and will install the library into the venv. Normally dependencies are downloaded from PyPi but by using `-e` you tell pip to use a folder as a source instead. This also means that any changes done to that folder are automatically picked up by homeassistant (after a restart) so that there is no need to reinstall anything after a change.

### Getting casambi-bt-hass

For the integration you also probably want homeassistant to always pick up your latest changes. Thankfully, this is easier as with dependencies. You can simply link the folder containing the integration into your development installation of homeassistant with the following commands:

```bash
git clone https://github.com/lkempf/casambi-bt-hass.git
cd core/config/
mkdir custom_components
cd custom_components
ln -s ../../../casambi-bt-hass/custom_components/casambi_bt
```

### Starting homeassistant

Homeassistant is now ready to be started using your development version. To avoid overwriting the development version of `casambi-bt` homeassisant should always be started using

```bash
hass -c config --skip-pip-packages casambi-bt
```

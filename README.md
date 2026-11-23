# NAOS Base System Build

**English** | [简体中文](README.zh-CN.md)

NAOS is a general-purpose Linux system for embedded devices, with container support, A/B system updates, factory reset, and device management.

This repository uses **Yocto Wrynose 6.0 LTS** to build system components and assembles userspace from Alpine APK packages. BSP layers provide hardware support, including the boot process, drivers, and security features available on each board.

## Quick start

### Try NAOS without compiling

Use the [NAOS image generator (naos-firmware-generator)](https://github.com/mixtile-rockchip/naos-firmware-generator) to select a supported board and a released version, generate firmware, and flash your device. No system source compilation is needed. See the image generator's README for instructions.

### Build from source

Use this repository to modify the BSP or build the system yourself.

First [prepare the build host](doc/build.md#prepare-the-host), then clone the project as a regular user:

```bash
git clone https://github.com/mixtile-rockchip/naos-base-system-build.git
cd naos-base-system-build
```

Choose a board from [Supported hardware](#supported-hardware) and prepare the required [keys and certificates](doc/keys.md). The following example uses the currently supported Mixtile Core3588E:

```bash
export MACHINE=mixtile-core3588e-v2
kas build kas/development.yml
```

Build output is placed in the board's directory. See [Build output](doc/build.md#build-output) for the available files:

```text
build/tmp/deploy/images/<MACHINE>/
```

To generate update bundles, prepare the OTA signing material and enable `NAOS_OTA`:

```bash
NAOS_OTA=1 kas build kas/development.yml
```

Before building a production image, prepare the security material required by the board and the SSH login certificates:

```bash
kas build kas/production.yml
```

See the [build guide](doc/build.md) for commands, feature flags, cache directories, and output files.

## Development and production

| Feature | Development | Production |
| --- | --- | --- |
| Secure boot | Disabled by default | Enabled by default; requires board support |
| Disk encryption | Disabled by default | Enabled by default; requires board support |
| RAUC bundle signature verification | Enabled | Enabled |
| SSH login | User `naos`, default password `naos` | Certificates signed by a trusted SSH user CA; login as `root` or `naos` |
| Serial console | Interactive access for development and debugging | Boot logs only; input and login disabled |
| SBOM | Disabled by default | Enabled by default |
| OTA bundle generation | Disabled by default; optional | Disabled by default; optional |

Environment variables control secure boot, disk encryption, SBOM, and OTA generation. Hardware support and configuration depend on the board. See the [build guide](doc/build.md) and [keys and certificates](doc/keys.md).

## System features

- Alpine userspace with OpenRC service management, SSH, Avahi, time synchronization, and basic administration tools.
- RAUC A/B system updates through local and cloud interfaces; Bootloader updates depend on board support.
- Local commands, a Unix socket, and an optional HTTP interface through `naos-core` for updates, factory reset, OEM information, mDNS, and watchdog management.
- NAOS Container integration for deploying container applications from a manifest.
- A userdata filesystem initialized on first boot, with configuration and application data preserved on subsequent boots.
- Secure boot and disk encryption for production images on supported boards.
- Optional system and component SBOMs covering the firmware, APK packages, and kernel modules actually delivered.

## Generate and customize images

Use this repository when changing the BSP or rebuilding system components.

To customize a device tree, preinstall application APKs, or replace system configuration using released components, use the independent [image generator](https://github.com/mixtile-rockchip/naos-firmware-generator). Supported boards and image types depend on the generator and available releases.

## Supported hardware

- **Mixtile Core3588E core board**: `MACHINE=mixtile-core3588e-v2`

## User documentation

- [Documentation index](doc/README.md)
- [Build environment, feature flags, and output](doc/build.md)
- [Keys and certificates](doc/keys.md)
- [Login, system configuration, and daily use](doc/runtime.md)
- [Updates, slots, and factory reset](doc/updates.md)

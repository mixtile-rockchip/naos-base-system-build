# NAOS user documentation

**English** | [简体中文](README.zh-CN.md)

[Project home](../README.md)

## Get started

| Task | Documentation |
| --- | --- |
| Generate firmware and try NAOS without compiling | [Image generator](https://github.com/mixtile-rockchip/naos-firmware-generator) |
| Prepare a host and build development or production images | [Build guide](build.md) |
| Prepare OTA, secure boot, encryption, and login material | [Keys and certificates](keys.md) |
| Log in and configure Core, container applications, and local network services | [Runtime guide](runtime.md) |
| Install local or cloud updates, inspect slots, and reset the device | [Updates and recovery](updates.md) |

Start with a development image for your first build. Prepare the production keys and login certificates before building and deploying a production image.

## Choose a build method

**Yocto builds** are for changes to the BSP, kernel configuration, base package selection, or system integration. Use the two kas entry points in the project root. See the [build guide](build.md).

The **image generator** uses released components to generate firmware without compiling the system source. It also supports custom device trees, configuration, and applications. See the [independent project](https://github.com/mixtile-rockchip/naos-firmware-generator).

Both methods can generate full firmware images and RAUC update bundles. Bundle signing keys must match the trust certificate installed on the device. Production boot signing keys and system encryption keys must also remain compatible with existing devices.

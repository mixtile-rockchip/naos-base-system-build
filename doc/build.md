# Building NAOS

**English** | [简体中文](build.zh-CN.md)

[Project home](../README.md) · [Documentation](README.md)

## Prepare the host

Ubuntu 24.04 x86_64 with Python 3.11 or newer is recommended. Install a version of kas that supports configuration format 14. Run kas and BitBake as a regular user.

Install the basic dependencies from the Ubuntu package repositories:

```bash
sudo apt-get update
sudo apt-get install build-essential chrpath cpio debianutils diffstat file gawk \
  git iputils-ping kas libacl1 libcrypt-dev locales python3 python3-git \
  python3-jinja2 python3-pexpect python3-pip python3-subunit socat texinfo \
  unzip util-linux wget xz-utils zstd qemu-user-static binfmt-support
sudo locale-gen en_US.UTF-8
export LANG=en_US.UTF-8
```

The host must also allow unprivileged users to create namespaces and execute programs for the target architecture through QEMU/binfmt:

- Alpine system assembly uses user, mount and IPC namespaces; on a regular host, it also uses a PID namespace.
- The QEMU/binfmt registration must match the target architecture, be enabled and include the `F` flag. Current Mixtile Core3588E builds use ARM64. Installing the QEMU files alone does not confirm that target execution works.
- These requirements also apply when building in a container. The build user must have read and write access to the working directory and cache directories.

Check namespace support first:

```bash
unshare --user --map-root-user --mount --ipc --pid --fork --mount-proc true
```

If the command reports a permission error, check the build environment's user namespace settings and security policy. Do not run BitBake as root to bypass the check.

## Get the project

```bash
git clone https://github.com/mixtile-rockchip/naos-yocto.git
cd naos-yocto
```

kas fetches the build layers automatically. The BSP and security tools use pinned commits from remote Git repositories; Core and Container use release archives with fixed versions and checksums. A local vendor SDK is not required, and Core and Container do not need to be compiled in this repository.

Before building, prepare the materials required for your build mode as described in [Keys and certificates](keys.md).

## Select the board and version

The currently supported core module and its build name are:

- **Mixtile Core3588E**:`mixtile-core3588e-v2`

Select the board through `MACHINE`:

```bash
export MACHINE=mixtile-core3588e-v2
```

This board is also the default. Additional boards need a corresponding machine configuration; changing the name alone is not sufficient.

You can set a release version and build identifier for a build:

```bash
export NAOS_VERSION=1.0.0
export NAOS_BUILD_ID=release-1.0.0
```

When unset, the version defaults to `2.0.0-dev` and the build identifier uses the version. Device version information, update bundles and release manifests record these values.

## Development and production

Build a complete development image:

```bash
kas build kas/development.yml
```

Build a complete production image:

```bash
kas build kas/production.yml
```

Development builds disable secure boot and disk encryption by default while retaining RAUC update signature verification. Production builds enable secure boot, system and userdata encryption, and SBOM generation by default. They use SSH certificate authentication and a serial console that only outputs logs.

## Generate update bundles

Set `NAOS_OTA=1` to generate both system and Bootloader update bundles alongside the complete image:

```bash
NAOS_OTA=1 kas build kas/development.yml
NAOS_OTA=1 kas build kas/production.yml
```

The OTA signing private key is required only when generating update bundles. A normal complete image build still requires the OTA verification certificate.

## Export image generator inputs

```bash
NAOS_PACKAGE=1 kas build kas/development.yml
NAOS_PACKAGE=1 kas build kas/production.yml
```

This mode exports intermediate inputs for the standalone image generator, which performs final image encryption, boot signing and OTA packaging. It does not also generate complete firmware or final OTA bundles.

Output directories:

```text
package/development/mixtile-core3588e-v2/
package/production/mixtile-core3588e-v2/
```

Exports still require the OTA verification certificate; production exports also require the SSH CA public key. Prepare the boot signing private key, system encryption key and OTA signing private key in the image generator for final assembly.

## Feature switches

Boolean switches accept `0` or `1`. Environment variables override the defaults in the selected kas configuration.

| Environment variable | Development default | Production default | Purpose |
| --- | --- | --- | --- |
| `NAOS_OTA` | `0` | `0` | Generate both system and Bootloader update bundles |
| `NAOS_PACKAGE` | `0` | `0` | Export image generator inputs |
| `NAOS_SBOM` | `0` | `1` | Generate a software bill of materials |
| `NAOS_SECURE_BOOT` | `0` | `1` | Sign and verify boot images |
| `NAOS_DISK_ENCRYPTION` | `0` | `1` | Enable both system and userdata encryption |
| `NAOS_SYSTEM_ENCRYPTION` | Follows the disk encryption switch | Follows the disk encryption switch | Select system encryption independently |
| `NAOS_DATA_ENCRYPTION` | Follows the disk encryption switch | Follows the disk encryption switch | Select userdata encryption independently |
| `NAOS_SECURE_BOOT_BURN_KEY_HASH` | `0` | `0` | Make the generated SPL program the OTP/eFuse public-key hash on the device |

Secure boot, disk encryption and OTP/eFuse require board support. In the current Mixtile Core3588E implementation, system encryption also requires userdata encryption. The OTP/eFuse switch is valid only with both the production configuration and secure boot enabled, and remains disabled by default. See [Keys and certificates](keys.md#otpefuse-public-key-hash) for the required materials.

For example, generate an SBOM for a development build:

```bash
NAOS_SBOM=1 kas build kas/development.yml
```

## Caches and repeat builds

The download cache defaults to `downloads/` in the project directory, and the shared build cache is `sstate-cache/`. Keep these directories to reuse them in subsequent builds.

You can set either location independently. The build user must have read and write access to the directories:

```bash
export DL_DIR="$HOME/.cache/naos/downloads"
export SSTATE_DIR="$HOME/.cache/naos/sstate-cache"
mkdir -p "$DL_DIR" "$SSTATE_DIR"
kas build kas/development.yml
```

Sources and build outputs are stored in `sources/` and `build/`, respectively. You do not need to delete them before another build.

## Build output

Normal build outputs are in `build/tmp/deploy/images/$MACHINE/`:

| File | Purpose |
| --- | --- |
| `naos-$MACHINE.img` | Complete firmware image for flashing |
| `MiniLoaderAll.bin` | Rockchip USB Loader |
| `partition-layout.json`, `manifest.json` | Partition layout and artifact information |
| `naos-bundle-$MACHINE-$NAOS_VERSION.raucb` | System, kernel and ramdisk update bundle; generated when OTA is enabled |
| `naos-bootloader-bundle-$MACHINE-$NAOS_VERSION.raucb` | Bootloader update bundle; generated when OTA is enabled |
| `naos-*.spdx.json`, `naos-*.sbom.json` | SBOM and summary; generated when SBOM is enabled |
| `naos-*.kernel.config`, `naos-*.uboot.config` | Corresponding build configurations; generated when SBOM is enabled |

Use `.raucb` bundles for system updates and the complete `.img` for flashing. See [Updates and recovery](updates.md) for installation instructions.

## Use a published version

To generate firmware without building it yourself, select a published version in the [image generator](https://github.com/mixtile-rockchip/naos-genimage). Published artifacts are available from `https://packages.naos.ai/repository`.

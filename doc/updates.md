# Updates and recovery

**English** | [简体中文](updates.zh-CN.md)

[Documentation](README.md) · [Project home](../README.md) · [Login and runtime configuration](runtime.md)

## Choose an update bundle

NAOS uses signed RAUC bundles for updates. Both development and production images verify update bundle signatures.

| Update bundle | Contents | System slot behavior |
| --- | --- | --- |
| `naos-bundle-<machine>-<version>.raucb` | rootfs and a boot image containing the kernel, device tree, and ramdisk | Writes the inactive system slot, then attempts to boot it |
| `naos-bootloader-bundle-<machine>-<version>.raucb` | U-Boot and IDBlock | Keeps the current system slot |

Choose a release bundle that matches the device's board, partition layout, and development or production configuration. The device's OTA verification certificate, secure boot keys, and system encryption keys must match its release chain. See [Keys and certificates](keys.md).

System and bootloader bundles can be released and installed separately. See [Get the source and build](build.md) for the build options and output paths used to generate them.

## Update from a local file

First copy the update bundle to the device. On a development image, you can upload it to the `naos` user's home directory:

```sh
scp naos-bundle-mixtile-core3588e-v2-1.0.0.raucb naos@192.0.2.10:~/update.raucb
```

The filename, version, and `192.0.2.10` are examples; replace them with your actual values. On a development image, log in as `naos` and use sudo for administrative commands. On a production image, use the appropriate SSH user certificate to upload directly to `/userdata` as root. See [Runtime configuration](runtime.md#log-in-to-the-device) for login instructions.

If you uploaded the file to the development user's home directory as above, move it to `/userdata` on the device as root, then start the update:

```sh
mv /home/naos/update.raucb /userdata/update.raucb
naos-core update /userdata/update.raucb
```

The command returns a task ID, and installation continues in the background. Query the task:

```sh
naos-core tasks
naos-core task <TASK_ID>
```

View the current update status:

```sh
naos-core update
```

You can also assign a request ID so the caller can identify the same operation:

```sh
naos-core update --request-id release-1.0.0 /userdata/update.raucb
```

Use the same command for a bootloader update, replacing the path with the corresponding `.raucb` file.

After a successful installation, Core automatically requests a reboot. After rebooting from a system bundle update, `naos-boot-confirm` checks and confirms the booted system slot. A task state of `boot_confirmed` means post-reboot confirmation is complete. Installation and successful boot of the new system are separate stages; callers should keep checking the task result.

## Update through the local HTTP API

Applications on the device can submit the absolute path of an existing local update bundle to `POST /v1/update`. Set `Content-Type: application/json`. Example request body:

```json
{
  "path": "/userdata/update.raucb",
  "request_id": "release-1.0.0"
}
```

Once the request is accepted, the API returns HTTP `202` and a task ID. Installation continues in the background. Query the result with `GET /v1/update-tasks/<TASK_ID>`, or list tasks with `GET /v1/update-tasks`.

This endpoint takes a local file path. The application must finish downloading or transferring the file before calling it. HTTP listens on `127.0.0.1:8890` by default. See [Runtime configuration](runtime.md#local-control-interfaces) for how to enable it.

## Update through hawkBit

Enter the connection settings in `/etc/naos-core/device.ini` on the device:

```ini
[hawkbit]
enabled=true
server_url=https://updates.example.com
tenant=DEFAULT
controller_id=
token=REPLACE_WITH_GATEWAY_TOKEN
token_type=gateway
poll_interval=4m
timeout=30s
download_path=/var/lib/naos-core/downloads
```

Replace the server address, tenant, and GatewayToken, and enable GatewayToken authentication on the server. `server_url` is the server address and may include a port. The client handles the tenant and API paths.

When using the same image on multiple devices, leave `controller_id` empty. When first enabled, each device generates and saves its own ID: an SN-derived ID if a valid SN is available, or a random ID otherwise. A device-specific TargetToken can be used with `token_type=target`.

After changing the configuration, restart Core and check the connection:

```sh
rc-service naos-core restart
naos-core hawkbit check
naos-core hawkbit status
```

Core maintains contact with the platform and schedules the updater according to its configuration. `poll_interval` controls the interval between updater runs. Platform heartbeat requests follow the polling interval returned by the server.

You can also register the device manually by explicitly providing the server, tenant, and GatewayToken:

```sh
naos-core hawkbit register \
  --server https://updates.example.com \
  --tenant DEFAULT \
  --token REPLACE_WITH_GATEWAY_TOKEN
```

After successful registration, the connection settings and device ID are saved to the same `device.ini`, and `enabled=true` is set automatically. A running Core applies the new configuration without a service restart. If an updater is active, the configuration takes effect after it finishes. If Core is stopped, the configuration takes effect at its next startup.

Core reports the current system version, build ID, and base OS information. It sends these attributes on the first successful connection, when version information changes, or when the platform requests device attributes. Regular heartbeats do not upload them again. Failed reports are retried on subsequent heartbeats. In the device details, “Current system version” shows the firmware version reported by the device; Assigned and Installed continue to show the software versions assigned and confirmed as installed by the platform.

Upload a system or bootloader RAUC bundle that matches the device to hawkBit, then assign an update task through the platform. The device uses the same signature verification and installation process. Bootloader bundles keep the current system slot.

## Inspect A/B status

```sh
rauc status
```

The system uses A/B groups of boot and system partitions. Each boot slot contains the kernel, device tree, and ramdisk, and each system slot contains the corresponding system image. U-Boot selects the boot slot using RAUC slot state and the remaining boot attempt counts. The system confirms the current slot after a successful boot.

By default, each boot slot is 50 MiB and each system slot is 200 MiB. You can adjust these sizes before generating the full image; see [Build instructions](build.md). **OTA does not resize existing partitions on the device**. The bundle payloads must fit their target partitions.

Both A/B groups share the persistent data partition. System updates and slot switches retain userdata, the writable layer for system configuration, container data, and device key material. If a new version changes an existing data format, the corresponding application must handle compatibility.

## Factory reset

Before resetting, copy any files you want to keep off the device. A reset clears all data in userdata, including:

- User files and application data.
- Writable root filesystem configuration, including installed software and local changes.
- Container data, Core state, and update tasks saved on the device.
- SSH host keys generated at runtime and locally configured connection settings.

Run as root:

```sh
naos-core recovery
```

Applications on the device can also request a reset through `POST /v1/recovery`.

Core writes the reset request and reboots. On the next boot, the system mounts userdata and clears its contents before mounting the root overlay, then continues booting normally. The partitions and filesystem are retained, as are the firmware, U-Boot slot selection environment, and key material in misc. Encrypted data volumes keep their existing unlock material.

After a reset, the system uses the image's default configuration. Platform settings included in the image remain; settings stored only in the device's writable layer are cleared. The device may receive a new IP address, and SSH host keys are regenerated. Verify the device identity and new host key fingerprint when reconnecting.

# Login and runtime configuration

**English** | [简体中文](runtime.zh-CN.md)

[Documentation](README.md) · [Project home](../README.md) · [Updates and recovery](updates.md)

## Log in to the device

The device obtains its IP address through DHCP. Replace the example address `192.0.2.10` below with the device's actual address.

### Development image

The default username and password are both `naos`:

```sh
ssh naos@192.0.2.10
```

Use `sudo` for administrative commands and enter the `naos` user's password. For example:

```sh
sudo rc-service naos-core status
```

The development image provides an interactive serial console and debugging access. The serial baud rate is `1500000`.

### Production image

The production image requires a user certificate signed by a trusted SSH CA. With your private key and its corresponding certificate, log in as follows:

```sh
ssh -i "$HOME/.ssh/device" \
  -o CertificateFile="$HOME/.ssh/device-cert.pub" \
  root@192.0.2.10
```

To log in as `root`, the certificate must include the `root` principal; to log in as `naos`, it must include `naos`. One certificate can contain both principals. The image includes the CA public key. User private keys and the CA private key remain with their respective holders. See [Keys and certificates](keys.md).

The production image disables SSH password authentication and the interactive serial console, while retaining boot logs. The `naos` user remains available, but passwordless sudo is not configured. For administration, log in with a certificate that includes the `root` principal.

Run the device management commands below as root. When logged in as a regular user on the development image, prefix them with `sudo`.

## Inspect the system and services

```sh
cat /usr/lib/naos/version.json
uname -r
ip address
rc-status
```

Common services:

| Service | Purpose |
| --- | --- |
| `naos-core` | Local control, update scheduling, device identity, and watchdog management |
| `naosd` | Container runtime service |
| `rauc` | System and bootloader update installation |
| `naos-boot-confirm` | Boot checks and confirmation of the current slot |
| `avahi-daemon` | Host and service discovery on the LAN |
| `sshd` | SSH access |
| `ntpd` | Network time synchronization |

Check the status or restart a service:

```sh
rc-service naos-core status
rc-service naos-core restart
```

View system and kernel logs:

```sh
tail -f /var/log/messages
dmesg
```

## Configure NAOS Core

Core uses a single configuration file:

```text
/etc/naos-core/device.ini
```

The file contains device connection credentials and has default permissions of `0600`. Edit it as root, then restart the service to apply your changes:

```sh
vi /etc/naos-core/device.ini
rc-service naos-core restart
```

The factory configuration lists all fields. Common settings are:

| Setting | Default | Purpose |
| --- | --- | --- |
| `[platform] soc` | `rk3588` | Select the OEM information reader |
| `[http] enabled` | `true` | Enable the local TCP HTTP interface |
| `[http] listen` | `127.0.0.1:8890` | HTTP listen address |
| `[core] socket` | `/run/naos/core.sock` | Control socket used by the local CLI |
| `[container] enabled` | `false` | Have Core submit the application manifest |
| `[container] manifest` | `/userdata/manifest.yml` | Application configuration file path |
| `[boot] check_enabled` | `false` | Enable a custom boot check |
| `[boot] check_program` | Empty | Path to the boot check program or script |
| `[hawkbit] enabled` | `false` | Enable cloud updates; see [Updates and recovery](updates.md#update-through-hawkbit) |
| `[watchdog] enabled` | `true` | Have Core take over and maintain the hardware watchdog |
| `[watchdog] timeout` | `30s` | Requested hardware timeout |
| `[watchdog] feed_interval` | `10s` | Core watchdog feed interval |

### Local control interfaces

View command help:

```sh
naos-core --help
naos-core containers --help
naos-core hawkbit --help
```

Common operations:

```sh
naos-core oem
naos-core containers
naos-core containers start app1
naos-core containers stop app1
naos-core containers restart app1
naos-core reboot
```

HTTP listens on a loopback address by default. Set the following in the existing INI file's `[http]` section to disable the TCP HTTP interface. The local CLI remains available through the socket:

```ini
[http]
enabled=false
```

`listen` accepts only numeric loopback addresses, such as `127.0.0.1:8890` or `[::1]:8890`.

When HTTP is enabled, `GET /healthz` returns `{"status":"ok"}`. Applications on the device can use endpoints such as `/v1/oem`, `/v1/containers`, `/v1/update`, `/v1/reboot`, and `/v1/recovery` for the corresponding operations. See [Updates and recovery](updates.md) for the update API behavior.

### Watchdog

```ini
[watchdog]
enabled=true
device=/dev/watchdog0
timeout=30s
feed_interval=10s
```

Watchdog support requires the appropriate hardware and driver for the board. When watchdog management is enabled, Core checks its own health and feeds the watchdog at the configured interval. The actual hardware timeout is the value returned by the driver. Whether U-Boot enables the watchdog during boot, and whether the kernel keeps feeding it until userspace takes over, depends on the board's BSP configuration.

`enabled=false` disables only Core's watchdog management. If the board enables the watchdog during boot, its subsequent behavior depends on the bootloader and kernel configuration. Disabling the HTTP service does not affect Core's watchdog health checks.

### Start applications from a manifest

Prepare the application configuration and container root filesystem:

```text
/userdata/
├── manifest.yml
└── rootfs/
```

Enable it in the Core configuration:

```ini
[container]
enabled=true
address=/var/run/naosd.sock
manifest=/userdata/manifest.yml
```

After initializing the device identity and hostname, Core submits this manifest to naosd. Use a manifest format supported by NAOS Container. The manifest's `meta.id` specifies the container ID.

This switch controls only whether Core submits the manifest. naosd's existing container autostart policy remains independent. Disabling the switch leaves running containers active; stop one with `naos-core containers stop <CONTAINER_ID>`.

### Custom boot checks

Basic system boot checks run by default. To wait for the application to become ready before confirming the system slot, set:

```ini
[boot]
check_enabled=true
check_program=/userdata/boot-check.sh
```

The check program must be executable. Scripts need a shebang, such as `#!/bin/sh`. The check passes if the program returns `0` within 60 seconds. A nonzero exit, timeout, or execution failure prevents boot confirmation for this attempt.

```sh
chmod +x /userdata/boot-check.sh
naos-core boot-check
```

Run `boot-check` on its own to inspect the result without changing RAUC slot state. During boot, `naos-boot-confirm` performs the system checks and confirms the current slot. An application check script can wait for containers to become ready within the allowed time.

### Hostname and LAN discovery

With the RK3588 configuration, Core reads the OEM SN at startup. A valid SN becomes the hostname; if no usable SN is available, the existing hostname is retained. Avahi provides LAN discovery using the system hostname.

Applications can submit a service declaration locally through `PUT /v1/mdns/services/<SERVICE_ID>`, with `name`, `type`, `port`, and optional `txt` fields. Set `Content-Type: application/json`. Example request body:

```json
{
  "name": "NAOS Device",
  "type": "_mlp._tcp",
  "port": 1886,
  "txt": {
    "role": "idle",
    "add": "true"
  }
}
```

Avahi publishes the declaration. Submitting the same information again preserves the existing configuration. The declaration remains after the application stops; remove it with `DELETE /v1/mdns/services/<SERVICE_ID>`.

## Persistent data

The userdata filesystem is created on first boot. Later boots reuse it and retain configuration and application data. When encryption is enabled, the system unlocks the volume before mounting the filesystem.

Writable changes to the root filesystem and data under `/userdata` are stored on the persistent partition and shared by both A/B system slots. After an update, previously modified configuration may continue to override the new system's defaults. See [Updates and recovery](updates.md#factory-reset) for the scope of a factory reset.

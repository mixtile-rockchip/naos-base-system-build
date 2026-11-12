# 登录与运行配置

[English](runtime.md) | **简体中文**

[文档首页](README.zh-CN.md) · [项目首页](../README.zh-CN.md) · [升级与恢复](updates.zh-CN.md)

## 登录设备

设备通过 DHCP 获取地址。下例中的 `192.0.2.10` 为示例地址，请替换为设备实际地址。

### 开发版

默认用户名和密码均为 `naos`：

```sh
ssh naos@192.0.2.10
```

需要管理权限时使用 `sudo`，输入 `naos` 用户的密码。例如：

```sh
sudo rc-service naos-core status
```

开发版保留串口交互和调试入口，串口波特率为 `1500000`。

### 量产版

量产版使用受信任 SSH CA 签发的用户证书登录。准备个人私钥及对应证书后，例如：

```sh
ssh -i "$HOME/.ssh/device" \
  -o CertificateFile="$HOME/.ssh/device-cert.pub" \
  root@192.0.2.10
```

登录 `root` 时，证书的 principal 需包含 `root`；登录 `naos` 时需包含 `naos`。同一张证书可以包含这两个 principal。镜像中安装 CA 公钥，用户私钥和 CA 私钥由各自持有人保管，详见[密钥与证书](keys.zh-CN.md)。

量产版关闭 SSH 密码认证和串口交互，保留启动日志。系统保留 `naos` 用户，未配置免密 sudo；管理操作使用具备 `root` principal 的证书登录。

以下设备管理命令以 root 身份执行；开发版使用普通用户登录时，在命令前加 `sudo`。

## 查看系统和服务

```sh
cat /usr/lib/naos/version.json
uname -r
ip address
rc-status
```

常用服务：

| 服务 | 用途 |
| --- | --- |
| `naos-core` | 本地控制、更新调度、设备身份和看门狗 |
| `naosd` | 容器运行服务 |
| `rauc` | 系统和 Bootloader 更新安装 |
| `naos-boot-confirm` | 启动条件检查和当前槽确认 |
| `avahi-daemon` | 局域网主机和服务发现 |
| `sshd` | SSH 登录 |
| `ntpd` | 网络时间同步 |

查看状态或重启服务：

```sh
rc-service naos-core status
rc-service naos-core restart
```

系统日志和内核日志：

```sh
tail -f /var/log/messages
dmesg
```

## 配置 NAOS Core

Core 使用一份配置文件：

```text
/etc/naos-core/device.ini
```

文件包含设备连接凭据，默认权限为 `0600`，需使用 root 编辑。配置修改后重启服务生效：

```sh
vi /etc/naos-core/device.ini
rc-service naos-core restart
```

出厂配置列出了全部字段。常用配置如下：

| 配置项 | 默认值 | 用途 |
| --- | --- | --- |
| `[platform] soc` | `rk3588` | 选择 OEM 信息读取适配 |
| `[http] enabled` | `true` | 启用本机 TCP HTTP 入口 |
| `[http] listen` | `127.0.0.1:8890` | HTTP 监听地址 |
| `[core] socket` | `/run/naos/core.sock` | 本机 CLI 使用的控制 socket |
| `[container] enabled` | `false` | 由 Core 提交业务 manifest |
| `[container] manifest` | `/userdata/manifest.yml` | 业务配置文件路径 |
| `[boot] check_enabled` | `false` | 启用自定义启动检查 |
| `[boot] check_program` | 空 | 启动检查程序或脚本路径 |
| `[hawkbit] enabled` | `false` | 启用云端更新，配置见[升级与恢复](updates.zh-CN.md#通过-hawkbit-更新) |
| `[watchdog] enabled` | `true` | 由 Core 接管并维护硬件看门狗 |
| `[watchdog] timeout` | `30s` | 请求的硬件超时时间 |
| `[watchdog] feed_interval` | `10s` | Core 喂狗周期 |

### 本机控制入口

查看命令帮助：

```sh
naos-core --help
naos-core containers --help
naos-core hawkbit --help
```

常用操作：

```sh
naos-core oem
naos-core containers
naos-core containers start app1
naos-core containers stop app1
naos-core containers restart app1
naos-core reboot
```

HTTP 默认仅监听本机回环地址。将下面配置写入现有 INI 的 `[http]` 节，可单独关闭 TCP HTTP 入口，本机 CLI 继续通过 socket 使用：

```ini
[http]
enabled=false
```

`listen` 仅接受数字回环地址，例如 `127.0.0.1:8890` 或 `[::1]:8890`。

HTTP 开启时，`GET /healthz` 返回 `{"status":"ok"}`。设备业务可使用 `/v1/oem`、`/v1/containers`、`/v1/update`、`/v1/reboot` 和 `/v1/recovery` 等入口完成相应操作。更新接口的行为见[升级与恢复](updates.zh-CN.md)。

### 看门狗

```ini
[watchdog]
enabled=true
device=/dev/watchdog0
timeout=30s
feed_interval=10s
```

看门狗功能需要板型提供相应的硬件和驱动支持。启用 Core 的看门狗管理后，Core 通过自身健康检查并按配置周期喂狗；硬件实际超时以驱动返回值为准。启动阶段是否由 U-Boot 开启看门狗，以及内核是否在用户态接管前持续喂狗，取决于对应板型的 BSP 配置。

`enabled=false` 仅关闭 Core 的看门狗管理。若对应板型已在启动阶段启用看门狗，其后续行为由 Bootloader 和内核配置决定。关闭 HTTP 服务不影响 Core 的看门狗健康检查。

### 按 manifest 启动业务

准备业务配置和容器根文件系统：

```text
/userdata/
├── manifest.yml
└── rootfs/
```

在 Core 配置中启用：

```ini
[container]
enabled=true
address=/var/run/naosd.sock
manifest=/userdata/manifest.yml
```

Core 完成设备身份和主机名初始化后，向 naosd 提交该 manifest。manifest 使用 NAOS Container 支持的格式；容器 ID 由 manifest 中的 `meta.id` 指定。

该开关只控制 Core 主动提交 manifest 的行为。naosd 原有容器自启动策略保持独立；将开关关闭后，已经运行的容器会继续运行，可使用 `naos-core containers stop <容器ID>` 停止。

### 自定义启动检查

默认采用系统基础启动检查。需要等待业务就绪后再确认系统槽时，设置：

```ini
[boot]
check_enabled=true
check_program=/userdata/boot-check.sh
```

检查程序应为可执行文件，脚本需有 shebang，例如 `#!/bin/sh`。程序在 60 秒内返回 `0` 表示检查通过；非零退出、超时或无法执行均阻止本次启动确认。

```sh
chmod +x /userdata/boot-check.sh
naos-core boot-check
```

`boot-check` 可单独检查返回结果，不修改 RAUC 槽状态。开机时由 `naos-boot-confirm` 完成系统检查并确认当前槽。业务检查脚本可在允许的时间内等待容器就绪。

### 主机名和局域网发现

RK3588 配置下，Core 启动时读取 OEM SN。有效 SN 用作主机名；没有可用 SN 时保留原主机名。Avahi 根据系统主机名提供局域网发现。

业务可通过本机 `PUT /v1/mdns/services/<服务ID>` 提交服务声明，字段包括 `name`、`type`、`port` 和可选 `txt`。请求须设置 `Content-Type: application/json`，请求体示例：

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

声明由 Avahi 发布。相同信息重复提交会保留现有配置；声明在业务停止后继续保留，可用 `DELETE /v1/mdns/services/<服务ID>` 撤销。

## 持久数据

userdata 首次启动时创建文件系统，后续启动使用已有文件系统并保留配置和业务数据。启用加密时，系统先完成解锁，再挂载文件系统。

根目录的可写修改和 `/userdata` 数据保存在持久分区，A/B 系统槽共用这些数据。升级后，先前修改过的配置可能继续覆盖新系统自带的默认配置。恢复出厂设置的清理范围见[升级与恢复](updates.zh-CN.md#恢复出厂设置)。

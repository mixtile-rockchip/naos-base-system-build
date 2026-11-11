# 升级与恢复

[文档首页](README.md) · [项目首页](../README.md) · [登录与运行配置](runtime.md)

## 选择升级包

NAOS 使用签名 RAUC 包进行升级，开发版和量产版均保留升级包验签。

| 升级包 | 更新内容 | 系统槽切换 |
| --- | --- | --- |
| `naos-bundle-<板型>-<版本>.raucb` | rootfs，以及包含内核、设备树和 ramdisk 的 boot 镜像 | 写入备用系统槽，随后尝试启动新槽 |
| `naos-bootloader-bundle-<板型>-<版本>.raucb` | U-Boot 和 IDBlock | 保持当前系统槽 |

选择与设备板型、分区布局和开发/量产配置匹配的发布包。设备的 OTA 验签证书、安全启动密钥和系统加密密钥应与该设备的发布链匹配，密钥说明见[密钥与证书](keys.md)。

系统包和 Bootloader 包可以分别发布、分别安装。生成升级包的构建开关及输出路径见[获取与构建](build.md)。

## 本地文件升级

先将升级包复制到设备。开发版可先上传到 `naos` 用户主目录，例如：

```sh
scp naos-bundle-mixtile-core3588e-v2-1.0.0.raucb naos@192.0.2.10:~/update.raucb
```

文件名、版本号和 `192.0.2.10` 均为示例，请换成实际值。开发版使用 `naos` 用户登录后，以 sudo 执行管理命令；量产版使用相应 SSH 用户证书，可通过 root 账号直接上传到 `/userdata`。登录方式见[运行配置](runtime.md#登录设备)。

使用上述开发版上传方式时，在设备上以 root 将文件移到 `/userdata`，再发起更新：

```sh
mv /home/naos/update.raucb /userdata/update.raucb
naos-core update /userdata/update.raucb
```

命令返回任务 ID，安装在后台继续。查询任务：

```sh
naos-core tasks
naos-core task <任务ID>
```

查看当前更新状态：

```sh
naos-core update
```

也可以给更新请求指定 ID，便于调用方识别同一次操作：

```sh
naos-core update --request-id release-1.0.0 /userdata/update.raucb
```

使用 Bootloader 升级包时，命令相同，将文件路径换成对应的 `.raucb` 即可。

安装成功后，Core 自动请求重启。系统包重启后由 `naos-boot-confirm` 检查并确认所启动的系统槽；任务显示 `boot_confirmed` 表示重启后的确认已完成。安装完成与新系统启动成功是两个阶段，调用方应继续查询任务结果。

## 本机 HTTP 升级

本机业务可以向 `POST /v1/update` 提交设备上已有升级包的绝对路径。请求须设置 `Content-Type: application/json`，请求体示例：

```json
{
  "path": "/userdata/update.raucb",
  "request_id": "release-1.0.0"
}
```

接口接受请求后返回 HTTP `202` 和任务 ID，安装继续在后台进行。通过 `GET /v1/update-tasks/<任务ID>` 查询结果；`GET /v1/update-tasks` 查看任务列表。

此入口使用本地文件路径。调用前，业务先完成文件下载或传输。HTTP 默认监听 `127.0.0.1:8890`，开启方式见[运行配置](runtime.md#本机控制入口)。

## 通过 hawkBit 更新

在设备 `/etc/naos-core/device.ini` 中填写连接信息：

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

请替换服务地址、租户和 GatewayToken，并在服务端启用 GatewayToken。`server_url` 为服务地址，可包含端口，租户和 API 路径由客户端处理。

同一份镜像用于多台设备时，保持 `controller_id` 为空。设备首次启用时会生成并保存自己的 ID：有有效 SN 时使用 SN 派生的 ID，否则使用随机 ID。设备专用的 TargetToken 可配合 `token_type=target` 使用。

修改配置后，重启 Core 并检查连接：

```sh
rc-service naos-core restart
naos-core hawkbit check
naos-core hawkbit status
```

Core 维护平台联系，并按配置调度 updater。`poll_interval` 控制 updater 的执行间隔；平台保活请求按服务端返回的轮询周期运行。

也可以显式提供服务器、租户和 GatewayToken，手动注册设备：

```sh
naos-core hawkbit register \
  --server https://updates.example.com \
  --tenant DEFAULT \
  --token REPLACE_WITH_GATEWAY_TOKEN
```

注册成功后，连接信息和设备 ID 保存到同一份 `device.ini`。按需将其中的 `enabled` 设为 `true`，再重启 Core 启用云端更新。

向 hawkBit 上传与设备匹配的系统或 Bootloader RAUC 包后，由平台分配更新任务。设备使用同一套签名验证和安装流程，Bootloader 包保持当前系统槽。

## 查看 A/B 状态

```sh
rauc status
```

系统使用 A/B 两组 boot 和 system 分区，每个 boot 槽包含内核、设备树及 ramdisk，每个 system 槽包含对应的系统镜像。U-Boot 使用 RAUC 选槽状态和试启动计数选择启动槽；成功启动后由系统确认当前槽。

默认每个 boot 槽为 50 MiB，每个 system 槽为 200 MiB。容量可在生成完整镜像前调整，见[构建说明](build.md)。**OTA 不调整设备已有分区的容量**；发布包中的载荷必须能放入目标分区。

A/B 共用持久数据分区。系统升级和槽位切换保留 userdata、系统配置的可写层、容器数据及设备密钥材料。若新版本需要改变已有数据格式，应由相应业务处理兼容性。

## 恢复出厂设置

恢复前将需要保留的文件复制到设备之外。恢复会清空整个 userdata 中的数据，包括：

- 用户文件和业务数据。
- 根文件系统的可写配置，包括新增的软件和本机修改。
- 容器数据、Core 状态和设备上保存的更新任务。
- 设备运行时生成的 SSH 主机密钥和自行配置的接入信息。

以 root 执行：

```sh
naos-core recovery
```

本机业务也可通过 `POST /v1/recovery` 请求恢复。

Core 写入恢复请求并重启。下次启动时，系统挂载 userdata 后、挂载根 overlay 前删除其中的数据，再继续正常启动。分区和文件系统保留，固件、U-Boot 选槽环境及 misc 中的密钥材料保留；加密数据卷沿用原解锁材料。

恢复后，系统采用镜像自带的默认配置。镜像中预置的平台配置仍在；只保存在设备可写层中的配置会被清除。设备可能重新获取 IP 地址，SSH 主机密钥也会重新生成。重新连接时核对设备身份和新的主机指纹。

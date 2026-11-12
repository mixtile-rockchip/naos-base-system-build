# naos-yocto

[English](README.md) | **简体中文**

NAOS 是面向嵌入式设备的通用 Linux 系统，提供容器运行、A/B 系统升级、恢复出厂设置和设备管理功能。

本仓库使用 **Yocto Wrynose 6.0 LTS** 构建系统组件，并从 Alpine APK 组装用户空间。不同硬件平台通过对应的 BSP 层适配，平台相关的启动、驱动和安全功能随板型提供。

## 快速开始

### 无需编译，快速体验

使用 [NAOS 镜像生成器（naos-genimage）](https://github.com/mixtile-rockchip/naos-genimage)，选择支持的板型和已发布版本，即可生成固件并烧录体验，无需编译系统源码。具体使用方法见镜像生成器的 README。

### 从源码构建

需要修改 BSP 或自行构建系统时，使用本仓库。

先完成[构建环境准备](doc/build.zh-CN.md#准备主机)，使用普通用户获取工程：

```bash
git clone https://github.com/mixtile-rockchip/naos-yocto.git
cd naos-yocto
```

从下方[支持的硬件](#支持的硬件)中选择板型，并按[密钥与证书说明](doc/keys.zh-CN.md)准备构建材料。以下以当前支持的 Mixtile Core3588E 为例：

```bash
export MACHINE=mixtile-core3588e-v2
kas build kas/development.yml
```

构建产物位于对应板型目录，具体文件见[构建说明](doc/build.zh-CN.md#获取构建结果)：

```text
build/tmp/deploy/images/<MACHINE>/
```

需要生成升级包时，准备 OTA 签名材料并启用 `NAOS_OTA`：

```bash
NAOS_OTA=1 kas build kas/development.yml
```

构建量产版前，按所选板型准备安全功能所需的材料及 SSH 登录证书：

```bash
kas build kas/production.yml
```

详细命令、功能开关、缓存目录与输出文件见[构建说明](doc/build.zh-CN.md)。

## 开发版与量产版

| 功能 | 开发版 | 量产版 |
| --- | --- | --- |
| 安全启动 | 默认关闭 | 默认开启，需板型支持 |
| 磁盘加密 | 默认关闭 | 默认开启，需板型支持 |
| RAUC 升级包验签 | 开启 | 开启 |
| SSH 登录 | `naos` 用户，默认密码 `naos` | 仅可信 SSH 用户 CA 签发的证书，可登录 `root` 或 `naos` |
| 串口 | 提供开发调试入口 | 保留启动日志，关闭输入和登录 |
| SBOM | 默认关闭 | 默认开启 |
| OTA 包生成 | 默认关闭，可选开启 | 默认关闭，可选开启 |

安全启动、磁盘加密、SBOM 和 OTA 可以通过环境变量选择。硬件相关功能的支持范围和配置以对应板型为准，详见[构建说明](doc/build.zh-CN.md)和[密钥与证书](doc/keys.zh-CN.md)。

## 系统功能

- Alpine 系统与 OpenRC 服务管理，预装 SSH、Avahi、时间同步和基础运维工具。
- RAUC 提供 A/B 系统升级，支持本地和云端更新；Bootloader 更新能力由板型适配。
- `naos-core` 提供本地命令、Unix socket 和可选 HTTP 接口，管理更新、恢复出厂、OEM 信息、mDNS 和看门狗。
- 集成 NAOS Container，支持通过 manifest 配置部署容器业务。
- userdata 首次启动时创建文件系统，后续启动保留配置和业务数据。
- 量产版可结合板型能力启用安全启动和磁盘加密。
- 按需生成整机及组件 SBOM，关联实际交付的固件、APK 和内核模块。

## 获取镜像与定制镜像

需要修改 BSP 或重新构建系统时，使用本仓库。

基于已发布产物定制设备树、预装应用 APK 或替换系统配置时，可使用独立的[镜像生成器](https://github.com/mixtile-rockchip/naos-genimage)。支持的板型及镜像类型以镜像生成器和已发布产物为准。

## 支持的硬件

- **Mixtile Core3588E 核心板**：`MACHINE=mixtile-core3588e-v2`

## 用户文档

- [文档目录](doc/README.zh-CN.md)
- [构建环境、功能开关与产物](doc/build.zh-CN.md)
- [密钥与证书](doc/keys.zh-CN.md)
- [登录、系统配置与日常使用](doc/runtime.zh-CN.md)
- [升级、槽位与恢复出厂设置](doc/updates.zh-CN.md)

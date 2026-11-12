# 构建 NAOS

[English](build.md) | **简体中文**

[返回项目首页](../README.zh-CN.md) · [文档目录](README.zh-CN.md)

## 准备主机

推荐使用 Ubuntu 24.04 x86_64、Python 3.11 或更新版本，并安装支持配置格式 14 的 kas。使用普通用户运行 kas 和 BitBake。

可通过 Ubuntu 软件源安装基础依赖：

```bash
sudo apt-get update
sudo apt-get install build-essential chrpath cpio debianutils diffstat file gawk \
  git iputils-ping kas libacl1 libcrypt-dev locales python3 python3-git \
  python3-jinja2 python3-pexpect python3-pip python3-subunit socat texinfo \
  unzip util-linux wget xz-utils zstd qemu-user-static binfmt-support
sudo locale-gen en_US.UTF-8
export LANG=en_US.UTF-8
```

主机还须允许非特权用户创建命名空间，并能通过 QEMU/binfmt 执行目标架构的程序：

- Alpine 系统组装使用用户、挂载和 IPC 命名空间；普通主机还使用 PID 命名空间。
- QEMU/binfmt 注册项须匹配目标架构、处于启用状态并包含 `F` 标志。当前 Mixtile Core3588E 构建使用 ARM64；仅安装 QEMU 文件不足以确认支持可用。
- 在容器中构建时也需要满足这些条件，构建用户须能读写工作目录和缓存目录。

可以先检查命名空间：

```bash
unshare --user --map-root-user --mount --ipc --pid --fork --mount-proc true
```

如果提示权限不足，检查构建环境的用户命名空间与安全策略。不要改用 root 运行 BitBake 来绕过检查。

## 获取工程

```bash
git clone https://github.com/mixtile-rockchip/naos-yocto.git
cd naos-yocto
```

kas 自动获取构建层。BSP、安全工具使用远端 Git 的固定提交；Core 和 Container 使用固定版本与摘要的发布归档。构建不要求准备本地厂商 SDK，也不需要在本仓库编译 Core 或 Container。

构建前按[密钥与证书](keys.zh-CN.md)准备当前模式所需材料。

## 选择板型与版本

当前支持的核心板及对应构建名称：

- **Mixtile Core3588E**：`mixtile-core3588e-v2`

通过 `MACHINE` 选择板型：

```bash
export MACHINE=mixtile-core3588e-v2
```

不设置时也使用该板型。后续板型须有对应 machine 配置，不能仅修改名称。

可为一次构建设置发布版本和构建标识：

```bash
export NAOS_VERSION=1.0.0
export NAOS_BUILD_ID=release-1.0.0
```

未设置时，默认版本为 `2.0.0-dev`，构建标识沿用版本。设备版本信息、升级包和发布清单会记录这些值。

## 开发版与量产版

开发版完整固件：

```bash
kas build kas/development.yml
```

量产版完整固件：

```bash
kas build kas/production.yml
```

开发版默认关闭安全启动和磁盘加密，保留 RAUC 更新包验签。量产版默认开启安全启动、system 与 userdata 加密、SBOM，并采用证书 SSH 登录和仅输出日志的串口策略。

## 生成升级包

启用 `NAOS_OTA=1` 后，完整固件之外会同时生成系统包和 Bootloader 包：

```bash
NAOS_OTA=1 kas build kas/development.yml
NAOS_OTA=1 kas build kas/production.yml
```

只有生成升级包时才需要 OTA 签名私钥。普通完整固件构建仍需 OTA 验签证书。

## 导出镜像生成器产物

```bash
NAOS_PACKAGE=1 kas build kas/development.yml
NAOS_PACKAGE=1 kas build kas/production.yml
```

此模式生成供独立镜像生成器使用的中间产物；最终镜像加密、启动签名和 OTA 打包由镜像生成器完成。它不会同时生成完整固件或最终 OTA 包。

输出目录：

```text
package/development/mixtile-core3588e-v2/
package/production/mixtile-core3588e-v2/
```

导出仍需 OTA 验签证书；量产模式还需 SSH CA 公钥。用于最终组装的启动签名私钥、系统加密密钥和 OTA 签名私钥在镜像生成器中准备。

## 功能开关

布尔开关使用 `0` 或 `1`。环境变量覆盖所选 kas 配置的默认值。

| 环境变量 | 开发版默认 | 量产版默认 | 用途 |
| --- | --- | --- | --- |
| `NAOS_OTA` | `0` | `0` | 同时生成系统、Bootloader 两种升级包 |
| `NAOS_PACKAGE` | `0` | `0` | 导出镜像生成器中间产物 |
| `NAOS_SBOM` | `0` | `1` | 生成软件物料清单 |
| `NAOS_SECURE_BOOT` | `0` | `1` | 签名并校验启动镜像 |
| `NAOS_DISK_ENCRYPTION` | `0` | `1` | 同时启用 system、userdata 加密 |
| `NAOS_SYSTEM_ENCRYPTION` | 跟随磁盘加密开关 | 跟随磁盘加密开关 | 单独选择 system 加密 |
| `NAOS_DATA_ENCRYPTION` | 跟随磁盘加密开关 | 跟随磁盘加密开关 | 单独选择 userdata 加密 |
| `NAOS_SECURE_BOOT_BURN_KEY_HASH` | `0` | `0` | 使生成的 SPL 在设备上烧写 OTP/eFuse 公钥哈希 |

安全启动、磁盘加密及 OTP/eFuse 功能需要对应板型支持。当前 Mixtile Core3588E 适配中，system 加密需要同时启用 userdata 加密；OTP/eFuse 开关仅在量产配置和安全启动均开启时有效，默认保持关闭。相关材料见[密钥与证书](keys.zh-CN.md#otpefuse-公钥哈希)。

例如，为开发版额外生成 SBOM：

```bash
NAOS_SBOM=1 kas build kas/development.yml
```

## 缓存与重复构建

默认下载缓存为工程目录下的 `downloads/`，共享构建缓存为 `sstate-cache/`。保留这两个目录可供后续构建复用。

可以独立指定其他位置，目录须由构建用户读写：

```bash
export DL_DIR="$HOME/.cache/naos/downloads"
export SSTATE_DIR="$HOME/.cache/naos/sstate-cache"
mkdir -p "$DL_DIR" "$SSTATE_DIR"
kas build kas/development.yml
```

源码和输出分别位于 `sources/`、`build/`，不需要因为再次构建而删除它们。

## 获取构建结果

普通构建的输出位于 `build/tmp/deploy/images/$MACHINE/`：

| 文件 | 用途 |
| --- | --- |
| `naos-$MACHINE.img` | 完整烧录固件 |
| `MiniLoaderAll.bin` | Rockchip USB Loader |
| `partition-layout.json`、`manifest.json` | 分区与产物信息 |
| `naos-bundle-$MACHINE-$NAOS_VERSION.raucb` | 系统、内核和 ramdisk 升级包；开启 OTA 时生成 |
| `naos-bootloader-bundle-$MACHINE-$NAOS_VERSION.raucb` | Bootloader 升级包；开启 OTA 时生成 |
| `naos-*.spdx.json`、`naos-*.sbom.json` | SBOM 及摘要；开启 SBOM 时生成 |
| `naos-*.kernel.config`、`naos-*.uboot.config` | 对应构建配置；开启 SBOM 时生成 |

系统升级使用 `.raucb` 包，完整 `.img` 用于烧录。安装方法见[升级与恢复](updates.zh-CN.md)。

## 获取已发布版本

无需自行构建时，可通过[镜像生成器](https://github.com/mixtile-rockchip/naos-genimage)选择已发布版本并生成固件。发布产物仓库为 `https://packages.naos.ai/repository`。

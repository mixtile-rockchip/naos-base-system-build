# 密钥与证书

[返回项目首页](../README.md) · [文档目录](README.md)

OTA 验签和 SSH 证书用于系统更新与登录。安全启动、磁盘加密和 OTP/eFuse 的材料及流程取决于板型；本页相关示例以当前支持的 Mixtile Core3588E 为准。

## 按构建内容准备

| 构建内容 | 必需材料 |
| --- | --- |
| 开发版完整固件 | OTA 验签证书 |
| 开发版完整固件及升级包 | OTA 签名私钥、匹配的验签证书 |
| 默认量产版完整固件 | OTA 验签证书、安全启动密钥组、system 加密密钥、SSH CA 公钥 |
| 默认量产版完整固件及升级包 | 上述量产材料，以及 OTA 签名私钥 |
| 镜像生成器中间产物 | OTA 验签证书；量产模式还需 SSH CA 公钥 |

修改安全启动、加密开关后，所需材料随实际启用的功能变化。userdata 的加密密钥由设备首次启动生成，不需要用户提供。

## 本地目录

默认按下列结构准备文件。各目录中的 README 提供生成方法。

```text
key/
├── ota/
│   ├── signing.cert.pem
│   └── signing.key.pem
├── secure-boot/
│   ├── dev.key
│   ├── dev.crt
│   └── dev.pubkey
├── system-encryption/
│   └── system.key
└── ssh-ca/
    └── ssh_ca_key.pub
```

| 材料 | 用途与生成说明 |
| --- | --- |
| `ota/signing.cert.pem` | 安装到系统中，验证系统和 Bootloader 升级包；[OTA README](../key/ota/README) |
| `ota/signing.key.pem` | 为两类升级包签名，与上面的证书匹配；仅生成升级包时需要 |
| `secure-boot/dev.key`、`dev.crt`、`dev.pubkey` | 同一组 RSA 密钥，用于 RK3588 启动镜像签名；[安全启动 README](../key/secure-boot/README) |
| `system-encryption/system.key` | system 镜像加密密钥；[系统加密 README](../key/system-encryption/README) |
| `ssh-ca/ssh_ca_key.pub` | 量产设备信任的 SSH 用户证书 CA 公钥；[SSH CA README](../key/ssh-ca/README) |

`system.key` 为 **64 个小写十六进制字符**，表示 32 字节密钥，末尾不能带换行，权限为 `0600`。它与设备自行生成的 userdata 密钥分别管理。

SSH CA 私钥和登录客户端私钥保留在签发或管理端；设备只安装 CA 公钥。证书中的 principal 需要包含实际登录用户名，例如 `root` 或 `naos`。

签名私钥和加密密钥不提交到 Git。首次获取工程后，按需生成自己的材料，或使用设备原有的材料；不要将测试密钥用于正式发布。

## 使用工程外的目录

不必把密钥复制进工程，可以通过环境变量指定绝对路径：

```bash
export NAOS_OTA_KEY_DIR="$HOME/.local/share/naos-keys/ota"
export NAOS_SECURE_BOOT_KEY_DIR="$HOME/.local/share/naos-keys/secure-boot"
export NAOS_SYSTEM_KEY_DIR="$HOME/.local/share/naos-keys/system-encryption"
export NAOS_SSH_CA_KEY="$HOME/.local/share/naos-keys/ssh-ca/ssh_ca_key.pub"
kas build kas/production.yml
```

构建会自动定位工程根目录。`NAOS_PROJECT_ROOT` 仅在需要显式指定工程位置时使用，普通构建无需设置。

## 给已有设备生成固件

- **OTA**：升级包需要由设备当前信任的证书对应私钥签名。仅替换构建端私钥，会使已有设备拒绝安装。
- **安全启动**：已经烧入 OTP/eFuse 公钥哈希的设备，必须使用与该哈希匹配的启动签名密钥。
- **system 加密**：已有设备的系统升级包需要继续使用该设备能解封的 `system.key`。
- **SSH**：量产镜像中的 CA 公钥应与管理员持有的用户证书匹配。

发布前确认这几组材料分别匹配，避免将不同用途的密钥互相替代。

## OTP/eFuse 公钥哈希

默认 `NAOS_SECURE_BOOT_BURN_KEY_HASH=0`，普通量产构建也保持关闭。

只有明确需要设备首次启动时烧写公钥哈希，才设置 `NAOS_SECURE_BOOT_BURN_KEY_HASH=1`，并同时选择量产配置和启用安全启动。该选项会改变生成的 SPL 的行为，不是在构建主机上执行烧写。

**OTP/eFuse 写入不可撤销。** 测试使用默认关闭配置。实际烧写前应确认设备状态、签名密钥和完整烧录流程。

## APK 仓库公钥

`NAOS_APK_PUBLIC_KEY` 指定设备使用的 NAOS APK 仓库验签公钥。默认使用工程自带的仓库公钥；它不是 APK 仓库签名私钥。

使用自建 APK 仓库时，需要同时配置对应地址和匹配的验签公钥。APK 仓库私钥由制包和仓库维护端保存，普通系统构建不需要它。

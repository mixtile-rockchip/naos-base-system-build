ALPINE_ARCH ?= "aarch64"
# Libraries and tools used by the source-built RAUC target. The RAUC binary
# and board-specific OTA configuration are added by the final image recipe.
ALPINE_PACKAGES ?= "alpine-base ca-certificates openssh-server util-linux sudo dbus avahi avahi-openrc u-boot-tools glib json-glib libcurl libfdisk libnl3 libcrypto3 libssl3 squashfs-tools"
ALPINE_SERVICES ?= "sshd:default dbus:default ntpd:default avahi-daemon:default"
ALPINE_DEFAULT_USER ?= "naos"
ALPINE_DEFAULT_PASSWORD ?= "naos"
ALPINE_CONSOLE_SHELL ?= "1"
ALPINE_ROOTFS_EPOCH ?= "1748563200"

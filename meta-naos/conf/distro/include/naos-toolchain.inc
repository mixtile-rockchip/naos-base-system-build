# Source-built target programs use the internal Wrynose toolchain, including
# RAUC, its Yocto-built dependencies, initramfs and the rootfs ownership helper.
# Official Alpine APKs retain their own upstream build provenance.
TCMODE = "default"
PREFERRED_TOOLCHAIN_TARGET = "gcc"
GCCVERSION = "15.3.0"
SDKGCCVERSION = "${GCCVERSION}"

# REQUIRED_VERSION fails provider selection if a future layer update removes
# the pinned version, instead of silently falling back to another compiler.
REQUIRED_VERSION_gcc = "${GCCVERSION}"
REQUIRED_VERSION_gcc-cross-${TARGET_ARCH} = "${GCCVERSION}"
REQUIRED_VERSION_gcc-runtime = "${GCCVERSION}"
REQUIRED_VERSION_gcc-sanitizers = "${GCCVERSION}"
REQUIRED_VERSION_libgcc = "${GCCVERSION}"
REQUIRED_VERSION_libgcc-initial = "${GCCVERSION}"
REQUIRED_VERSION_libgfortran = "${GCCVERSION}"
REQUIRED_VERSION_gcc-crosssdk-${SDK_SYS} = "${SDKGCCVERSION}"
REQUIRED_VERSION_gcc-cross-canadian-${TRANSLATED_TARGET_ARCH} = "${SDKGCCVERSION}"
REQUIRED_VERSION_nativesdk-gcc = "${SDKGCCVERSION}"
REQUIRED_VERSION_nativesdk-gcc-runtime = "${SDKGCCVERSION}"
REQUIRED_VERSION_nativesdk-libgcc = "${SDKGCCVERSION}"
REQUIRED_VERSION_nativesdk-libgcc-initial = "${SDKGCCVERSION}"

NAOS_BINUTILS_VERSION = "2.46.1"
REQUIRED_VERSION_binutils = "${NAOS_BINUTILS_VERSION}"
REQUIRED_VERSION_binutils-cross-${TARGET_ARCH} = "${NAOS_BINUTILS_VERSION}"
REQUIRED_VERSION_binutils-crosssdk-${SDK_SYS} = "${NAOS_BINUTILS_VERSION}"
REQUIRED_VERSION_binutils-cross-canadian-${TRANSLATED_TARGET_ARCH} = "${NAOS_BINUTILS_VERSION}"
REQUIRED_VERSION_musl = "1.2.6+git"
# musl's exact source commit and GCC/binutils source digests are supplied by
# the OE-Core commit pinned in kas/development.yml; a version string alone is not a lock.

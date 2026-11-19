SUMMARY = "NAOS factory firmware with optional separate OTA bundles"
LICENSE = "MIT"

inherit nopackages
INHIBIT_DEFAULT_DEPS = "1"

# This target only selects other recipes, like OE-Core's package-index target.
deltask do_fetch
deltask do_unpack
deltask do_patch
deltask do_configure
deltask do_compile
deltask do_install
deltask do_populate_lic
deltask do_populate_sysroot
EXCLUDE_FROM_WORLD = "1"

# Keep orchestration outside naos-image: the system bundle already depends on
# that image, so making the image depend on its bundle would create a cycle.
NAOS_RELEASE_IMAGE ?= "naos-image"
NAOS_RELEASE_OTA_TASKS ?= "naos-bundle:do_build naos-bootloader-bundle:do_build"

do_build[depends] += "${NAOS_RELEASE_IMAGE}:do_build"
do_build[depends] += "${@d.getVar('NAOS_RELEASE_OTA_TASKS') if d.getVar('NAOS_OTA') == '1' else ''}"
do_build[depends] += "${@'naos-sbom:do_build' if d.getVar('NAOS_SBOM') == '1' else ''}"

# Include recipes reached only through deployed artifacts, such as U-Boot.
do_build[recrdeptask] += "${@'do_create_recipe_spdx do_create_spdx do_create_package_spdx' if d.getVar('NAOS_SBOM') == '1' else ''}"

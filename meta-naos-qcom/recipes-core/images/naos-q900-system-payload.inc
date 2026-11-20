NAOS_Q900_SYSTEM_IMAGE_TYPE = "${@bb.utils.contains('NAOS_SYSTEM_ENCRYPTION', '1', 'naoscrypt', 'squashfs', d)}"

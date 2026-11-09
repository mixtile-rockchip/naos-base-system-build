/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef __NAOS_SECURE_ENV_H
#define __NAOS_SECURE_ENV_H

/* Only these values may replace built-in defaults when importing MMC env. */
#define CFG_ENV_FLAGS_LIST_STATIC \
    "BOOT_ORDER:sw,BOOT_A_LEFT:dw,BOOT_B_LEFT:dw"

#endif

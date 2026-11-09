// SPDX-License-Identifier: GPL-2.0+
/* Select the same MMC device that the redundant environment backend uses. */
#include <command.h>
#include <env.h>
#include <mmc.h>

static int do_naos_bootdev(struct cmd_tbl *cmdtp, int flag, int argc,
                          char *const argv[])
{
    int devnum = mmc_get_env_dev();

    if (devnum < 0)
        return CMD_RET_FAILURE;

    /* Vendor bootm also consumes devtype/devnum on its standard FIT path. */
    if (env_set("devtype", "mmc") ||
        env_set_ulong("devnum", devnum) ||
        env_set_ulong("naos_mmcdev", devnum))
        return CMD_RET_FAILURE;

    return CMD_RET_SUCCESS;
}

U_BOOT_CMD(naos_bootdev, 1, 0, do_naos_bootdev,
           "select the MMC device used for NAOS boot and environment", "");

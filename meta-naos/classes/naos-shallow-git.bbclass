# SPDX-License-Identifier: MIT
# Limit BSP downloads to the pinned commit. BitBake's fast shallow fetch is
# retained; its full-history fallback is rejected by the Git wrapper.
BB_GIT_SHALLOW = "1"
BB_GIT_SHALLOW_DEPTH = "1"
BB_GENERATE_SHALLOW_TARBALLS = "1"
BB_GIT_SHALLOW_EXTRA_REFS = ""
BB_GIT_SHALLOW_REVS = ""

# Names cover the pinned BSP sources and the independent security project.
BB_GIT_SHALLOW_DEPTH_default = "1"
BB_GIT_SHALLOW_DEPTH_uboot = "1"
BB_GIT_SHALLOW_DEPTH_rkbin = "1"
BB_GIT_SHALLOW_DEPTH_tee = "1"
BB_GIT_SHALLOW_DEPTH_tools = "1"
BB_GIT_SHALLOW_REVS_default = ""
BB_GIT_SHALLOW_REVS_uboot = ""
BB_GIT_SHALLOW_REVS_rkbin = ""
BB_GIT_SHALLOW_REVS_tee = ""
BB_GIT_SHALLOW_REVS_tools = ""

FETCHCMD_git = "'${NAOS_LAYER_DIR}/libexec/naos-shallow-git' -c gc.autoDetach=false -c core.pager=cat -c safe.bareRepository=all -c clone.defaultRemoteName=origin"
do_fetch[file-checksums] += "${NAOS_LAYER_DIR}/libexec/naos-shallow-git:True"

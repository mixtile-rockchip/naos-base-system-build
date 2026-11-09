# Public development/test credentials are intentionally shipped in this project.
# Products must replace this public test pair and their trusted certificate before release.
NAOS_OTA_KEY_DIR ?= "${NAOS_PROJECT_ROOT}/key/ota"
NAOS_OTA_KEYRING ?= "${NAOS_OTA_KEY_DIR}/signing.cert.pem"

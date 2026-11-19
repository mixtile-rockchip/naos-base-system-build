FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI += "file://q900-ufs-partitions.conf"

do_compile:prepend() {
    install -D -m 0644 "${UNPACKDIR}/q900-ufs-partitions.conf" \
        "${S}/platforms/radxa-airbox-q900/ufs/partitions.conf"
}

do_compile:append() {
    q900_layout="${S}/platforms/radxa-airbox-q900/ufs"
    lun0_dir="${B}/q900-lun0"
    install -d "$lun0_dir"

    # Keep the vendor growth behavior for the other LUNs. LUN 0 userdata
    # starts at its declared size and grows from the NAOS initramfs.
    sed -n -e '/^--disk /s/ --grow-last-partition//p' \
        -e '/^--partition --lun=0 /p' "$q900_layout/partitions.conf" > "$lun0_dir/partitions.conf"
    qcom-ptool gen_partition -i "$lun0_dir/partitions.conf" -o "$lun0_dir/partitions.xml"
    (cd "$lun0_dir" && qcom-ptool ptool -x partitions.xml -p 0)
    install -m 0644 "$lun0_dir"/gpt_*0.bin "$lun0_dir/rawprogram0.xml" \
        "$lun0_dir/patch0.xml" "$q900_layout/"
}

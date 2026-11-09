#!/usr/bin/env python3
"""Build the OS FIT with Rockchip's native signer and verify against U-Boot."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


KEY_NODE = "/signature/key-dev"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def execute(command, cwd, env=None):
    result = subprocess.run([str(part) for part in command], cwd=cwd, env=env,
                            capture_output=True, timeout=120)
    require(result.returncode == 0, "native FIT signing or verification failed")
    return result.stdout.decode().strip()


def executable(path):
    selected = shutil.which(str(path))
    require(selected is not None, "required host tool is unavailable")
    return Path(selected).resolve(strict=True)


def public_key(dtb, fdtget, cwd):
    def cells(name):
        return tuple(int(word, 16) for word in execute([fdtget, "-t", "x", dtb, KEY_NODE, name], cwd).split())
    bits, exponent, modulus = cells("rsa,num-bits"), cells("rsa,exponent"), cells("rsa,modulus")
    require(bits == (2048,) and sum(word << (32 * index) for index, word in enumerate(reversed(exponent))) == 65537,
            "U-Boot must contain an RSA-2048 key with exponent 65537")
    require(len(modulus) == 64 and modulus[0] & 0x80000000, "invalid U-Boot RSA modulus")
    require(execute([fdtget, dtb, KEY_NODE, "required"], cwd) == "conf", "U-Boot must require configuration signatures")
    return bits, exponent, modulus


def verify_resource(fit, expected, fdtget, cwd):
    # The vendor host checker only loads kernel/FDT/ramdisk; check multi here.
    node = "/images/resource"
    def cells(path, name):
        return execute([fdtget, "-t", "x", fit, path, name], cwd).split()
    position = int("".join(word.zfill(8) for word in cells(node, "data-position")), 16)
    size = int("".join(word.zfill(8) for word in cells(node, "data-size")), 16)
    require(size == expected["size"] and position + size <= fit.stat().st_size, "resource payload is out of bounds")
    with fit.open("rb") as stream:
        stream.seek(position)
        actual = hashlib.sha256(stream.read(size)).hexdigest()
    require(actual == expected["sha256"] == "".join(word.zfill(8) for word in cells(node + "/hash", "value")),
            "resource payload hash differs")
    signed = execute([fdtget, fit, "/configurations/conf/signature", "hashed-nodes"], cwd).split()
    require(node in signed and node + "/hash" in signed, "configuration signature must cover the resource hash")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input-dir", "output-dir", "layout", "fit-key-dir", "fit-check-sign", "fit-key-dtb", "mkimage"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("kernel", "dtb", "initramfs"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--resource")
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--fdtget", default="fdtget")
    args = parser.parse_args()
    for name in ("input_dir", "layout", "fit_key_dir", "fit_key_dtb"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    for name in ("mkimage", "fit_check_sign", "fdtget"):
        setattr(args, name, executable(getattr(args, name)))
    require(args.epoch >= 0, "epoch must be nonnegative")
    require(all((args.fit_key_dir / name).is_file() for name in ("dev.key", "dev.crt")), "dev.key and dev.crt are required")
    loads = json.loads(args.layout.read_text())["fit"]
    nodes, payloads = [], {}
    specs = [("kernel", args.kernel, "kernel_load", "kernel"),
             ("fdt", args.dtb, "fdt_load", "flat_dt"),
             ("ramdisk", args.initramfs, "ramdisk_load", "ramdisk")]
    if args.resource:
        specs.append(("resource", args.resource, None, "multi"))
    for role, name, setting, kind in specs:
        source = (args.input_dir / name).resolve(strict=True)
        require(source.is_file() and source.stat().st_size > 0, "FIT inputs must be nonempty files")
        require(not any(character in str(source) for character in ('"', "\\", "\n", "\r")), "unsafe FIT input path")
        extra = ""
        payloads[role] = {"size": source.stat().st_size, "sha256": digest(source)}
        if setting:
            value = loads[setting]
            load = int(value, 0) if isinstance(value, str) else value
            require(isinstance(load, int) and 0 < load <= 0xFFFFFFFF, "invalid FIT load address")
            payloads[role]["load"] = load
            extra = 'load = <0x%x>;' % load
            if role != "fdt":
                extra += ' os = "linux";'
            if role == "kernel":
                extra += " entry = <0x%x>;" % load
        nodes.append('%s { data = /incbin/("%s"); type = "%s"; arch = "arm64"; compression = "none"; %s hash { algo = "sha256"; }; };' % (role, source, kind, extra))
    its_text = '''/dts-v1/; / {
    description = "NAOS signed OS FIT"; #address-cells = <1>;
    images { %s };
    configurations { default = "conf"; conf {
        kernel = "kernel"; fdt = "fdt"; ramdisk = "ramdisk"; %s
        signature { algo = "sha256,rsa2048"; padding = "pss";
            key-name-hint = "dev"; sign-images = "kernel", "fdt", "ramdisk"%s; };
    }; };
};
''' % ("\n".join(nodes), 'multi = "resource";' if args.resource else "", ', "multi"' if args.resource else "")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(args.epoch)}
    with tempfile.TemporaryDirectory(prefix=".naos-fit-", dir=output.parent) as directory:
        work = Path(directory)
        its, fit, key = work / "boot.its", work / "boot.img", work / "verification-key.dtb"
        original = digest(args.fit_key_dtb)
        before = public_key(args.fit_key_dtb, args.fdtget, work)
        shutil.copyfile(args.fit_key_dtb, key)
        its.write_text(its_text)
        # Native -K fills software RSA parameters in a public verification copy.
        execute([args.mkimage, "-k", args.fit_key_dir, "-K", key, "-r", "-E", "-p", "0x3000", "-f", its, fit], work, env)
        require(public_key(key, args.fdtget, work) == before, "signing key differs from the actual U-Boot key")
        execute([args.fit_check_sign, "-f", fit, "-k", key, "-c", "conf"], work)
        if args.resource:
            verify_resource(fit, payloads["resource"], args.fdtget, work)
        require(digest(args.fit_key_dtb) == original, "original U-Boot control DTB changed")
        modulus = b"".join(word.to_bytes(4, "big") for word in before[2])
        result = {"schema_version": 1, "fit_sha256": digest(fit), "fit_bytes": fit.stat().st_size,
                  "algorithm": "sha256,rsa2048", "padding": "pss", "key_name": "dev",
                  "public_key": {"control_dtb_sha256": original, "rsa_modulus_sha256": hashlib.sha256(modulus).hexdigest()},
                  "payloads": payloads, "original_signature_valid": True, "hardware_tested": False}
        fit.replace(output / "boot.img")
        its.replace(output / "boot.its")
        (output / "boot-verification.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("NAOS OS FIT signed and verified with native Rockchip tools")


if __name__ == "__main__":
    main()

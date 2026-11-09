import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "build-apk.py"
spec = importlib.util.spec_from_file_location("build_apk", SCRIPT)
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


class BuildApkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / "export"
        self.inputs.mkdir()
        contents = {"Image": b"kernel bytes\x00", "rootfs.tar.gz": b"opaque\x1f\x8brootfs",
                    "bootloader/bl31.elf": b"\x7fELF opaque target data"}
        for name, data in contents.items():
            target = self.inputs / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.document = {"schema_version": 1, "machine": "mixtile-core3588e-v2",
                         "profile": "development", "architecture": "aarch64",
                         "version": "2.0.0-dev.20261106.2", "build_id": "build-123",
                         "source_date_epoch": 1748563200,
                         "otp": {"burn_public_key_hash": False},
                         "input_files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                         for name, data in contents.items()}}
        self.write_manifest()

    def write_manifest(self):
        (self.inputs / "export.json").write_text(json.dumps(self.document, indent=2) + "\n")

    def test_unlisted_file_is_rejected_before_it_can_enter_the_apk(self):
        (self.inputs / "private.key").write_text("not a real key")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            packaging.inspect_export(self.inputs)

    def test_directory_symlink_is_rejected(self):
        (self.inputs / "outside").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "non-ordinary"):
            packaging.inspect_export(self.inputs)

    def test_corrupted_recorded_input_is_rejected(self):
        (self.inputs / "Image").write_bytes(b"changed kernel")
        with self.assertRaisesRegex(ValueError, "differs from manifest"):
            packaging.inspect_export(self.inputs)

    def test_machine_cannot_inject_apkbuild_shell_commands(self):
        self.document["machine"] = "board; echo bad"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "machine"):
            packaging.inspect_export(self.inputs)

    def test_source_tar_is_reproducible_and_keeps_inner_archive_bytes(self):
        data = packaging.inspect_export(self.inputs)
        first, second = self.root / "first.tar", self.root / "second.tar"
        packaging.write_inputs_tar(self.inputs, data, first)
        for path in self.inputs.rglob("*"):
            os.utime(path, (123, 456))
        packaging.write_inputs_tar(self.inputs, data, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual({item.name for item in archive},
                             {"inputs/" + name for name in data["input_files"]} | {"inputs/export.json"})
            self.assertEqual(archive.extractfile("inputs/rootfs.tar.gz").read(),
                             (self.inputs / "rootfs.tar.gz").read_bytes())
            self.assertTrue(all(item.uid == 0 and item.gid == 0 and item.mtime == data["source_date_epoch"]
                                for item in archive))

    def test_input_change_after_validation_is_detected_while_streaming_tar(self):
        data = packaging.inspect_export(self.inputs)
        (self.inputs / "Image").write_bytes(b"not kernel bytes")
        with self.assertRaisesRegex(ValueError, "changed during packaging"):
            packaging.write_inputs_tar(self.inputs, data, self.root / "changed.tar")

    def test_checked_in_apkbuilds_preserve_payload_and_require_source_checksum(self):
        for profile in ("development", "production"):
            with self.subTest(profile=profile):
                text = packaging.apkbuild_path(profile).read_text()
                self.assertIn('arch="all"', text)
                self.assertIn('url="https://www.mixtile.com"', text)
                self.assertIn('options="!strip !tracedeps !archcheck !check"', text)
                self.assertIn("/usr/share/naos/image-inputs/" + profile + "/", text)
                self.assertIn('cp -a "$srcdir/inputs/."', text)
                self.assertIn('NAOS_APK_SOURCE_SHA512:?', text)
                self.assertNotIn("SKIP", text)
                suffix = "-dev" if profile == "development" else "-prod"
                self.assertIn('pkgname="${NAOS_APK_MACHINE}-alpine' + suffix + '"', text)
                self.assertEqual(packaging.apkbuild_path(profile).parent.name, "rk3588")

    def test_checked_in_apkbuilds_copy_verified_inputs_to_the_selected_profile(self):
        source = self.root / "src"
        shutil.copytree(self.inputs, source / "inputs")
        for profile in ("development", "production"):
            with self.subTest(profile=profile):
                package = self.root / ("pkg-" + profile)
                env = os.environ.copy()
                env.update({"NAOS_APK_MACHINE": self.document["machine"],
                            "NAOS_APK_VERSION": "2.0.0_p123", "NAOS_APK_SOURCE_SHA512": "a" * 128,
                            "srcdir": str(source), "pkgdir": str(package)})
                result = subprocess.run(["sh", "-c", '. "$1"; package; printf "%s\\n" "$pkgname" "$pkgver" "$sha512sums"',
                                         "sh", str(packaging.apkbuild_path(profile))], env=env,
                                        check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout.splitlines(),
                                 [self.document["machine"] + "-alpine-" + ("dev" if profile == "development" else "prod"),
                                  "2.0.0_p123", "a" * 128 + "  inputs.tar"])
                installed = package / "usr/share/naos/image-inputs" / profile / self.document["machine"]
                self.assertEqual(packaging.export_helper().verify(installed), self.document)

    def test_build_stages_private_keys_scopes_config_and_publishes_only_apk(self):
        private, public = self.root / "private", self.root / "public"
        private.write_text("fake private material")
        public.write_text("fake public material")
        output = self.root / "apk-output"
        staged_paths = []

        def command(args, **kwargs):
            if args[:2] == ["apk", "version"]:
                return subprocess.CompletedProcess(args, 0)
            if args[0] == "openssl":
                return subprocess.CompletedProcess(args, 0, stdout=b"same public DER")
            if args[0] == "abuild":
                env = kwargs["env"]
                key = Path(env["PACKAGER_PRIVKEY"])
                staged_paths.append(key)
                self.assertEqual(key.name, "naos-apk.rsa")
                self.assertEqual(key.stat().st_mode & 0o777, 0o600)
                self.assertEqual(key.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(Path(env["PACKAGER_PUBKEY"]).name, "naos-apk.rsa.pub")
                self.assertEqual(env["ABUILD_CONF"], "/dev/null")
                self.assertEqual(env["SOURCE_DATE_EPOCH"], "1748563200")
                self.assertEqual(env["NAOS_APK_MACHINE"], self.document["machine"])
                self.assertEqual(env["NAOS_APK_VERSION"], "2.0.0_p123")
                self.assertRegex(env["NAOS_APK_SOURCE_SHA512"], r"^[0-9a-f]{128}$")
                self.assertEqual((Path(kwargs["cwd"]) / "APKBUILD").read_bytes(),
                                 packaging.apkbuild_path("development").read_bytes())
                self.assertEqual(args[1:2], ["-F"])
                repo = Path(env["REPODEST"]) / "naos/x86_64"
                repo.mkdir(parents=True)
                name = "mixtile-core3588e-v2-alpine-dev-2.0.0_p123-r0.apk"
                signature = io.BytesIO()
                with tarfile.open(fileobj=signature, mode="w") as archive:
                    member = tarfile.TarInfo(".SIGN.RSA.naos-apk.rsa.pub")
                    member.size = len(b"fake signature")
                    archive.addfile(member, io.BytesIO(b"fake signature"))
                control = io.BytesIO()
                with tarfile.open(fileobj=control, mode="w") as archive:
                    content = ("pkgname = mixtile-core3588e-v2-alpine-dev\n"
                               "pkgver = 2.0.0_p123-r0\narch = x86_64\n").encode()
                    member = tarfile.TarInfo(".PKGINFO")
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
                (repo / name).write_bytes(gzip.compress(signature.getvalue(), mtime=0)
                                         + gzip.compress(control.getvalue(), mtime=0))
                return subprocess.CompletedProcess(args, 0)
            if args[:2] == ["apk", "--keys-dir"]:
                self.assertEqual(args[3], "verify")
                return subprocess.CompletedProcess(args, 0)
            self.fail("Unexpected external command: " + repr(args))

        with mock.patch.object(packaging.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(packaging.subprocess, "run", side_effect=command), \
                mock.patch.object(packaging.os, "geteuid", return_value=0):
            report = packaging.build(self.inputs, output, private, public, "2.0.0_p123")
        self.assertEqual(report["architecture"], "x86_64")
        self.assertEqual(report["target_arch"], "aarch64")
        self.assertEqual(report["version"], "2.0.0_p123-r0")
        self.assertEqual(report["export_version"], self.document["version"])
        self.assertEqual(report["sha256"], packaging.digest(output / report["filename"]))
        self.assertEqual({path.name for path in output.iterdir()}, {report["filename"], "package.json"})
        self.assertTrue(all(not path.exists() for path in staged_paths))

    def test_invalid_version_does_not_reach_external_tools(self):
        with mock.patch.object(packaging.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "pkgver"):
                packaging.build(self.inputs, self.root / "out", self.root / "key", self.root / "pub", "2.0.0-dev")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

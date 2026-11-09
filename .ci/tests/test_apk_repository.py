import base64
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


CI = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, CI / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load("repository_server", "apk-repository-server.py")
client = load("repository_client", "publish-apk.py")
MACHINE = "mixtile-core3588e-v2"
NAME = MACHINE + "-alpine-dev"
PRODUCTION_NAME = MACHINE + "-alpine-prod"


def tar_bytes(files, cut=False, pax=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT if pax else tarfile.USTAR_FORMAT) as archive:
        for name, data in files:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            if pax:
                item.pax_headers = {"atime": "0", "ctime": "0"}
            archive.addfile(item, io.BytesIO(data))
        used = archive.offset
    raw = stream.getvalue()
    if cut:
        raw = raw[:used]
    return raw


@unittest.skipUnless(shutil.which("openssl"), "openssl is needed to verify real index signatures")
class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key_directory = tempfile.TemporaryDirectory(prefix="naos-apk-test-keys-")
        cls.private = Path(cls.key_directory.name) / "naos-apk.rsa"
        cls.public = Path(cls.key_directory.name) / server.PUBLIC_KEY_NAME
        subprocess.run(["openssl", "genrsa", "-out", str(cls.private), "2048"],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", str(cls.private), "-pubout", "-out", str(cls.public)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.key_directory.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="naos-apk-test-repo-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repository"
        self.incoming = Path(self.temporary.name) / "incoming"

    def index(self, records, architecture="x86_64", algorithm="RSA256", signature_name=None, pax=True):
        body = b""
        for record in records:
            name, version, data = (NAME, *record) if len(record) == 2 else record
            checksum = base64.b64encode(hashlib.sha1(data).digest()).decode()
            body += (f"C:Q1{checksum}\nP:{name}\nV:{version}\nA:{architecture}\n"
                     f"S:{len(data)}\nI:{len(data)}\nT:Test input package\nL:custom\n\n").encode()
        unsigned = gzip.compress(tar_bytes([("APKINDEX", body)]), mtime=0)
        signature = subprocess.check_output(["openssl", "dgst", "-sha256" if algorithm == "RSA256" else "-sha1",
                                             "-sign", str(self.private)], input=unsigned)
        name = signature_name or f".SIGN.{algorithm}.{server.PUBLIC_KEY_NAME}"
        return gzip.compress(tar_bytes([(name, signature)], cut=True, pax=pax), mtime=0) + unsigned

    def publish(self, version, data, index, expected="missing", architecture="x86_64", profile="development"):
        name = NAME if profile == "development" else PRODUCTION_NAME
        filename = name + "-" + version + ".apk"
        command = " ".join(("publish", profile, architecture, expected, filename))
        packet = io.BytesIO(tar_bytes([(filename, data), ("APKINDEX.tar.gz", index)]))
        return server.publish(self.root, self.incoming, self.public, command, packet)

    def test_signed_initial_publication_and_modes(self):
        data = b"test APK bytes"
        index = self.index([("1.0.0-r0", data)])
        result = self.publish("1.0.0-r0", data, index)
        self.assertTrue(result["published"])
        repository = self.root / "x86_64"
        self.assertEqual((repository / result["filename"]).read_bytes(), data)
        self.assertEqual((repository / "APKINDEX.tar.gz").read_bytes(), index)
        self.assertEqual((repository / result["filename"]).stat().st_mode & 0o777, 0o644)
        self.assertEqual((repository / "APKINDEX.tar.gz").stat().st_mode & 0o777, 0o644)
        self.assertEqual(list(self.incoming.iterdir()), [])
        self.assertFalse(any(path.name.endswith(".rsa") for path in self.root.rglob("*")))

    def test_second_publication_preserves_existing_apk(self):
        first, second = b"first", b"second"
        old = self.index([("1.0.0-r0", first)])
        self.publish("1.0.0-r0", first, old)
        new = self.index([("1.0.0-r0", first), ("1.1.0-r0", second)])
        self.publish("1.1.0-r0", second, new, hashlib.sha256(old).hexdigest())
        self.assertEqual((self.root / "x86_64" / (NAME + "-1.0.0-r0.apk")).read_bytes(), first)
        self.assertEqual((self.root / "x86_64/APKINDEX.tar.gz").read_bytes(), new)

    def test_both_profile_packages_share_the_repository_and_index_lock(self):
        development_filename = NAME + "-1.0.0-r0.apk"
        production_filename = PRODUCTION_NAME + "-1.0.0-r0.apk"
        development = self.index([("1.0.0-r0", b"development")])
        merged = self.index([(NAME, "1.0.0-r0", b"development"),
                             (PRODUCTION_NAME, "1.0.0-r0", b"production")])
        self.publish("1.0.0-r0", b"development", development, profile="development")
        with self.assertRaises(server.Conflict):
            self.publish("1.0.0-r0", b"production", merged, profile="production")
        self.publish("1.0.0-r0", b"production", merged,
                     hashlib.sha256(development).hexdigest(), profile="production")
        self.assertEqual((self.root / "x86_64" / development_filename).read_bytes(), b"development")
        self.assertEqual((self.root / "x86_64" / production_filename).read_bytes(), b"production")
        self.assertEqual((self.root / "x86_64/APKINDEX.tar.gz").read_bytes(), merged)
        self.assertTrue((self.root / "x86_64/.publish.lock").is_file())
        self.assertFalse((self.root / "development").exists())
        self.assertFalse((self.root / "production").exists())

    def test_stale_snapshot_is_conflict_without_mutation(self):
        data = b"first"
        old = self.index([("1.0.0-r0", data)])
        self.publish("1.0.0-r0", data, old)
        with self.assertRaises(server.Conflict):
            self.publish("1.1.0-r0", b"new", self.index([("1.1.0-r0", b"new")]))
        self.assertEqual((self.root / "x86_64/APKINDEX.tar.gz").read_bytes(), old)
        self.assertFalse((self.root / "x86_64" / (NAME + "-1.1.0-r0.apk")).exists())
        self.assertEqual(list(self.incoming.iterdir()), [])

    def test_same_filename_is_immutable_but_identical_retry_is_allowed(self):
        original = self.index([("1.0.0-r0", b"old")])
        self.publish("1.0.0-r0", b"old", original)
        snapshot = hashlib.sha256(original).hexdigest()
        self.publish("1.0.0-r0", b"old", original, snapshot)
        with self.assertRaisesRegex(server.RepositoryError, "different contents"):
            self.publish("1.0.0-r0", b"new", self.index([("1.0.0-r0", b"new")]), snapshot)
        self.assertEqual((self.root / "x86_64/APKINDEX.tar.gz").read_bytes(), original)

    def test_bad_signature_and_wrong_signing_identity_are_rejected(self):
        index = self.index([("1.0.0-r0", b"data")])
        corrupt = bytearray(index)
        corrupt[-9] ^= 1
        for candidate in (bytes(corrupt), self.index([("1.0.0-r0", b"data")], signature_name=".SIGN.RSA256.other.pub")):
            with self.subTest(candidate_length=len(candidate)):
                with self.assertRaises(server.RepositoryError):
                    self.publish("1.0.0-r0", b"data", candidate)
        self.assertFalse((self.root / "x86_64/APKINDEX.tar.gz").exists())

    def test_uploaded_package_must_match_signed_index(self):
        with self.assertRaisesRegex(server.RepositoryError, "does not describe"):
            self.publish("1.0.0-r0", b"different size", self.index([("1.0.0-r0", b"small")]))

    def test_legacy_rsa_and_second_architecture(self):
        data = b"aarch64 payload"
        index = self.index([("1.0.0-r0", data)], architecture="aarch64", algorithm="RSA")
        self.publish("1.0.0-r0", data, index, architecture="aarch64")
        self.assertTrue((self.root / "aarch64/APKINDEX.tar.gz").is_file())

    def test_plain_ustar_signature_is_also_accepted(self):
        data = b"USTAR signature fixture"
        self.publish("1.0.0-r0", data, self.index([("1.0.0-r0", data)], pax=False))

    def test_index_rename_failure_preserves_old_index_and_retry_can_complete(self):
        old = self.index([("1.0.0-r0", b"old")])
        self.publish("1.0.0-r0", b"old", old)
        new = self.index([("1.0.0-r0", b"old"), ("1.1.0-r0", b"new")])
        snapshot = hashlib.sha256(old).hexdigest()
        replace = server.os.replace

        def fail_index(source, destination):
            if Path(destination).name == "APKINDEX.tar.gz":
                raise OSError("simulated index rename failure")
            return replace(source, destination)

        with mock.patch.object(server.os, "replace", side_effect=fail_index):
            with self.assertRaises(OSError):
                self.publish("1.1.0-r0", b"new", new, snapshot)
        self.assertEqual((self.root / "x86_64/APKINDEX.tar.gz").read_bytes(), old)
        self.publish("1.1.0-r0", b"new", new, snapshot)
        self.assertEqual((self.root / "x86_64/APKINDEX.tar.gz").read_bytes(), new)

    def test_strict_command_and_tar_input(self):
        for command in ("sh", "publish development x86_64 missing ../../bad.apk", "publish development armhf missing a.apk",
                        "publish development x86_64 nope a.apk", "publish development x86_64 missing a.apk;id",
                        "publish ../../wrong x86_64 missing a.apk"):
            with self.subTest(command=command), self.assertRaises(server.RepositoryError):
                server.parse_command(command)
        staged = Path(self.temporary.name) / "staged"
        staged.mkdir()
        for files in ([('../outside', b'x')], [('a.apk', b'first'), ('a.apk', b'second')], [('a.apk', b'x')]):
            with self.subTest(files=[name for name, _ in files]), self.assertRaises(server.RepositoryError):
                with tempfile.TemporaryDirectory(dir=staged) as directory:
                    server.receive(io.BytesIO(tar_bytes(files)), Path(directory), "a.apk")
        packet = io.BytesIO()
        with tarfile.open(fileobj=packet, mode="w") as archive:
            member = tarfile.TarInfo("a.apk")
            member.type, member.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(member)
        with self.assertRaises(server.RepositoryError):
            server.receive(io.BytesIO(packet.getvalue()), staged, "a.apk")


class ClientTests(unittest.TestCase):
    def test_package_metadata_checks_and_concrete_architecture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            filename = NAME + "-1.0.0-r0.apk"
            apk = root / filename
            apk.write_bytes(b"data")
            data = {"schema_version": 1, "filename": filename, "pkgname": NAME,
                    "version": "1.0.0-r0", "architecture": "x86_64", "profile": "development", "machine": MACHINE, "size": 4,
                    "sha256": hashlib.sha256(b"data").hexdigest()}
            (root / "package.json").write_text(json.dumps(data))
            self.assertEqual(client.package_input(root)[0], apk)
            production_apk = root / (PRODUCTION_NAME + "-1.0.0-r0.apk")
            production_apk.write_bytes(b"data")
            production_data = {**data, "filename": production_apk.name,
                               "pkgname": PRODUCTION_NAME, "profile": "production"}
            (root / "package.json").write_text(json.dumps(production_data))
            self.assertEqual(client.package_input(root)[0], production_apk)
            wrong_profile = {**data, "profile": "production"}
            (root / "package.json").write_text(json.dumps(wrong_profile))
            with self.assertRaisesRegex(client.PublishError, "machine and profile"):
                client.package_input(root)
            (root / "package.json").write_text(json.dumps(data))
            apk.write_bytes(b"different")
            with self.assertRaises(client.PublishError):
                client.package_input(root)

    def test_index_commands_verify_old_and_new_without_allow_untrusted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            commands = []
            with mock.patch.object(client, "run", side_effect=lambda command: commands.append([str(x) for x in command])):
                client.build_index(root / "new.apk", root / "old.tar.gz", root / "APKINDEX.tar.gz", root / "keys", "x86_64")
            self.assertTrue(all("--allow-untrusted" not in command for command in commands))
            self.assertIn("--merge", commands[0])
            self.assertIn(str(root / "old.tar.gz"), commands[0])
            self.assertEqual(commands[1][:4], ["abuild-sign", "-q", "-t", "RSA256"])
            self.assertIn(str(root / "keys/naos-apk.rsa"), commands[1])
            self.assertIn(str(root / "keys/naos-apk.rsa.pub"), commands[1])
            self.assertEqual(commands[2][0], "apk")
            self.assertIn("--index", commands[2])
            self.assertIn(str(root / "APKINDEX.tar.gz"), commands[2])

    def test_conflict_refetches_and_rebuilds_index(self):
        args = SimpleNamespace(repository_url="http://example.test/repository", arch="x86_64", profile="development")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(client, "fetch_index", side_effect=["missing", "a" * 64]) as fetch, \
                    mock.patch.object(client, "build_index") as build, \
                    mock.patch.object(client, "send_update", side_effect=[75, 0]) as send:
                report = client.publish_attempts(args, root / "new.apk", root / "keys", root)
            self.assertEqual(report["attempts"], 2)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.args[0], "http://example.test/repository/x86_64/APKINDEX.tar.gz")
            self.assertIsNone(build.call_args_list[0].args[1])
            self.assertEqual(build.call_args_list[1].args[1], root / "previous-index.tar.gz")
            self.assertEqual(send.call_args_list[1].args[3], "a" * 64)

    def test_conflicts_have_bounded_retries(self):
        args = SimpleNamespace(repository_url="https://example.test/repository", arch="x86_64", profile="production")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(client, "fetch_index", return_value="missing"), \
                    mock.patch.object(client, "build_index"), \
                    mock.patch.object(client, "send_update", return_value=75) as send:
                with self.assertRaises(client.PublishError):
                    client.publish_attempts(args, root / "new.apk", root / "keys", root)
            self.assertEqual(send.call_count, 3)

    def test_repository_urls_reject_credentials_and_queries(self):
        for value in ("ftp://example.test/repo", "http://user:secret@example.test/repo", "http://example.test/repo?token=secret"):
            with self.subTest(value=value), self.assertRaises(client.PublishError):
                client.repository_url(value)

    def test_tool_diagnostics_are_bounded_and_do_not_include_private_material(self):
        private = b"-----BEGIN PRIVATE KEY-----\n" + b"A" * 128 + b"\n-----END PRIVATE KEY-----"
        result = client.diagnostic(b"x" * 2000 + b"\n" + private + b"\nreason: BAD signature\n" + b"f" * 64)
        self.assertLessEqual(len(result), 1600)
        self.assertIn("reason: BAD signature", result)
        self.assertNotIn("BEGIN PRIVATE KEY", result)
        self.assertNotIn("A" * 128, result)
        self.assertNotIn("f" * 64, result)


if __name__ == "__main__":
    unittest.main()

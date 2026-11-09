#!/usr/bin/env python3
"""Receive one APK and its signed repository index through a restricted SSH key."""

import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import zlib


ARCHITECTURES = ("x86_64", "aarch64")
PROFILES = ("development", "production")
PUBLIC_KEY_NAME = "naos-apk.rsa.pub"
MAX_APK_BYTES = 2 * 1024**3
MAX_INDEX_BYTES = 64 * 1024**2
MAX_INDEX_CONTENT = 32 * 1024**2
FILENAME = re.compile(r"[a-z0-9][a-z0-9+_.-]*\.apk\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class RepositoryError(ValueError):
    pass


class Conflict(RepositoryError):
    """Another publisher committed after the client downloaded its index."""


def require(condition, message):
    if not condition:
        raise RepositoryError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def parse_command(command):
    require(isinstance(command, str) and len(command) <= 512, "Invalid publish command")
    try:
        parts = shlex.split(command)
    except ValueError:
        raise RepositoryError("Invalid publish command") from None
    require(len(parts) == 5 and parts[0] == "publish", "Only the publish command is allowed")
    _, profile, architecture, previous, filename = parts
    require(profile in PROFILES, "Unsupported repository profile")
    require(architecture in ARCHITECTURES, "Unsupported repository architecture")
    require(previous == "missing" or DIGEST.fullmatch(previous), "Invalid previous index digest")
    require(len(filename) <= 100 and FILENAME.fullmatch(filename), "Invalid APK filename")
    return profile, architecture, previous, filename


def _regular(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    require(stat.S_ISREG(mode), "Repository files must be ordinary files")
    return True


def _exact(stream, size):
    chunks = []
    while size:
        chunk = stream.read(min(size, 1024 * 1024))
        require(bool(chunk), "Truncated upload")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def receive(stream, directory, filename):
    """Read USTAR headers directly: links, paths and extended headers are rejected."""
    expected = {filename: MAX_APK_BYTES, "APKINDEX.tar.gz": MAX_INDEX_BYTES}
    received = set()
    while True:
        header = _exact(stream, 512)
        if header == bytes(512):
            require(_exact(stream, 512) == bytes(512), "Invalid tar terminator")
            trailer = stream.read(65537)
            require(len(trailer) <= 65536 and not trailer.strip(b"\0"), "Unexpected upload trailer")
            break
        try:
            member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        except (tarfile.HeaderError, UnicodeError):
            raise RepositoryError("Invalid upload header") from None
        require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not member.linkname,
                "Only ordinary upload files are allowed")
        require(member.name in expected and member.name not in received, "Unexpected or duplicate upload file")
        require(0 < member.size <= expected[member.name], "Upload file exceeds its size limit")
        target = directory / member.name
        with target.open("xb") as output:
            remaining = member.size
            while remaining:
                chunk = _exact(stream, min(remaining, 1024 * 1024))
                output.write(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        padding = _exact(stream, (-member.size) % 512)
        require(not padding.strip(b"\0"), "Invalid tar padding")
        received.add(member.name)
    require(received == set(expected), "Upload must contain one APK and APKINDEX.tar.gz")


def _gzip_member(data, limit):
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        decoded = decoder.decompress(data, limit + 1)
    except zlib.error:
        raise RepositoryError("Invalid compressed index") from None
    require(decoder.eof and len(decoded) <= limit, "Truncated or oversized compressed index")
    return decoded, decoder.unused_data


def _index_records(content, architecture):
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise RepositoryError("Invalid APKINDEX encoding") from None
    records = {}
    for paragraph in text.strip().split("\n\n"):
        if not paragraph:
            continue
        fields = {}
        for line in paragraph.splitlines():
            require(len(line) >= 2 and line[1] == ":", "Invalid APKINDEX record")
            key, value = line[0], line[2:]
            require(key not in fields, "Duplicate APKINDEX field")
            fields[key] = value
        require(all(fields.get(key) for key in ("P", "V", "A", "S", "C")), "Incomplete APKINDEX record")
        require(fields["A"] == architecture, "Index contains another architecture")
        filename = fields["P"] + "-" + fields["V"] + ".apk"
        require(len(filename) <= 100 and FILENAME.fullmatch(filename), "Invalid indexed APK filename")
        require(filename not in records, "Duplicate APKINDEX package")
        try:
            size = int(fields["S"])
        except ValueError:
            raise RepositoryError("Invalid indexed APK size") from None
        require(0 < size <= MAX_APK_BYTES, "Invalid indexed APK size")
        records[filename] = {**fields, "size": size}
    require(bool(records), "The repository index must not be empty")
    return records


def verify_index(path, public_key, architecture, directory):
    """Verify the v2 abuild signature using only the published RSA public key."""
    require(_regular(public_key), "Repository public key is missing")
    require(public_key.name == PUBLIC_KEY_NAME, "Unexpected repository public key name")
    require(path.stat().st_size <= MAX_INDEX_BYTES, "Repository index exceeds its size limit")
    signature_tar, unsigned = _gzip_member(path.read_bytes(), 16384)
    require(bool(unsigned), "A signed APKINDEX is required")
    try:
        # abuild uses POSIX tar with small atime/ctime PAX headers. The decoded
        # signature archive is bounded above and is never extracted to disk.
        with tarfile.open(fileobj=io.BytesIO(signature_tar), mode="r:") as archive:
            members = list(archive)
            require(len(members) == 1, "Unexpected index signature members")
            member = members[0]
            require(member.isreg() and 128 <= member.size <= 1024, "Invalid index signature data")
            signature_data = archive.extractfile(member).read()
    except (tarfile.TarError, UnicodeError):
        raise RepositoryError("Invalid index signature header") from None
    allowed = {".SIGN.RSA." + PUBLIC_KEY_NAME: "-sha1",
               ".SIGN.RSA256." + PUBLIC_KEY_NAME: "-sha256"}
    require(member.isreg() and member.name in allowed and 128 <= member.size <= 1024,
            "Index signature does not use the repository key")
    end = member.offset_data + ((member.size + 511) // 512) * 512
    require(len(signature_tar) == end and not signature_tar[member.offset_data + member.size:].strip(b"\0"),
            "Unexpected index signature data")
    index_tar, extra = _gzip_member(unsigned, MAX_INDEX_CONTENT)
    require(not extra, "Unexpected compressed index members")
    signature = directory / "index-signature.bin"
    signature.write_bytes(signature_data)
    payload = directory / "unsigned-index.tar.gz"
    payload.write_bytes(unsigned)
    result = subprocess.run(["openssl", "dgst", allowed[member.name], "-verify", str(public_key),
                             "-signature", str(signature), str(payload)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    require(result.returncode == 0, "Repository index signature verification failed")
    contents = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(index_tar), mode="r:") as archive:
            for entry in archive:
                require(entry.isreg() and entry.name in ("APKINDEX", "DESCRIPTION")
                        and entry.name not in contents, "Unexpected repository index content")
                contents[entry.name] = archive.extractfile(entry).read()
    except tarfile.TarError:
        raise RepositoryError("Invalid repository index archive") from None
    require("APKINDEX" in contents, "APKINDEX is missing")
    return _index_records(contents["APKINDEX"], architecture)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(root, incoming, public_key, command, stream):
    profile, architecture, expected, filename = parse_command(command)
    root, incoming, public_key = Path(root), Path(incoming), Path(public_key)
    require(not root.is_symlink() and not incoming.is_symlink(), "Invalid repository directory")
    root.mkdir(parents=True, exist_ok=True)
    incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
    repository = root / architecture
    require(not repository.is_symlink(), "Invalid architecture directory")
    repository.mkdir(mode=0o755, exist_ok=True)
    require(repository.stat().st_dev == incoming.stat().st_dev,
            "Repository and incoming directory must share a filesystem")
    with tempfile.TemporaryDirectory(prefix="publish-", dir=incoming) as temporary:
        staged = Path(temporary)
        receive(stream, staged, filename)
        entries = verify_index(staged / "APKINDEX.tar.gz", public_key, architecture, staged)
        require(filename in entries and entries[filename]["size"] == (staged / filename).stat().st_size,
                "Signed index does not describe the uploaded APK")
        suffix = "-alpine-dev" if profile == "development" else "-alpine-prod"
        require(entries[filename]["P"].endswith(suffix), "Package name does not match the publish profile")
        apk_digest = digest(staged / filename)
        index_digest = digest(staged / "APKINDEX.tar.gz")
        lock = repository / ".publish.lock"
        descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            index = repository / "APKINDEX.tar.gz"
            current = digest(index) if _regular(index) else "missing"
            if current != expected:
                raise Conflict("Repository index changed; fetch it again and retry")
            destination = repository / filename
            exists = _regular(destination)
            require(not exists or digest(destination) == apk_digest,
                    "An APK with the same filename already has different contents")
            for name in (filename, "APKINDEX.tar.gz"):
                os.chmod(staged / name, 0o644)
                with (staged / name).open("rb") as file:
                    os.fsync(file.fileno())
            if not exists:
                os.replace(staged / filename, destination)
                _sync_directory(repository)
            os.replace(staged / "APKINDEX.tar.gz", index)
            _sync_directory(repository)
        return {"published": True, "profile": profile, "architecture": architecture, "filename": filename,
                "sha256": apk_digest, "index_sha256": index_digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/srv/naos/apk/repository")
    parser.add_argument("--incoming", default="/var/lib/naos-apk/incoming")
    parser.add_argument("--public-key", default="/srv/naos/apk/keys/" + PUBLIC_KEY_NAME)
    args = parser.parse_args()
    try:
        report = publish(args.root, args.incoming, args.public_key,
                         os.environ.get("SSH_ORIGINAL_COMMAND", ""), sys.stdin.buffer)
        print(json.dumps(report))
        return 0
    except Conflict as error:
        print(str(error), file=sys.stderr)
        return 75
    except (RepositoryError, OSError, subprocess.SubprocessError) as error:
        print("Repository publication failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

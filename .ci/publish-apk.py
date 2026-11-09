#!/usr/bin/env python3
"""Sign a merged APKINDEX in CI, then publish it with its APK over SSH."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request


ARCHITECTURES = ("x86_64", "aarch64")
PROFILES = ("development", "production")
MAX_INDEX_BYTES = 64 * 1024**2
KEY_NAME = "naos-apk.rsa"
APK_FILENAME = re.compile(r"[a-z0-9][a-z0-9+_.-]*\.apk\Z")


class PublishError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise PublishError(message)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def diagnostic(data):
    text = data.decode(errors="replace")
    text = re.sub(r"-----BEGIN [^\n]*PRIVATE KEY-----.*?(?:-----END [^\n]*PRIVATE KEY-----|\Z)",
                  "[private key omitted]", text, flags=re.DOTALL)
    text = re.sub(r"\b[0-9a-fA-F]{64,}\b", "[hex omitted]", text)
    text = re.sub(r"(?m)^[A-Za-z0-9+/]{64,}={0,2}$", "[encoded data omitted]", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "?", text)
    return text[-1600:].strip()


def run(command):
    result = subprocess.run([str(value) for value in command], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=120)
    if result.returncode:
        detail = diagnostic(result.stderr)
        raise PublishError(f"{Path(command[0]).name} failed (exit {result.returncode})"
                           + (": " + detail if detail else ""))
    return result.stdout


def package_input(directory):
    directory = Path(directory)
    try:
        document = json.loads((directory / "package.json").read_text())
    except (OSError, ValueError):
        raise PublishError("Cannot read package.json") from None
    filename = document.get("filename")
    require(document.get("schema_version") == 1 and isinstance(filename, str)
            and len(filename) <= 100 and APK_FILENAME.fullmatch(filename), "Invalid package metadata")
    require(filename == document.get("pkgname", "") + "-" + document.get("version", "") + ".apk",
            "Package filename differs from its metadata")
    require(document.get("architecture") in ("noarch", *ARCHITECTURES), "Invalid package architecture")
    require(document.get("profile") in PROFILES, "Invalid package profile")
    machine = document.get("machine")
    require(isinstance(machine, str) and re.fullmatch(r"[a-z0-9][a-z0-9+_.-]*", machine),
            "Invalid package machine")
    expected_name = machine + ("-alpine-dev" if document["profile"] == "development" else "-alpine-prod")
    require(document["pkgname"] == expected_name, "Package name does not match its machine and profile")
    apk = directory / filename
    require(apk.is_file() and not apk.is_symlink(), "The APK must be an ordinary file")
    require(type(document.get("size")) is int and document["size"] == apk.stat().st_size
            and 0 < document["size"] <= 2 * 1024**3, "APK size differs from its metadata")
    require(isinstance(document.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", document["sha256"])
            and sha256(apk) == document["sha256"], "APK digest differs from its metadata")
    return apk, document


def repository_url(value):
    parts = urllib.parse.urlsplit(value)
    require(parts.scheme in ("http", "https") and parts.hostname and not parts.username
            and not parts.password and not parts.query and not parts.fragment,
            "Expected a repository HTTP(S) URL without credentials")
    return value.rstrip("/")


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise PublishError("Unexpected repository redirect")


def fetch_index(url, destination):
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
    opener = urllib.request.build_opener(NoRedirects())
    try:
        with opener.open(request, timeout=60) as response:
            contents = response.read(MAX_INDEX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return "missing"
        raise PublishError(f"Cannot fetch repository index: HTTP {error.code}") from None
    except urllib.error.URLError:
        raise PublishError("Cannot fetch repository index") from None
    require(0 < len(contents) <= MAX_INDEX_BYTES, "Invalid repository index size")
    destination.write_bytes(contents)
    return hashlib.sha256(contents).hexdigest()


def copy_input(source, target, limit):
    source = Path(source)
    require(source.is_file() and not source.is_symlink() and 0 < source.stat().st_size <= limit,
            "A required key or known-hosts file is missing or invalid")
    with source.open("rb") as original, target.open("xb") as output:
        os.chmod(target, 0o600)
        shutil.copyfileobj(original, output)


def prepare_keys(args, directory):
    keys = directory / "keys"
    keys.mkdir(mode=0o700)
    copy_input(args.signing_key, keys / KEY_NAME, 65536)
    copy_input(args.public_key, keys / (KEY_NAME + ".pub"), 16384)
    copy_input(args.ssh_key, directory / "ssh-key", 65536)
    copy_input(args.known_hosts, directory / "known_hosts", 65536)
    private_public = run(["openssl", "pkey", "-in", keys / KEY_NAME, "-pubout", "-outform", "DER"])
    public = run(["openssl", "pkey", "-pubin", "-in", keys / (KEY_NAME + ".pub"), "-outform", "DER"])
    require(private_public == public, "The APK signing key and public key do not match")
    return keys


def build_index(apk, previous, output, keys, architecture):
    command = ["apk", "--keys-dir", keys, "index", "--no-warnings", "--rewrite-arch", architecture,
               "--description", "NAOS image-generator inputs", "--output", output]
    if previous:
        # Reading an existing index checks its signature against this keys dir.
        command += ["--merge", "--index", previous]
    run(command + [apk])
    run(["abuild-sign", "-q", "-t", "RSA256", "-k", keys / KEY_NAME,
         "-p", keys / (KEY_NAME + ".pub"), output])
    # apk verify handles packages, not APKINDEX: re-read the signed index instead.
    run(["apk", "--keys-dir", keys, "index", "--no-warnings", "--merge", "--index", output,
         "--output", output.with_name("verified-index.tar.gz")])


def send_update(args, apk, index, previous, directory):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", args.ssh_host), "Invalid SSH host")
    require(re.fullmatch(r"[a-z_][a-z0-9_-]*", args.ssh_user), "Invalid SSH user")
    command = ["ssh", "-T", "-i", str(directory / "ssh-key"), "-o", "BatchMode=yes",
               "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "UserKnownHostsFile=" + str(directory / "known_hosts"),
               "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=3", args.ssh_user + "@" + args.ssh_host,
               shlex.join(["publish", args.profile, args.arch, previous, apk.name])]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = threading.Timer(600, process.kill)
    deadline.daemon = True
    deadline.start()
    write_error = None
    try:
        try:
            with tarfile.open(fileobj=process.stdin, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
                for path, name in ((apk, apk.name), (index, "APKINDEX.tar.gz")):
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = path.stat().st_size, 0o644
                    with path.open("rb") as source:
                        archive.addfile(info, source)
        except (BrokenPipeError, OSError) as error:
            write_error = error
        finally:
            try:
                process.stdin.close()
            except OSError as error:
                write_error = error
            finally:
                process.stdin = None
        stdout, stderr = process.communicate(timeout=600)
    except BaseException:
        process.kill()
        process.communicate()
        raise
    finally:
        deadline.cancel()
    if process.returncode not in (0, 75):
        detail = diagnostic(stderr)
        raise PublishError(f"SSH publication failed (exit {process.returncode})"
                           + (": " + detail if detail else ""))
    if write_error and process.returncode == 0:
        raise PublishError("SSH publication upload was incomplete")
    return process.returncode


def publish_attempts(args, apk, keys, directory):
    require(args.profile in PROFILES, "Invalid package profile")
    url = repository_url(args.repository_url) + "/" + args.arch + "/APKINDEX.tar.gz"
    for attempt in range(1, 4):
        old = directory / "previous-index.tar.gz"
        previous = fetch_index(url, old)
        index = directory / "APKINDEX.tar.gz"
        build_index(apk, old if previous != "missing" else None, index, keys, args.arch)
        if send_update(args, apk, index, previous, directory) == 0:
            return {"published": True, "profile": args.profile, "architecture": args.arch, "filename": apk.name,
                    "repository_url": repository_url(args.repository_url),
                    "attempts": attempt}
        print("Repository changed during publication; rebuilding its index", file=sys.stderr)
    raise PublishError("Repository changed during all three publication attempts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="apk-output")
    parser.add_argument("--repository-url", required=True)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--ssh-user", default="ubuntu")
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--known-hosts", required=True)
    parser.add_argument("--signing-key", required=True)
    parser.add_argument("--public-key", required=True)
    parser.add_argument("--arch", choices=ARCHITECTURES, default="x86_64")
    args = parser.parse_args()
    try:
        apk, document = package_input(args.output_dir)
        args.profile = document["profile"]
        require(document["architecture"] in ("noarch", args.arch), "Package and repository architectures differ")
        os.umask(0o077)
        with tempfile.TemporaryDirectory(prefix="naos-apk-publish-") as temporary:
            directory = Path(temporary)
            keys = prepare_keys(args, directory)
            run(["apk", "--keys-dir", keys, "verify", apk])
            print(json.dumps(publish_attempts(args, apk, keys, directory)))
        return 0
    except (PublishError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        print("APK publication failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

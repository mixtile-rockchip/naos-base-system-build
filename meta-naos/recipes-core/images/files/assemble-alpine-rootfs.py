#!/usr/bin/env python3
"""Assemble signed Alpine APK inputs in a rootless namespace and real chroot.

The target fakeroot library records target ownership, including changes made by
APK scripts. Its state is used to create the archive, so a single-ID user
namespace never collapses package owners to the invoking host user. Build-only
libraries and mounts are removed before packaging.
"""

import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command, *, env, input=None, reject_apk_errors=False, log_output=True):
    print("+ " + shlex.join(str(item) for item in command), flush=True)
    result = subprocess.run([str(item) for item in command], env=env, input=input,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, check=False)
    if log_output:
        print(result.stdout, end="", flush=True)
    if result.returncode:
        raise RuntimeError("command failed with exit status %d" % result.returncode)
    if reject_apk_errors and re.search(r"(^|\n)ERROR:|script exited with error", result.stdout):
        raise RuntimeError("APK reported an installation or script error")
    return result.stdout.strip()


def write_file(path, contents, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)


def validate_seed(path):
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise RuntimeError("unsafe seed archive member: " + member.name)


def ssh_login_policy(args):
    policy = {"PermitRootLogin": "no", "PasswordAuthentication": "yes" if args.default_password else "no"}
    if args.ssh_ca_key:
        policy.update({"PermitRootLogin": "prohibit-password", "PasswordAuthentication": "no", "KbdInteractiveAuthentication": "no",
                       "AuthenticationMethods": "publickey", "PubkeyAuthentication": "yes",
                       "AuthorizedKeysFile": "none", "AuthorizedKeysCommand": "none",
                       "TrustedUserCAKeys": "/etc/ssh/ssh_ca_key.pub"})
    return policy


def effective_ssh_options(contents):
    options = dict(line.split(None, 1) for line in contents.splitlines() if " " in line)
    if options.get("permitrootlogin") == "without-password":
        options["permitrootlogin"] = "prohibit-password"
    # OpenSSH omits the line when its AuthorizedKeysFile list is empty.
    options.setdefault("authorizedkeysfile", "none")
    return options


def read_ssh_ca_key(path):
    contents = path.read_text()
    lines = [line.strip() for line in contents.splitlines() if line.strip()]
    try:
        if len(lines) != 1 or "-----BEGIN" in contents or "PRIVATE KEY" in contents:
            raise ValueError("not a single public key")
        fields = lines[0].split(None, 2)
        if len(fields) < 2 or "-cert-v01@" in fields[0]:
            raise ValueError("not a CA public key")
        wire = base64.b64decode(fields[1], validate=True)
        size = int.from_bytes(wire[:4], "big")
        if len(wire) < 4 + size or not size or wire[4:4 + size] != fields[0].encode("ascii"):
            raise ValueError("public key type differs from wire format")
    except (ValueError, UnicodeError):
        raise RuntimeError("SSH CA input must be a single OpenSSH public key, not a private key or certificate") from None
    return contents


def configured_inittab(contents, serial_consoles, enabled):
    terminals = {"console", *(tty for baud, tty in serial_consoles)}
    entries = []
    for line in contents.splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            entries.append(line)
            continue
        fields = line.split(":", 3)
        if fields[0] in terminals:
            continue
        if not enabled and len(fields) == 4 and (fields[2] == "askfirst" or
                re.search(r"(?:^|[\s/])(?:[\w.-]*getty|login)(?:\s|$)", fields[3])):
            continue
        entries.append(line)
    if enabled:
        entries += [f"{tty}::respawn:/sbin/getty -L {baud} {tty} vt100" for baud, tty in serial_consoles]
    return "\n".join(entries) + "\n"


def installed_packages(root):
    records = []
    database = (root / "lib/apk/db/installed").read_text(encoding="utf-8")
    for paragraph in database.split("\n\n"):
        fields = dict(line.split(":", 1) for line in paragraph.splitlines()
                      if ":" in line)
        if "P" not in fields:
            continue
        if fields.get("A") not in {"aarch64", "noarch"}:
            raise RuntimeError("unexpected package architecture: " + fields.get("A", ""))
        records.append({"name": fields["P"], "version": fields.get("V", ""),
                        "architecture": fields.get("A", ""),
                        "license": fields.get("L", ""), "origin": fields.get("o", "")})
    if not records:
        raise RuntimeError("Alpine installed-package database is empty")
    return sorted(records, key=lambda item: (item["name"], item["version"]))


class Target:
    def __init__(self, root, env):
        self.root, self.env, self.key, self.pid = root, env, None, None

    def run(self, command, **kwargs):
        prefix = ["chroot", self.root]
        if self.key:
            prefix += ["/usr/bin/env", "FAKEROOTKEY=" + self.key,
                       "LD_PRELOAD=/.naos-build/helper/usr/lib/libfakeroot.so:/.naos-build/libnaos-chown-compat.so"]
        return run(prefix + list(command), env=self.env, **kwargs)

    def start(self):
        pending = []
        # Defer Python's termination handler until the daemon PID is recorded.
        # Unlike blocking signals, this does not leave faked inheriting a
        # blocked SIGTERM when subprocess.run starts it.
        previous_handlers = {number: signal.signal(number, lambda signum, frame: pending.append(signum))
                             for number in (signal.SIGTERM, signal.SIGINT)}
        try:
            result = self.run(["/.naos-build/helper/usr/bin/faked", "--save-file", "/.naos-build/ownership.state"])
            match = re.fullmatch(r"([0-9]+):([0-9]+)", result)
            if not match:
                raise RuntimeError("fakeroot daemon did not provide a key and PID")
            self.key, self.pid = match.group(1), int(match.group(2))
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
        if pending:
            terminate(pending[0], None)

    def _running(self):
        # In private PID mode the daemon can become our adopted child. Reap it
        # if possible; in container mode it may instead belong to container init.
        try:
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                return False
        except ChildProcessError:
            pass
        try:
            status = Path("/proc/%d/stat" % self.pid).read_text()
        except FileNotFoundError:
            return False
        return status.rsplit(")", 1)[1].split()[0] != "Z"

    def _wait_stopped(self, timeout):
        deadline = time.monotonic() + timeout
        while self._running():
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def stop(self):
        if self.pid:
            # Synchronize the last metadata request before asking faked to save.
            error = None
            try:
                self.run(["/bin/ls", "-ldn", "/"])
            except (OSError, RuntimeError) as failure:
                error = failure
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if not self._wait_stopped(10):
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if not self._wait_stopped(2):
                    raise RuntimeError("cannot stop fakeroot daemon")
                if error is None:
                    error = RuntimeError("fakeroot daemon did not stop gracefully")
            self.pid = None
            self.key = None
            if error:
                raise error
            state = self.root / ".naos-build/ownership.state"
            if not state.is_file():
                raise RuntimeError("fakeroot did not save its ownership metadata")


def mount_rootfs(command, path, recursive, mounts, env):
    # A termination signal must not separate mounting from its cleanup record.
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
    try:
        run(command + [path], env=env)
        mounts.append((path, recursive))
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def unmount_rootfs(mounts, env):
    errors = []
    for path, recursive in reversed(mounts[:]):
        command = ["umount"]
        if recursive:
            # Detach the complete proc tree, including Docker's locked child
            # mounts. Unmounting each child would fail or remove protection.
            command.append("-l")
        try:
            run(command + [path], env=env)
        except (OSError, RuntimeError) as error:
            errors.append(str(path) + ": " + str(error))
        else:
            mounts.remove((path, recursive))
    if errors:
        raise RuntimeError("cannot unmount rootfs: " + "; ".join(errors))


def cleanup_rootfs(target, mounts, workspace, env):
    errors = []
    previous_handlers = {number: signal.signal(number, signal.SIG_IGN)
                         for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        if target and target.pid:
            try:
                target.stop()
            except Exception as error:
                errors.append("cannot stop fakeroot: " + str(error))
        try:
            unmount_rootfs(mounts, env)
        except Exception as error:
            errors.append(str(error))
        if mounts or (target and target.pid):
            errors.append("retained workspace with active mounts or daemon: " + str(workspace))
        else:
            try:
                shutil.rmtree(workspace)
            except OSError as error:
                errors.append("cannot remove workspace: " + str(error))
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)
    return errors


def terminate(signum, frame):
    raise SystemExit(128 + signum)


def configure_rootfs(root, target, args, serial_consoles, extra_services):
    def configure_file(relative, contents, mode=0o644, owner="0:0"):
        path = root / relative
        missing_parents = []
        parent = path.parent
        while not parent.exists():
            missing_parents.append(parent)
            parent = parent.parent
        write_file(path, contents, mode)
        # Host writes bypass target LD_PRELOAD. Refresh their fake metadata to
        # prevent an inode recycled after package installation retaining an old
        # owner or mode in fakeroot's database.
        for directory in reversed(missing_parents):
            relative_directory = "/" + directory.relative_to(root).as_posix()
            target.run(["/bin/chown", "0:0", relative_directory])
            target.run(["/bin/chmod", oct(stat.S_IMODE(directory.stat().st_mode))[2:], relative_directory])
        target_path = "/" + relative
        target.run(["/bin/chown", owner, target_path])
        target.run(["/bin/chmod", oct(mode)[2:], target_path])

    configure_file("etc/apk/world", "\n".join(shlex.split(args.packages)) + "\n")
    configure_file("etc/apk/repositories", args.repositories.read_text())
    configure_file("etc/hostname", "naos\n")
    configure_file("etc/hosts", "127.0.0.1\tlocalhost naos\n::1\tlocalhost naos\n")
    configure_file("etc/profile.d/naos-prompt.sh",
                   'if [ -n "${BASH_VERSION:-}${BB_ASH_VERSION:-}" ]; then\n'
                   "    PS1='\\u@\\h:\\w\\$ '\n"
                   "fi\n")
    configure_file("etc/network/interfaces", "auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n")
    # The developer account is a normal sudo-capable user. Root's Unix
    # password remains disabled; certificate policy may permit root SSH.
    target.run(["/usr/sbin/adduser", "-D", "-h", "/home/" + args.default_user,
                "-s", "/bin/sh", args.default_user])
    target.run(["/usr/sbin/addgroup", args.default_user, "wheel"])
    target.run(["/bin/chown", "-R", args.default_user + ":" + args.default_user,
                "/home/" + args.default_user])
    if args.default_password and not args.ssh_ca_key:
        # A fixed developer credential must not introduce random salts into an
        # otherwise reproducible image. Certificate-only policy is applied last.
        password_hash = target.run(["/bin/busybox", "mkpasswd", "-m", "sha512",
                                    "-S", "naos-dev", "-P", "0"],
                                   input=args.default_password + "\n", log_output=False)
        target.run(["/usr/sbin/chpasswd", "-e"], input=args.default_user + ":" + password_hash + "\n")
    elif not args.ssh_ca_key:
        target.run(["/usr/bin/passwd", "-l", args.default_user])
    target.run(["/usr/bin/passwd", "-l", "root"])
    shadow = root / "etc/shadow"
    entries = []
    for entry in shadow.read_text().splitlines():
        fields = entry.split(":")
        if fields[0] in {"root", args.default_user}:
            fields[2] = str(args.epoch // 86400)
        entries.append(":".join(fields))
    configure_file("etc/shadow", "\n".join(entries) + "\n", 0o640, "root:shadow")
    # Account tools replace files via rename. Refresh fake inode metadata after
    # those replacements so recycled shadow inodes cannot hide passwd/group.
    for name in ("passwd", "group"):
        configure_file("etc/" + name, (root / "etc" / name).read_text(), 0o644)
    for name in ("passwd", "shadow", "group", "gshadow"):
        (root / "etc" / (name + "-")).unlink(missing_ok=True)
    # Product/board configuration is applied after generic user-space defaults.
    for overlay in args.overlay:
        if not overlay.is_dir():
            raise RuntimeError("rootfs overlay is not a directory: " + str(overlay))
        shutil.copytree(overlay, root, dirs_exist_ok=True, symlinks=True)
        for directory, directories, files in os.walk(overlay, followlinks=False):
            for name in directories + files:
                source = Path(directory) / name
                relative = "/" + source.relative_to(overlay).as_posix()
                target.run(["/bin/chown", "-h", "0:0", relative])
                if not source.is_symlink():
                    target.run(["/bin/chmod", oct(stat.S_IMODE(source.stat().st_mode))[2:], relative])
    # Apply login policy after product overlays. A locked ('!') ordinary
    # account is rejected by Alpine's non-PAM sshd before certificate auth.
    if args.ssh_ca_key:
        entries, found = [], False
        for entry in shadow.read_text().splitlines():
            fields = entry.split(":")
            if fields[0] == args.default_user:
                fields[1], fields[2] = "*", str(args.epoch // 86400)
                found = True
            elif fields[0] == "root":
                fields[1], fields[2] = "*", str(args.epoch // 86400)
            entries.append(":".join(fields))
        if not found:
            raise RuntimeError("certificate login account is absent from shadow")
        configure_file("etc/shadow", "\n".join(entries) + "\n", 0o640, "root:shadow")
        configure_file("etc/ssh/ssh_ca_key.pub", read_ssh_ca_key(args.ssh_ca_key))
        target.run(["/usr/bin/ssh-keygen", "-l", "-f", "/etc/ssh/ssh_ca_key.pub"], log_output=False)
    if (root / "usr/bin/sudo").is_file():
        if args.ssh_ca_key:
            (root / "etc/sudoers.d/naos").unlink(missing_ok=True)
        else:
            configure_file("etc/sudoers.d/naos", args.default_user + " ALL=(ALL:ALL) ALL\n", 0o440)
    policy = ssh_login_policy(args)
    configure_file("etc/ssh/sshd_config.d/00-naos-login.conf",
                   "".join(f"{option} {value}\n" for option, value in policy.items()))
    if args.ssh_ca_key:
        ssh_config = root / "etc/ssh/sshd_config"
        contents = ssh_config.read_text()
        include = "Include /etc/ssh/sshd_config.d/00-naos-login.conf\n"
        if not contents.startswith(include):
            configure_file("etc/ssh/sshd_config", include + contents)
    configure_file("etc/inittab", configured_inittab((root / "etc/inittab").read_text(),
                   serial_consoles, args.console_shell == "1"))
    services = {"sysinit": ("devfs", "procfs", "sysfs", "dmesg", "mdev"),
                "boot": ("modules", "hwclock", "hostname", "sysctl", "bootmisc", "networking", "seedrng"),
                "default": ("syslog", "crond"),
                "shutdown": ("killprocs", "savecache", "mount-ro")}
    for runlevel, names in services.items():
        for name in names:
            if (root / "etc/init.d" / name).is_file():
                target.run(["/sbin/rc-update", "add", name, runlevel])
    for name, runlevel in extra_services:
        if not (root / "etc/init.d" / name).is_file():
            raise RuntimeError("requested OpenRC service is not installed: " + name)
        target.run(["/sbin/rc-update", "add", name, runlevel])
    if args.console_shell == "0":
        for path in (root / "etc/runlevels").glob("*/*"):
            if path.is_symlink() and re.fullmatch(r"[\w.-]*getty(?:[.-].*)?", path.name):
                path.unlink()
    runlevel_links = ["/" + path.relative_to(root).as_posix()
                      for path in sorted((root / "etc/runlevels").glob("*/*")) if path.is_symlink()]
    if runlevel_links:
        target.run(["/bin/chown", "-h", "0:0", *runlevel_links])
    return {directory.name: sorted(path.name for path in directory.iterdir() if path.is_symlink())
            for directory in sorted((root / "etc/runlevels").iterdir()) if directory.is_dir()}


def read_ownership(path):
    ownership = {}
    for line in path.read_text().splitlines():
        fields = dict(item.split("=", 1) for item in line.split(","))
        ownership[(int(fields["dev"], 16), int(fields["ino"]))] = {
            "uid": int(fields["uid"]), "gid": int(fields["gid"]),
            "mode": int(fields["mode"], 8), "rdev": int(fields.get("rdev", "0"), 16)}
    return ownership


def archive_rootfs(root, destination, epoch, ownership):
    paths = [root]
    for directory, directories, files in os.walk(root, followlinks=False):
        paths.extend(Path(directory) / name for name in directories + files)
    paths = sorted(set(paths), key=lambda path: path.relative_to(root).as_posix())
    with destination.open("wb") as output, gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.GNU_FORMAT) as archive:
            for path in paths:
                name = "." if path == root else "./" + path.relative_to(root).as_posix()
                info = archive.gettarinfo(str(path), arcname=name)
                actual = path.lstat()
                fake = ownership.get((actual.st_dev, actual.st_ino), {})
                info.uid, info.gid = fake.get("uid", 0), fake.get("gid", 0)
                # Linux symlink permission bits are always 0777. A reused
                # inode in fakeroot can carry an irrelevant former file mode;
                # retain the actual symlink mode instead of serializing it.
                info.mode = stat.S_IMODE(actual.st_mode if info.issym() else fake.get("mode", actual.st_mode))
                info.uname = info.gname = ""
                info.mtime = epoch
                # Mounted build devices are empty placeholders after unmount.
                device = {"dev/null": (1, 3), "dev/zero": (1, 5),
                          "dev/random": (1, 8), "dev/urandom": (1, 9)}.get(path.relative_to(root).as_posix())
                if device:
                    info.type, info.size, info.mode = tarfile.CHRTYPE, 0, 0o666
                    info.devmajor, info.devminor = device
                elif not info.issym() and (stat.S_ISCHR(fake.get("mode", 0)) or stat.S_ISBLK(fake.get("mode", 0))):
                    info.type = tarfile.CHRTYPE if stat.S_ISCHR(fake["mode"]) else tarfile.BLKTYPE
                    info.size = 0
                    info.devmajor, info.devminor = os.major(fake["rdev"]), os.minor(fake["rdev"])
                if info.isfile():
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
                else:
                    archive.addfile(info)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("seed", "apk-dir", "output-dir", "repositories", "fakeroot-apk", "ownership-library"):
        parser.add_argument("--" + option, type=Path, required=True)
    parser.add_argument("--name", default="naos-alpine-rootfs-aarch64")
    parser.add_argument("--packages", default="alpine-base ca-certificates")
    parser.add_argument("--services", default="")
    parser.add_argument("--default-user", default="naos")
    parser.add_argument("--default-password", default="")
    parser.add_argument("--ssh-ca-key", type=Path, help="OpenSSH public CA for certificate-only login")
    parser.add_argument("--console-shell", choices=("0", "1"), default="0")
    parser.add_argument("--serial-consoles", default="")
    parser.add_argument("--overlay", type=Path, action="append", default=[])
    parser.add_argument("--apk-key", type=Path, action="append", default=[])
    parser.add_argument("--owner", default="0:0", help="namespace owner; kept for class API compatibility")
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--processors", type=int, default=4)
    parser.add_argument("--tar-only", action="store_true")
    parser.add_argument("--proc-mode", choices=("private", "bind"), default="private",
                        help="create a private proc filesystem or recursively bind the container's existing proc")
    args = parser.parse_args()
    if not args.tar_only:
        parser.error("this helper emits a metadata-preserving tar; BitBake creates SquashFS")
    return args


def main():
    args = parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("run this helper through unshare --user --map-root-user --mount --ipc; private proc also needs --pid --fork --mount-proc")
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, terminate)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.name):
        raise RuntimeError("invalid artifact name")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.default_user) or args.default_user == "root":
        raise RuntimeError("invalid non-root login user")
    if any(character in args.default_password for character in "\n\r:"):
        raise RuntimeError("invalid password characters")
    if args.ssh_ca_key:
        if not args.ssh_ca_key.is_file():
            raise RuntimeError("SSH CA public-key file is missing")
        read_ssh_ca_key(args.ssh_ca_key)
    if not 0 <= args.epoch <= 0xFFFFFFFF:
        raise RuntimeError("invalid source date epoch")
    serial_consoles = []
    for entry in shlex.split(args.serial_consoles):
        fields = entry.split(";")
        if len(fields) != 2 or not fields[0].isdecimal() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", fields[1]):
            raise RuntimeError("invalid SERIAL_CONSOLES entry: " + entry)
        serial_consoles.append((int(fields[0]), fields[1]))
    extra_services = []
    for entry in shlex.split(args.services):
        fields = entry.split(":")
        if len(fields) != 2 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", field) for field in fields):
            raise RuntimeError("invalid service:runlevel entry: " + entry)
        extra_services.append(tuple(fields))
    requested_packages = shlex.split(args.packages)
    if not requested_packages or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.~<>=:@-]*", package) for package in requested_packages):
        raise RuntimeError("invalid requested package list")
    seed = args.seed.resolve(strict=True)
    apk_files = sorted(args.apk_dir.resolve(strict=True).glob("*.apk"))
    if not apk_files:
        raise RuntimeError("no locked APKs were provided")
    validate_seed(seed)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C",
           "TZ": "UTC", "SOURCE_DATE_EPOCH": str(args.epoch)}
    workspace = Path(tempfile.mkdtemp(prefix=".naos-alpine-", dir=output.parent))
    mounts, target = [], None
    try:
        root = workspace / "rootfs"
        root.mkdir()
        run(["tar", "--no-same-owner", "-xpf", seed, "-C", root], env=env)
        helper = root / ".naos-build"
        helper.mkdir()
        for key in args.apk_key:
            shutil.copyfile(key, root / "etc/apk/keys" / key.name)
        target = Target(root, env)
        # Signature verification precedes extraction of the build helper.
        shutil.copyfile(args.fakeroot_apk, helper / "fakeroot.apk")
        target.run(["/sbin/apk", "verify", "/.naos-build/fakeroot.apk"])
        (helper / "helper").mkdir()
        target.run(["/sbin/apk", "extract", "--no-chown", "--destination", "/.naos-build/helper", "/.naos-build/fakeroot.apk"])
        shutil.copyfile(args.ownership_library, helper / "libnaos-chown-compat.so")
        shutil.copyfile(seed, helper / "seed.tar.gz")
        (helper / "packages").mkdir()
        for package in apk_files:
            shutil.copyfile(package, helper / "packages" / package.name)
        # Bind only the required devices; never expose the host's entire /dev.
        for name in ("null", "zero", "random", "urandom"):
            path = root / "dev" / name
            path.touch(exist_ok=True)
            path.chmod(0o666)
            mount_rootfs(["mount", "--bind", "/dev/" + name], path, False, mounts, env)
        proc = root / "proc"
        if args.proc_mode == "bind":
            # Keep the container's PID view and all masked/readonly proc child
            # mounts; a fresh proc mount can be rejected in rootless Docker.
            mount_rootfs(["mount", "--rbind", "/proc"], proc, True, mounts, env)
        else:
            mount_rootfs(["mount", "-t", "proc", "-o", "nosuid,nodev,noexec", "proc"], proc, False, mounts, env)
        target.start()
        # Re-extract with target fakeroot so seed UID/GID values enter the same
        # ownership database as subsequent APK installations and scripts.
        target.run(["/bin/tar", "-xpf", "/.naos-build/seed.tar.gz", "-C", "/"])
        # Re-extraction may restore /etc/apk/keys; add product trust afterward.
        for key in args.apk_key:
            shutil.copyfile(key, root / "etc/apk/keys" / key.name)
        # APK 3 replaces an earlier world constraint when the same package is
        # named again. Put local files last so unversioned profile entries do
        # not replace their locked file/version constraints with a seed version.
        target.run(["/sbin/apk", "add", "--no-network", "--repositories-file", "/dev/null", "--no-cache",
                    *requested_packages, *["/.naos-build/packages/" + path.name for path in apk_files]],
                   reject_apk_errors=True)
        configured_services = configure_rootfs(root, target, args, serial_consoles, extra_services)
        checks = {}
        for name, command in (("machine", ["/bin/busybox", "uname", "-m"]),
                              ("apk_architecture", ["/sbin/apk", "--print-arch"]),
                              ("openrc_version", ["/sbin/openrc", "--version"]),
                              ("login_uid", ["/usr/bin/id", "-u", args.default_user]),
                              ("login_gid", ["/usr/bin/id", "-g", args.default_user]),
                              ("sudo_policy", ["/usr/sbin/visudo", "-c"])):
            checks[name] = target.run(command)
        if checks["machine"] != "aarch64" or checks["apk_architecture"] != "aarch64":
            raise RuntimeError("target execution did not report aarch64")
        ssh_command = ["/usr/sbin/sshd", "-G"]
        if args.ssh_ca_key:
            # Contextual -T checks require a host key. This build-only key is
            # removed with .naos-build before the rootfs is archived.
            test_host_key = "/.naos-build/sshd-policy-test-key"
            target.run(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                        "-f", test_host_key], log_output=False)
            ssh_command = ["/usr/sbin/sshd", "-T", "-h", test_host_key, "-C",
                           "user=" + args.default_user + ",host=localhost,addr=192.0.2.1"]
        ssh = target.run(ssh_command, log_output=False)
        effective = effective_ssh_options(ssh)
        for option, expected in ssh_login_policy(args).items():
            option = option.lower()
            checks["ssh_" + option] = effective.get(option, "")
            if checks["ssh_" + option] != expected:
                raise RuntimeError("SSH login policy was not applied: " + option)
        packages = installed_packages(root)
        versions = {package["name"]: package["version"] for package in packages}
        for path in apk_files:
            match = re.fullmatch(r"(.+)-([0-9][^-]*-r[0-9]+)\.apk", path.name)
            if match and versions.get(match.group(1)) != match.group(2):
                raise RuntimeError("installed version differs from locked APK: " + path.name)
        target.stop()
        ownership = read_ownership(helper / "ownership.state")
        unmount_rootfs(mounts, env)
        shutil.rmtree(helper)
        (root / "var/log/apk.log").unlink(missing_ok=True)
        # Machine identity and SSH host keys must be generated by each device.
        (root / "etc/machine-id").unlink(missing_ok=True)
        (root / "var/lib/dbus/machine-id").unlink(missing_ok=True)
        for path in (root / "etc/ssh").glob("ssh_host_*"):
            path.unlink()
        archive_rootfs(root, output / (args.name + ".tar.gz"), args.epoch, ownership)
        manifest = {"name": args.name, "architecture": "aarch64", "source_date_epoch": args.epoch,
                    "requested_packages": requested_packages,
                    "repositories": args.repositories.read_text().splitlines(),
                    "seed": {"name": seed.name, "sha256": sha256(seed)},
                    "input_apks": [{"name": path.name, "sha256": sha256(path)} for path in apk_files],
                    "packages": packages, "checks": checks, "openrc_services": configured_services,
                    "default_user": args.default_user, "root_password_locked": not bool(args.ssh_ca_key),
                    "root_unix_password_disabled": True,
                    "ssh_authentication": "user-ca" if args.ssh_ca_key else "password" if args.default_password else "publickey",
                    "ssh_ca_key_sha256": sha256(root / "etc/ssh/ssh_ca_key.pub") if args.ssh_ca_key else None,
                    "console_login_enabled": args.console_shell == "1",
                    "serial_consoles": [{"baud": baud, "tty": tty} for baud, tty in serial_consoles],
                    "ownership": "target fakeroot metadata; numeric UID/GID retained",
                    "kernel_modules_included": False}
        write_file(output / (args.name + ".manifest.json"), json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        print("Created " + str(output / (args.name + ".tar.gz")), flush=True)
    finally:
        original_error = sys.exc_info()[0] is not None
        errors = cleanup_rootfs(target, mounts, workspace, env)
        if errors:
            message = "rootfs cleanup failed: " + "; ".join(errors)
            if original_error:
                print("ERROR: " + message, file=sys.stderr)
            else:
                raise RuntimeError(message)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, tarfile.TarError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)

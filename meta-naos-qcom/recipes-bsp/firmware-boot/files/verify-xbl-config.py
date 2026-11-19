"""Check the signed LeMans XBL configuration and its embedded Capsule root."""

from hashlib import sha384
from pathlib import Path
from struct import unpack_from
from subprocess import check_output, run
from sys import argv
from tempfile import TemporaryDirectory

import libfdt
from elftools.elf.elffile import ELFFile


def der_object(data):
    length = data[1]
    if length < 128:
        return data[:2 + length]
    length_size = length & 0x7f
    length_end = 2 + length_size
    return data[:length_end + int.from_bytes(data[2:length_end], "big")]


def read_capsule_roots(segments):
    for segment in segments:
        data = segment.data()
        if data[:4] != b"\xd0\x0d\xfe\xed":
            continue
        size, = unpack_from(">I", data, 4)
        tree = libfdt.Fdt(data[:size])
        path = tree.path_offset("/sw/uefi/uefiplat", quiet=(libfdt.NOTFOUND,))
        if path < 0:
            continue
        prop = tree.getprop(path, "QcCapsuleRootCert", quiet=(libfdt.NOTFOUND,))
        if not isinstance(prop, int):
            size, = unpack_from(">I", prop)
            yield bytes(prop[4:4 + size])


def verify_image(image, capsule_root):
    with image.open("rb") as source:
        segments = list(ELFFile(source).iter_segments())
        if list(read_capsule_roots(segments)) != [capsule_root.read_bytes()]:
            raise ValueError("XBL configuration does not contain the requested Capsule root")

        # Qualcomm marks the MBNv6 hash segment in its ELF program flags.
        hash_segment, = [s for s in segments if s["p_flags"] & 0x07000000 == 0x02000000]
        mbn = hash_segment.data()
        header = unpack_from("<12I", mbn)
        if header[1] != 6 or header[2] or header[3] or header[10]:
            raise ValueError("Expected OEM-only LeMans MBNv6 signature")
        hash_offset = 48 + header[11]
        signed_end = hash_offset + header[5]
        expected_hashes = b"".join(
            bytes(48) if s is hash_segment else sha384(s.data()).digest()
            for s in segments
        )
        if mbn[hash_offset:signed_end] != expected_hashes:
            raise ValueError("XBL configuration segment digests do not match the signed hash table")

        signature_end = signed_end + header[7]
        signature = der_object(mbn[signed_end:signature_end])
        leaf = der_object(mbn[signature_end:signature_end + header[9]])

    with TemporaryDirectory() as temp:
        public_key = Path(temp) / "leaf.pem"
        signature_file = Path(temp) / "signature.der"
        public_key.write_bytes(check_output(["openssl", "x509", "-inform", "DER", "-pubkey", "-noout"], input=leaf))
        signature_file.write_bytes(signature)
        run(
            ["openssl", "dgst", "-sha384", "-verify", str(public_key), "-signature", str(signature_file)],
            input=mbn[:signed_end],
            check=True,
        )


if __name__ == "__main__":
    verify_image(Path(argv[1]), Path(argv[2]))

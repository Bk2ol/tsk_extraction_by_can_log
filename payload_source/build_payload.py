#!/usr/bin/env python3
import argparse
import binascii
import struct

from Crypto.Cipher import AES
from Crypto.Hash import CMAC


LENGTH = 0xFE0
JMP_LOCATION = 0xFD0
PAYLOAD_LOAD_ADDR = 0xFEBF0000


def cmac(to_auth, key):
    cobj = CMAC.new(key, ciphermod=AES)
    cobj.update(to_auth)
    return cobj.digest()


def main():
    parser = argparse.ArgumentParser(description="Build encrypted/CMAC Toyota EPS payload from V850 shellcode.")
    parser.add_argument("shellcode", help="Path to shellcode binary, usually shellcode/main.bin")
    parser.add_argument("-s", "--secret", required=True, help="Payload build secret, 16 bytes hex")
    parser.add_argument("-k", "--key", default="00" * 16, help="DID 0x201 key, 16 bytes hex")
    parser.add_argument("-i", "--iv", default="00" * 16, help="DID 0x202 IV, 16 bytes hex")
    parser.add_argument("-o", "--output", default="payload_dataflash_ff1ff000_ff209000.bin")
    args = parser.parse_args()

    secret = bytes.fromhex(args.secret)
    key = bytes.fromhex(args.key)
    iv = bytes.fromhex(args.iv)
    if len(secret) != 16 or len(key) != 16 or len(iv) != 16:
        raise SystemExit("secret/key/iv must each be 16 bytes")

    payload = open(args.shellcode, "rb").read()
    padding = JMP_LOCATION - len(payload)
    if padding < 0:
        raise SystemExit(f"shellcode too large: {len(payload)} > {JMP_LOCATION}")
    payload += b"\x00" * padding
    payload += struct.pack("<I", PAYLOAD_LOAD_ADDR)
    payload += b"\x00" * (LENGTH - len(payload))
    payload += struct.pack("<I", PAYLOAD_LOAD_ADDR)
    payload += struct.pack("<I", 0xFF0)
    payload += b"\x00" * 4
    crc = binascii.crc32(payload)
    payload += struct.pack("<I", crc ^ 0xFFFFFFFF)
    assert binascii.crc32(payload[:0xFF0]) == 0xFFFFFFFF

    derived_key = AES.new(secret, AES.MODE_ECB).encrypt(key)
    payload += cmac(iv + payload, key=derived_key)
    payload = AES.new(derived_key, AES.MODE_CBC, iv=iv).encrypt(payload)
    open(args.output, "wb").write(payload)
    print(f"[OK] wrote {args.output} ({len(payload)} bytes)")


if __name__ == "__main__":
    main()

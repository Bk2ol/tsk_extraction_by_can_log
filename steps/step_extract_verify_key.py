"""
Step 5: Extract & Verify SecOC Key from dump using CAN oracle.

Vehicle requirement: None (offline computation)
Action: Scans dump for high-entropy 16-byte candidates, verifies with AES-CMAC
        against collected CAN oracle frames (0x0F sync + protected frames).
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DUMP_START = 0xFF200000
MIN_ENTROPY = 3.5
MAX_CANDIDATES = 2000
MAX_SYNC_SAMPLES = 1024
MAX_PROTECTED_PER_ADDR = 250
SYNC_THRESHOLD = 0.99
PROTECTED_THRESHOLD = 0.90


def entropy(buf: bytes) -> float:
    counts = {}
    for b in buf:
        counts[b] = counts.get(b, 0) + 1
    return -sum((c / len(buf)) * math.log2(c / len(buf)) for c in counts.values())


def sha16(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def left_shift_one(buf: bytes) -> bytes:
    out = bytearray(len(buf))
    carry = 0
    for i in range(len(buf) - 1, -1, -1):
        out[i] = ((buf[i] << 1) & 0xFF) | carry
        carry = 1 if (buf[i] & 0x80) else 0
    return bytes(out)


def xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def cmac_subkeys(key: bytes):
    from Crypto.Cipher import AES
    L = AES.new(key, AES.MODE_ECB).encrypt(b"\x00" * 16)
    K1 = bytearray(left_shift_one(L))
    if L[0] & 0x80:
        K1[15] ^= 0x87
    K2 = bytearray(left_shift_one(bytes(K1)))
    if K1[0] & 0x80:
        K2[15] ^= 0x87
    return bytes(K1), bytes(K2)


def aes_cmac(key: bytes, msg: bytes, subkeys=None) -> bytes:
    from Crypto.Cipher import AES
    K1, K2 = subkeys or cmac_subkeys(key)
    n = max(1, (len(msg) + 15) // 16)
    complete = len(msg) > 0 and len(msg) % 16 == 0
    if complete:
        last = xor_bytes(msg[(n - 1) * 16:n * 16], K1)
    else:
        chunk = msg[(n - 1) * 16:] + b"\x80"
        chunk = chunk.ljust(16, b"\x00")
        last = xor_bytes(chunk, K2)
    X = b"\x00" * 16
    cipher = AES.new(key, AES.MODE_ECB)
    for i in range(n - 1):
        X = cipher.encrypt(xor_bytes(X, msg[i * 16:(i + 1) * 16]))
    return cipher.encrypt(xor_bytes(X, last))


def first28(mac: bytes) -> int:
    return ((mac[0] << 20) | (mac[1] << 12) | (mac[2] << 4) | (mac[3] >> 4)) & 0xFFFFFFF


def tail28(data: bytes) -> int:
    return (((data[4] & 0x0F) << 24) | (data[5] << 16) | (data[6] << 8) | data[7]) & 0xFFFFFFF


def sync_input(trip: int, reset: int) -> bytes:
    return struct.pack(">HH", 0x0F, trip) + bytes([((reset << 4) >> 16) & 0xFF, ((reset << 4) >> 8) & 0xFF, (reset << 4) & 0xFF])


def freshness(trip: int, reset: int, msg_cnt: int) -> bytes:
    return struct.pack(">HI", trip & 0xFFFF, ((reset & 0xFFFFF) << 12) | ((msg_cnt & 0xFF) << 4) | ((reset & 3) << 2))


def load_oracle_samples(oracle_path: Path):
    """Load sync and protected samples from CAN oracle file."""
    buses = {0, 2}
    protected_addrs = {0x131, 0x2E4, 0x344}
    sync_samples = []
    protected_samples = []
    sync_by_bus = {}
    sync_seen = set()
    prot_counts = {}

    with oracle_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            addr = int(r["addr"])
            bus = int(r["bus"])
            if bus not in buses:
                continue
            data = bytes.fromhex(r["data"][:16])

            if addr == 0x0F:
                trip = int.from_bytes(data[0:2], "big")
                reset = (data[2] << 12) | (data[3] << 4) | (data[4] >> 4)
                auth = tail28(data)
                sync_by_bus[bus] = (trip, reset, auth)
                k = (bus, trip, reset, auth)
                if k not in sync_seen and len(sync_samples) < MAX_SYNC_SAMPLES:
                    sync_seen.add(k)
                    sync_samples.append({"bus": bus, "trip": trip, "reset": reset, "auth": auth})

            elif addr in protected_addrs:
                if prot_counts.get(addr, 0) >= MAX_PROTECTED_PER_ADDR:
                    continue
                if bus not in sync_by_bus:
                    continue
                trip, reset, _ = sync_by_bus[bus]
                prot_counts[addr] = prot_counts.get(addr, 0) + 1
                protected_samples.append({
                    "addr": addr, "bus": bus,
                    "payload4": data[:4], "flag": data[4] >> 4,
                    "auth": tail28(data), "trip": trip, "reset": reset,
                })

    return sync_samples, protected_samples


def extract_candidates(dump_path: Path):
    """Scan dump for high-entropy 16-byte windows."""
    blob = dump_path.read_bytes()
    out = []
    seen = set()
    for off in range(0, len(blob) - 15):
        key = blob[off:off + 16]
        h = sha16(key)
        if h in seen:
            continue
        e = entropy(key)
        if e < MIN_ENTROPY:
            continue
        seen.add(h)
        out.append({"key": key, "hash": h, "offset": off, "addr": DUMP_START + off, "entropy": e})
    out.sort(key=lambda r: (-r["entropy"], r["offset"] % 4, r["offset"]))
    return out[:MAX_CANDIDATES]


def verify_sync(candidate, samples):
    subkeys = cmac_subkeys(candidate["key"])
    matches = 0
    for s in samples:
        if first28(aes_cmac(candidate["key"], sync_input(s["trip"], s["reset"]), subkeys)) == s["auth"]:
            matches += 1
    return matches, len(samples)


def verify_protected(candidate, samples):
    subkeys = cmac_subkeys(candidate["key"])
    matches = 0
    for s in samples:
        ok = False
        for msg_cnt in range(256):
            flag = ((msg_cnt & 3) << 2) | (s["reset"] & 3)
            if flag != s["flag"]:
                continue
            msg = struct.pack(">H", s["addr"]) + s["payload4"] + freshness(s["trip"], s["reset"], msg_cnt)
            if first28(aes_cmac(candidate["key"], msg, subkeys)) == s["auth"]:
                ok = True
                break
        if ok:
            matches += 1
    return matches, len(samples)


def run(state: dict, setup_dir: Path, auto_yes: bool) -> bool:
    """Extract and verify SecOC key. Returns True if key found."""
    from toyota_dataflash_secoc_setup import mark_step, confirm

    # Check dependencies
    try:
        from Crypto.Cipher import AES
    except ImportError:
        print("[ERROR] pycryptodome not available. Install it or use the venv.")
        mark_step(state, "extract_verify_key", "failed", error="pycryptodome missing")
        return False

    # Find dump
    dump_file = setup_dir / f"dump_{DUMP_START:08x}_{DUMP_START + 0x8000:08x}.bin"
    if not dump_file.exists():
        print(f"[ERROR] Dump file not found: {dump_file}")
        print("        Complete Step 4 (dump_dataflash) first.")
        return False

    dump_size = dump_file.stat().st_size
    if dump_size < 32000:
        print(f"[ERROR] Dump too small ({dump_size} bytes). Need complete 32KB dump.")
        return False

    # Find oracle
    oracle_file = setup_dir / "can_oracle.ndjson"
    if not oracle_file.exists():
        print(f"[ERROR] CAN oracle not found: {oracle_file}")
        print("        Complete Step 1 (collect_can) first.")
        return False

    print(f"[verify] Dump: {dump_file} ({dump_size} bytes)")
    print(f"[verify] Oracle: {oracle_file}")
    print()

    # Load samples
    print("[verify] Loading oracle samples...")
    sync_samples, protected_samples = load_oracle_samples(oracle_file)
    print(f"    Sync samples: {len(sync_samples)}")
    print(f"    Protected samples: {len(protected_samples)}")

    if len(sync_samples) < 10:
        print("[WARNING] Very few sync samples. Results may be unreliable.")

    # Extract candidates
    print("[verify] Scanning dump for candidates...")
    candidates = extract_candidates(dump_file)
    print(f"    Candidates: {len(candidates)}")
    print()

    if not candidates:
        print("[ERROR] No candidates found in dump.")
        mark_step(state, "extract_verify_key", "failed", error="no candidates")
        return False

    # Verify
    print("[verify] Verifying candidates against sync oracle...")
    start = time.time()
    ranked = []
    for i, c in enumerate(candidates):
        sm, sc = verify_sync(c, sync_samples)
        ranked.append((sm, sc, c))
        if (i + 1) % 100 == 0:
            print(f"    checked {i + 1}/{len(candidates)}...")

    ranked.sort(key=lambda x: (-x[0], -x[2]["entropy"], x[2]["offset"]))
    elapsed = time.time() - start
    print(f"    Done in {elapsed:.1f}s")
    print()

    best_sync_m, best_sync_c, best = ranked[0]
    print(f"[verify] Best sync match: {best_sync_m}/{best_sync_c}")
    print(f"    Hash: {best['hash']}")
    print(f"    Address: 0x{best['addr']:08x} (offset {best['offset']})")
    print(f"    Entropy: {best['entropy']:.4f}")

    # Protected frame verification
    print("\n[verify] Verifying against protected frames...")
    pm, pc = verify_protected(best, protected_samples)
    print(f"    Protected match: {pm}/{pc}")

    # Decision
    sync_rate = best_sync_m / max(1, best_sync_c)
    prot_rate = pm / max(1, pc)
    accepted = (best_sync_c > 0 and sync_rate >= SYNC_THRESHOLD and
                pc > 0 and prot_rate >= PROTECTED_THRESHOLD)

    print()
    if accepted:
        print("╔══════════════════════════════════════════════════════════╗")
        print("║  ✓ SecOC Key FOUND and VERIFIED                         ║")
        print("╠══════════════════════════════════════════════════════════╣")
        print(f"║  Hash:      {best['hash']:<42}║")
        print(f"║  Address:   0x{best['addr']:08x}{'':>34}║")
        print(f"║  Sync:      {best_sync_m}/{best_sync_c} ({sync_rate*100:.1f}%){'':>28}║")
        print(f"║  Protected: {pm}/{pc} ({prot_rate*100:.1f}%){'':>28}║")
        print("╚══════════════════════════════════════════════════════════╝")
        print()

        # Write and display key
        key_file = setup_dir / "SecOCKey.hex"
        key_hex = best["key"].hex()
        key_file.write_text(key_hex + "\n", encoding="utf-8")
        import os
        os.chmod(key_file, 0o600)
        print(f"[verify] SecOCKey: {key_hex}")
        print(f"[verify] Saved to: {key_file}")

        mark_step(state, "extract_verify_key", "complete",
                  key_sha256_16=best["hash"],
                  key_addr=f"0x{best['addr']:08x}",
                  sync_rate=f"{best_sync_m}/{best_sync_c}",
                  protected_rate=f"{pm}/{pc}",
                  key_file=str(key_file))
        return True
    else:
        print("[FAIL] No candidate passed both sync and protected verification.")
        print(f"    Best sync: {best_sync_m}/{best_sync_c} ({sync_rate*100:.1f}%)")
        print(f"    Best prot: {pm}/{pc} ({prot_rate*100:.1f}%)")
        print()
        print("    Possible causes:")
        print("    - Not enough CAN oracle frames (re-run Step 1 in READY mode)")
        print("    - Dump incomplete or corrupted")
        print("    - Key not in expected memory range")

        mark_step(state, "extract_verify_key", "failed",
                  best_hash=best["hash"],
                  sync_rate=f"{best_sync_m}/{best_sync_c}",
                  protected_rate=f"{pm}/{pc}")
        return False

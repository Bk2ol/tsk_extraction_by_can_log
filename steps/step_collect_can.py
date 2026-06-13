"""
Step 1: Collect CAN frames for oracle verification and fingerprinting.

Vehicle requirement: READY mode (hybrid system on / engine running)
Duration: ~60 seconds by default

Collects frames on bus 0 and 2:
  - 0x0F  (sync/freshness counter with 28-bit MAC)
  - 0x131 (protected frame)
  - 0x2E4 (protected frame)
  - 0x344 (protected frame)
  - All other frames (for fingerprint analysis)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Import mark_step from parent — will be called by main
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


ORACLE_ADDRS = {0x0F, 0x131, 0x2E4, 0x344}
ORACLE_BUSES = {0, 2}
CAN_SECONDS = 60.0


def run(state: dict, setup_dir: Path, auto_yes: bool) -> bool:
    """Collect CAN frames. Returns True if complete."""
    from tss3_setup import mark_step, confirm

    can_log = setup_dir / "can_oracle.ndjson"
    fp_log = setup_dir / "can_fingerprint.ndjson"

    print(f"[collect_can] Will collect CAN frames for {CAN_SECONDS}s")
    print(f"[collect_can] Oracle output: {can_log}")
    print(f"[collect_can] Fingerprint output: {fp_log}")
    print()

    # Stop openpilot/boardd to release Panda USB
    import subprocess
    print("[collect_can] Stopping openpilot/boardd...")
    for cmd in [["sudo", "systemctl", "stop", "comma"], ["pkill", "-f", "boardd"]]:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
    time.sleep(1.5)

    # Try to import panda
    try:
        from panda import Panda
    except ImportError:
        print("[ERROR] Cannot import panda. Make sure you're on the comma device.")
        print("        Try: PYTHONPATH=/data/openpilot python3 tss3_setup.py")
        mark_step(state, "collect_can", "failed", error="panda import failed")
        return False

    # Connect to panda
    try:
        panda = Panda()
        panda.set_safety_mode(3)  # ELM327
        print(f"[collect_can] Connected to Panda")
    except Exception as e:
        print(f"[ERROR] Cannot connect to Panda: {e}")
        mark_step(state, "collect_can", "failed", error=str(e))
        return False

    # Collect frames
    oracle_counts = {}
    fp_counts = {}
    start = time.time()

    print(f"[collect_can] Collecting... (Ctrl+C to stop early)")
    print()

    try:
        with can_log.open("w", encoding="utf-8") as oracle_f, \
             fp_log.open("w", encoding="utf-8") as fp_f:

            while time.time() - start < CAN_SECONDS:
                frames = panda.can_recv()
                if not frames:
                    time.sleep(0.005)
                    continue

                ts_ms = time.time() * 1000.0
                for addr, data, bus in frames:
                    row = {
                        "addr": int(addr),
                        "bus": int(bus),
                        "ts_ms": ts_ms,
                        "data": bytes(data).hex(),
                    }

                    # Oracle frames (for SecOC verification)
                    if int(bus) in ORACLE_BUSES and int(addr) in ORACLE_ADDRS:
                        oracle_f.write(json.dumps(row, sort_keys=True) + "\n")
                        k = f"{bus}:0x{addr:x}"
                        oracle_counts[k] = oracle_counts.get(k, 0) + 1

                    # All frames (for fingerprinting)
                    fp_f.write(json.dumps(row, sort_keys=True) + "\n")
                    fk = f"0x{addr:x}"
                    fp_counts[fk] = fp_counts.get(fk, 0) + 1

                elapsed = time.time() - start
                if int(elapsed) % 10 == 0 and int(elapsed) > 0:
                    total_oracle = sum(oracle_counts.values())
                    if total_oracle and int(elapsed) % 10 == 0:
                        pass  # progress printed below

            # Print every 10 seconds would be noisy; print final summary
    except KeyboardInterrupt:
        print("\n[collect_can] Stopped early by user")

    elapsed = time.time() - start
    total_oracle = sum(oracle_counts.values())
    total_fp = sum(fp_counts.values())

    print(f"\n[collect_can] Done! Elapsed: {elapsed:.1f}s")
    print(f"[collect_can] Oracle frames: {total_oracle}")
    for k, v in sorted(oracle_counts.items()):
        print(f"    {k}: {v}")
    print(f"[collect_can] Total CAN frames: {total_fp}")
    print(f"[collect_can] Unique addresses: {len(fp_counts)}")

    # Check if we have enough
    sync_count = sum(v for k, v in oracle_counts.items() if "0xf" in k)
    if sync_count < 50:
        print()
        print("[WARNING] Very few sync (0x0F) frames collected.")
        print("          Make sure the car is in READY mode (hybrid on).")
        print("          You may want to re-run this step with --redo collect_can")

    mark_step(state, "collect_can", "complete",
              can_oracle=str(can_log),
              can_fingerprint=str(fp_log),
              oracle_counts=oracle_counts,
              total_frames=total_fp,
              unique_addrs=len(fp_counts),
              elapsed_sec=round(elapsed, 1))

    print(f"\n[collect_can] ✓ Complete")
    return True

"""
Step 3: EPS Probe — Read-only UDS probe to confirm EPS firmware.

Vehicle requirement: IG-ON only (not READY)
Action: Reads DID F181 to confirm EPS part number (8965B4514000).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_DIDS = [0xF180, 0xF181, 0xF182, 0xF187, 0xF188, 0xF189, 0xF18A]
TX_ADDR = 0x7A1
RX_ADDR = 0x7A9
BUS = 0


def run(state: dict, setup_dir: Path, auto_yes: bool) -> bool:
    """Run EPS probe. Returns True if complete."""
    from tss3_setup import mark_step, confirm

    print("[eps_probe] Read-only EPS UDS probe")
    print(f"[eps_probe] TX: 0x{TX_ADDR:x}, RX: 0x{RX_ADDR:x}, Bus: {BUS}")
    print()

    # Stop openpilot
    print("[eps_probe] Stopping openpilot/boardd...")
    for cmd in [["sudo", "systemctl", "stop", "comma"], ["pkill", "-f", "boardd"]]:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
    time.sleep(1.0)

    # Import dependencies
    try:
        from panda import Panda
    except ImportError:
        print("[ERROR] Cannot import panda.")
        mark_step(state, "eps_probe", "failed", error="panda import failed")
        return False

    # Find UDS module
    uds_mod = None
    for modname in ("opendbc.car.uds", "panda.python.uds", "panda.uds"):
        try:
            uds_mod = __import__(modname, fromlist=["UdsClient"])
            break
        except Exception:
            continue
    if uds_mod is None:
        print("[ERROR] Cannot import UDS module.")
        mark_step(state, "eps_probe", "failed", error="UDS module not found")
        return False

    # Connect
    try:
        panda = Panda()
        panda.set_safety_mode(3)
        print(f"[eps_probe] Connected to Panda")
    except Exception as e:
        print(f"[ERROR] Cannot connect to Panda: {e}")
        mark_step(state, "eps_probe", "failed", error=str(e))
        return False

    # Create UDS client
    uds = None
    for kwargs in [{"timeout": 0.5, "debug": False}, {"timeout": 0.5}, {}]:
        try:
            uds = uds_mod.UdsClient(panda, TX_ADDR, RX_ADDR, BUS, **kwargs)
            break
        except TypeError:
            continue
    if uds is None:
        print("[ERROR] Cannot create UDS client")
        mark_step(state, "eps_probe", "failed", error="UDS client creation failed")
        return False

    # Read DIDs
    dids = {}
    firmware = None
    print("[eps_probe] Reading DIDs...")
    for did in DEFAULT_DIDS:
        try:
            data = uds.read_data_by_identifier(did)
            hex_str = data.hex()
            ascii_str = data.decode("latin1", "replace")
            dids[hex(did)] = {"hex": hex_str, "ascii": ascii_str}
            print(f"    DID {hex(did)}: {ascii_str.strip()}")
            if did == 0xF181 and b"8965B" in data:
                firmware = ascii_str.strip().replace("\x01", "").replace("\x00", "")
        except Exception as e:
            dids[hex(did)] = {"error": str(e)}
            print(f"    DID {hex(did)}: ERROR - {e}")

    if firmware:
        print(f"\n[eps_probe] ✓ EPS firmware: {firmware}")
    else:
        print("\n[WARNING] Could not confirm EPS firmware from DID 0xF181")

    # Save probe result
    probe_file = setup_dir / "eps_probe.json"
    probe_file.write_text(json.dumps({
        "tx": hex(TX_ADDR), "rx": hex(RX_ADDR), "bus": BUS,
        "dids": dids, "firmware": firmware,
    }, indent=2), encoding="utf-8")

    mark_step(state, "eps_probe", "complete",
              firmware=firmware,
              dids=dids,
              probe_file=str(probe_file))

    print(f"[eps_probe] ✓ Complete")
    return True

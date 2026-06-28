"""
Step 2: Fingerprint Patch — Ensure openpilot recognizes the car.

Vehicle requirement: Any (can be off)
Action: Patches missing Sienna firmware markers into TOYOTA_SIENNA_4TH_GEN.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TARGET_CAR = "CAR.TOYOTA_SIENNA_4TH_GEN"

# Default — will be overridden by probed firmware if available
DEFAULT_FW = [
    {
        "name": "eps_8965B4514000",
        "ecu_expr": "(Ecu.eps, 0x7a1, None)",
        "ecu_re": r"\(\s*Ecu\.eps\s*,\s*0x7a1\s*,\s*None\s*\)",
        "marker_plain": "8965B4514000",
        "marker_literal": 'b"\\x018965B4514000\\x00\\x00\\x00\\x00"',
    },
]


def get_required_fw(state: dict) -> list:
    """Get firmware markers from EPS probe result, or fall back to defaults."""
    eps_probe = state.get("steps", {}).get("eps_probe", {})
    firmware = eps_probe.get("firmware")
    if firmware and firmware.strip():
        fw_clean = firmware.strip().replace("\x01", "").replace("\x00", "")
        if len(fw_clean) >= 6:
            return [{
                "name": f"eps_{fw_clean}",
                "ecu_expr": "(Ecu.eps, 0x7a1, None)",
                "ecu_re": r"\(\s*Ecu\.eps\s*,\s*0x7a1\s*,\s*None\s*\)",
                "marker_plain": fw_clean,
                "marker_literal": f'b"\\x01{fw_clean}\\x00\\x00\\x00\\x00"',
            }]
    return DEFAULT_FW

TARGET_FILES = [
    "opendbc_repo/opendbc/car/toyota/fingerprints.py",
    "opendbc/car/toyota/fingerprints.py",
    "opendbc_repo/opendbc/car/toyota/values.py",
    "opendbc/car/toyota/values.py",
]

OPENPILOT_DIR = Path("/data/openpilot")


def check_fingerprint_status(required_fw: list) -> dict:
    """Check if fingerprint already has required markers."""
    result = {"all_present": True, "details": []}
    for rel in TARGET_FILES:
        p = OPENPILOT_DIR / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if TARGET_CAR not in text:
            continue
        for fw in required_fw:
            present = fw["marker_plain"] in text
            result["details"].append({
                "file": rel,
                "marker": fw["name"],
                "present": present,
            })
            if not present:
                result["all_present"] = False
    return result


def run(state: dict, setup_dir: Path, auto_yes: bool) -> bool:
    """Check and patch fingerprint. Returns True if complete."""
    from toyota_dataflash_secoc_setup import mark_step, confirm

    print("[fingerprint] Checking openpilot fingerprint...")
    print(f"[fingerprint] Target: {TARGET_CAR}")
    print(f"[fingerprint] Openpilot: {OPENPILOT_DIR}")
    print()

    if not OPENPILOT_DIR.exists():
        print(f"[ERROR] Openpilot directory not found: {OPENPILOT_DIR}")
        mark_step(state, "fingerprint_patch", "failed", error="openpilot not found")
        return False

    # Get firmware markers dynamically from EPS probe result
    REQUIRED_FW = get_required_fw(state)
    print(f"[fingerprint] EPS firmware to check: {REQUIRED_FW[0]['marker_plain']}")
    
    status = check_fingerprint_status(REQUIRED_FW)

    if status["all_present"]:
        print("[fingerprint] ✓ All required markers already present!")
        for d in status["details"]:
            print(f"    {d['marker']}: {'✓' if d['present'] else '✗'} in {d['file']}")
        mark_step(state, "fingerprint_patch", "complete", patched=False, already_present=True)
        return True

    print("[fingerprint] Missing markers detected:")
    for d in status["details"]:
        if not d["present"]:
            print(f"    ✗ {d['marker']} missing in {d['file']}")

    print()
    if not confirm("Apply fingerprint patch?", auto_yes):
        print("[fingerprint] Skipped. Run --redo fingerprint_patch when ready.")
        return False

    # Apply patch inline — add missing markers to fingerprint files
    try:
        patched_any = False
        for rel in TARGET_FILES:
            p = OPENPILOT_DIR / rel
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if TARGET_CAR not in text:
                continue
            for fw in REQUIRED_FW:
                if fw["marker_plain"] in text:
                    continue
                # Find the ECU entry and add the marker
                ecu_match = re.search(fw["ecu_re"] + r"\s*:\s*\[", text)
                if ecu_match:
                    insert_pos = ecu_match.end()
                    insert_text = f"\n      {fw['marker_literal']},"
                    text = text[:insert_pos] + insert_text + text[insert_pos:]
                    patched_any = True
                    print(f"    ✓ Added {fw['name']} to {rel}")
                else:
                    print(f"    ⚠ ECU entry not found for {fw['name']} in {rel}")
                    print(f"      You may need to manually add {fw['marker_literal']}")
            if patched_any:
                import shutil
                backup = p.with_suffix('.py.bak')
                shutil.copy2(p, backup)
                p.write_text(text, encoding="utf-8")

        # Clear fingerprint cache
        cache_params = ["CarParams", "CarParamsCache", "CarParamsPersistent",
                       "FirmwareQueryDone", "CarFingerprint"]
        params_dir = Path("/data/params/d")
        for name in cache_params:
            cache_file = params_dir / name
            if cache_file.exists():
                cache_file.unlink()
                print(f"    Cleared cache: {name}")

    except Exception as e:
        print(f"[ERROR] Patch failed: {e}")
        mark_step(state, "fingerprint_patch", "failed", error=str(e))
        return False

    # Verify after patch
    post_status = check_fingerprint_status(REQUIRED_FW)
    if post_status["all_present"]:
        print("\n[fingerprint] ✓ Patch applied successfully!")
        print()
        print("  ⚠️  ACTION REQUIRED: Please reboot the comma device:")
        print("     sudo reboot")
        print()
        print("  After reboot, re-run: python3 toyota_dataflash_secoc_setup.py")
        mark_step(state, "fingerprint_patch", "complete", patched=True, needs_reboot=True)
        return False  # Return False to pause — user needs to reboot
    else:
        print("[WARNING] Patch applied but verification failed. Check manually.")
        mark_step(state, "fingerprint_patch", "complete", patched=True, verify_failed=True)
        return True

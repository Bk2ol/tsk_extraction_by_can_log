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

REQUIRED_FW = [
    {
        "name": "eps_8965B4514000",
        "ecu_expr": "(Ecu.eps, 0x7a1, None)",
        "ecu_re": r"\(\s*Ecu\.eps\s*,\s*0x7a1\s*,\s*None\s*\)",
        "marker_plain": "8965B4514000",
        "marker_literal": 'b"\\x018965B4514000\\x00\\x00\\x00\\x00"',
    },
]

TARGET_FILES = [
    "opendbc_repo/opendbc/car/toyota/fingerprints.py",
    "opendbc/car/toyota/fingerprints.py",
    "opendbc_repo/opendbc/car/toyota/values.py",
    "opendbc/car/toyota/values.py",
]

OPENPILOT_DIR = Path("/data/openpilot")


def check_fingerprint_status() -> dict:
    """Check if fingerprint already has required markers."""
    result = {"all_present": True, "details": []}
    for rel in TARGET_FILES:
        p = OPENPILOT_DIR / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if TARGET_CAR not in text:
            continue
        for fw in REQUIRED_FW:
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

    status = check_fingerprint_status()

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

    # Apply patch using the existing patch script logic
    try:
        # Try to use the existing patch script
        patch_script = Path(__file__).resolve().parent.parent.parent / "patch_tss3_missing_fw_to_4th_gen.py"
        if patch_script.exists():
            print(f"[fingerprint] Running: python3 {patch_script} --apply --clear-cache")
            result = subprocess.run(
                ["python3", str(patch_script), "--apply", "--clear-cache",
                 "--openpilot-dir", str(OPENPILOT_DIR)],
                capture_output=True, text=True, timeout=30
            )
            print(result.stdout)
            if result.returncode != 0:
                print(f"[WARNING] Patch script returned {result.returncode}")
                if result.stderr:
                    print(result.stderr)
        else:
            print(f"[fingerprint] Patch script not found at {patch_script}")
            print("[fingerprint] Please run patch_tss3_missing_fw_to_4th_gen.py manually")
            mark_step(state, "fingerprint_patch", "failed", error="patch script not found")
            return False

    except Exception as e:
        print(f"[ERROR] Patch failed: {e}")
        mark_step(state, "fingerprint_patch", "failed", error=str(e))
        return False

    # Verify after patch
    post_status = check_fingerprint_status()
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

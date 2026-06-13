#!/usr/bin/env python3
"""
TSS3 SecOC Key Setup Wizard — Single controller script.

Usage on comma device:
    python3 tss3_setup.py          # Interactive wizard, resumes from last state
    python3 tss3_setup.py --status # Show current progress
    python3 tss3_setup.py --step collect_can   # Jump to specific step
    python3 tss3_setup.py --redo collect_can   # Force re-run a step
    python3 tss3_setup.py --yes    # Auto-confirm all prompts

Workflow:
    1. collect_can        — Collect CAN frames (READY mode, ~60s)
    2. fingerprint_patch  — Analyze + patch openpilot fingerprint
    3. eps_probe          — Read-only EPS UDS probe (IG-ON)
    4. dump_dataflash     — Upload payload, dump EPS memory (IG-ON, may need 2 runs)
    5. extract_verify_key — Scan dump + AES-CMAC verify against CAN oracle
    6. install_key        — Write SecOCKey to params
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SETUP_DIR = Path("/data/tss3_setup")
STATE_FILE = SETUP_DIR / "state.json"
VERSION = "20260611"

STEPS = [
    "collect_can",
    "fingerprint_patch",
    "eps_probe",
    "dump_dataflash",
    "extract_verify_key",
    "install_key",
]

STEP_LABELS = {
    "collect_can": "Collect CAN Log (READY mode)",
    "fingerprint_patch": "Fingerprint Patch",
    "eps_probe": "EPS Probe (IG-ON)",
    "dump_dataflash": "DataFlash Dump (IG-ON)",
    "extract_verify_key": "SecOC Key Extraction & Verification",
    "install_key": "Install SecOC Key",
}

STEP_VEHICLE_REQ = {
    "collect_can": "Vehicle in READY mode (engine/hybrid on)",
    "fingerprint_patch": "Any (can be off, SSH only)",
    "eps_probe": "IG-ON only (press start WITHOUT brake, not READY)",
    "dump_dataflash": "IG-ON only (press start WITHOUT brake, not READY)",
    "extract_verify_key": "Any (offline computation)",
    "install_key": "Any (then reboot after)",
}


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "version": VERSION,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current_step": STEPS[0],
        "steps": {},
    }


def save_state(state: dict):
    SETUP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def step_status(state: dict, step: str) -> str:
    return state.get("steps", {}).get(step, {}).get("status", "pending")


def mark_step(state: dict, step: str, status: str, **extra):
    if "steps" not in state:
        state["steps"] = {}
    if step not in state["steps"]:
        state["steps"][step] = {}
    state["steps"][step]["status"] = status
    state["steps"][step]["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["steps"][step].update(extra)
    # Advance current_step
    if status == "complete":
        idx = STEPS.index(step)
        if idx + 1 < len(STEPS):
            state["current_step"] = STEPS[idx + 1]
        else:
            state["current_step"] = "done"
    save_state(state)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_banner(state: dict):
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  TSS3 SecOC Key Setup Wizard                            ║")
    print("╠══════════════════════════════════════════════════════════╣")
    for i, step in enumerate(STEPS, 1):
        s = step_status(state, step)
        if s == "complete":
            mark = "✓"
        elif s == "primed":
            mark = "⚡"
        elif s == "failed":
            mark = "✗"
        else:
            mark = " "
        label = STEP_LABELS[step]
        print(f"║  [{mark}] Step {i}: {label:<45}║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()
    cur = state.get("current_step", STEPS[0])
    if cur == "done":
        print("  ✅ All steps complete! SecOCKey is installed.")
    else:
        print(f"  Current: Step {STEPS.index(cur) + 1} — {STEP_LABELS[cur]}")
        print(f"  Vehicle: {STEP_VEHICLE_REQ[cur]}")
    print()


def confirm(msg: str, auto_yes: bool = False) -> bool:
    if auto_yes:
        print(f"{msg} [Y/n] > y (auto)")
        return True
    try:
        resp = input(f"{msg} [Y/n] > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return False
    return resp in ("", "y", "yes")


# ---------------------------------------------------------------------------
# Step Implementations (import from submodules)
# ---------------------------------------------------------------------------

def run_step(step: str, state: dict, auto_yes: bool = False) -> bool:
    """Run a step. Returns True if completed, False if needs user action."""
    print(f"\n{'='*60}")
    print(f"  STEP: {STEP_LABELS[step]}")
    print(f"  Vehicle: {STEP_VEHICLE_REQ[step]}")
    print(f"{'='*60}\n")

    if not confirm(f"Proceed with {STEP_LABELS[step]}?", auto_yes):
        return False

    if step == "collect_can":
        from steps import step_collect_can
        return step_collect_can.run(state, SETUP_DIR, auto_yes)

    elif step == "fingerprint_patch":
        from steps import step_fingerprint_patch
        return step_fingerprint_patch.run(state, SETUP_DIR, auto_yes)

    elif step == "eps_probe":
        from steps import step_eps_probe
        return step_eps_probe.run(state, SETUP_DIR, auto_yes)

    elif step == "dump_dataflash":
        from steps import step_dump_dataflash
        return step_dump_dataflash.run(state, SETUP_DIR, auto_yes)

    elif step == "extract_verify_key":
        from steps import step_extract_verify_key
        return step_extract_verify_key.run(state, SETUP_DIR, auto_yes)

    elif step == "install_key":
        from steps import step_install_key
        return step_install_key.run(state, SETUP_DIR, auto_yes)

    else:
        print(f"[ERROR] Unknown step: {step}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def find_venv_python() -> Optional[str]:
    """Find the comma device venv python with pycryptodome."""
    candidates = [
        "/usr/local/venv/bin/python3",
        "/data/openpilot/.venv/bin/python3",
        "/opt/venv/bin/python3",
        "/usr/local/bin/python3",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def check_dependencies():
    """Check that required dependencies are available."""
    issues = []

    # Check panda
    try:
        from panda import Panda
    except ImportError:
        issues.append(("panda", "Set PYTHONPATH=/data/openpilot or install panda package"))

    # Check pycryptodome
    try:
        from Crypto.Cipher import AES
    except ImportError:
        issues.append(("pycryptodome (Crypto.Cipher.AES)",
                      "Use venv python or: pip install pycryptodome"))

    # Check UDS module
    uds_found = False
    for modname in ("opendbc.car.uds", "panda.python.uds", "panda.uds"):
        try:
            __import__(modname, fromlist=["UdsClient"])
            uds_found = True
            break
        except Exception:
            continue
    if not uds_found:
        issues.append(("UDS module (opendbc.car.uds)",
                      "Set PYTHONPATH=/data/openpilot"))

    if issues:
        print("\n⚠️  MISSING DEPENDENCIES:")
        print()
        for name, fix in issues:
            print(f"  ✗ {name}")
            print(f"    Fix: {fix}")
        print()
        # Find best python
        venv_py = find_venv_python()
        if venv_py:
            print(f"  Recommended run command:")
            print(f"    PYTHONPATH=/data/openpilot {venv_py} tss3_setup.py")
        else:
            print("  Recommended run command:")
            print("    PYTHONPATH=/data/openpilot python3 tss3_setup.py")
            print("  (You may also need: pip install pycryptodome)")
        print()
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="TSS3 SecOC Key Setup Wizard")
    ap.add_argument("--status", action="store_true", help="Show current progress and exit")
    ap.add_argument("--step", choices=STEPS, help="Jump to a specific step")
    ap.add_argument("--redo", choices=STEPS, help="Force re-run a completed step")
    ap.add_argument("--yes", action="store_true", help="Auto-confirm all prompts")
    ap.add_argument("--setup-dir", default="/data/tss3_setup", help="Override setup directory")
    args = ap.parse_args()

    global SETUP_DIR, STATE_FILE
    SETUP_DIR = Path(args.setup_dir)
    STATE_FILE = SETUP_DIR / "state.json"
    SETUP_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()

    if args.status:
        print_banner(state)
        return 0

    if args.redo:
        mark_step(state, args.redo, "pending")
        state["current_step"] = args.redo
        save_state(state)

    if args.step:
        state["current_step"] = args.step
        save_state(state)

    # Check dependencies before running
    if not check_dependencies():
        return 1

    print_banner(state)

    cur = state.get("current_step", STEPS[0])
    if cur == "done":
        print("All steps complete. Nothing to do.")
        print("Use --redo <step> to re-run a specific step.")
        return 0

    # Run steps sequentially from current
    idx = STEPS.index(cur)
    for step in STEPS[idx:]:
        s = step_status(state, step)
        if s == "complete":
            continue

        completed = run_step(step, state, args.yes)
        if not completed:
            # Step needs user action (reboot, power cycle, etc.)
            print_banner(state)
            print("  ⏸️  Paused. Re-run this script after completing the required action.")
            return 1

    print_banner(state)
    print("  🎉 Setup complete! Reboot and test LKAS/LTA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

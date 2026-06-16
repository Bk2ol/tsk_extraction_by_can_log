"""
Step 6: Install SecOC Key — Write verified key to openpilot params.

Vehicle requirement: Any (then reboot after)
Action: Writes SecOCKey to /data/params/d/SecOCKey and /cache/params/SecOCKey.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

INSTALL_TARGETS = [
    Path("/data/params/d/SecOCKey"),
    Path("/cache/params/SecOCKey"),
]


def run(state: dict, setup_dir: Path, auto_yes: bool) -> bool:
    """Install SecOC key. Returns True if complete."""
    from toyota_dataflash_secoc_setup import mark_step, confirm

    key_file = setup_dir / "SecOCKey.hex"
    if not key_file.exists():
        print("[ERROR] SecOCKey.hex not found. Complete Step 5 first.")
        return False

    key_hex = key_file.read_text(encoding="utf-8").strip()
    if len(key_hex) != 32:
        print(f"[ERROR] Invalid key length: {len(key_hex)} chars (expected 32 hex chars)")
        return False

    key_hash = hashlib.sha256(bytes.fromhex(key_hex)).hexdigest()[:16]
    print(f"[install] Key hash: {key_hash}")
    print(f"[install] Key length: 16 bytes (32 hex chars)")
    print(f"[install] Targets:")
    for t in INSTALL_TARGETS:
        exists = t.exists()
        print(f"    {t} {'(exists)' if exists else '(new)'}")
    print()

    if not confirm("Install SecOCKey to params?", auto_yes):
        print("[install] Skipped.")
        return False

    # Backup existing keys
    backup_dir = setup_dir / "key_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for target in INSTALL_TARGETS:
        if target.exists():
            old = target.read_text(encoding="utf-8", errors="replace").strip()
            if old:
                old_hash = hashlib.sha256(old.encode()).hexdigest()[:16]
                backup = backup_dir / f"{target.name}.{int(time.time())}.bak"
                backup.write_text(old + "\n", encoding="utf-8")
                print(f"[install] Backed up {target.name} (old hash: {old_hash})")

    # Write key
    import subprocess
    for target in INSTALL_TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(key_hex + "\n", encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            os.chmod(target, 0o600)
            print(f"[install] ✓ Wrote {target}")
        except PermissionError:
            # /cache/params/ may need sudo — use temp file to avoid key in ps
            print(f"[install] Permission denied, retrying with sudo...")
            tmp_sudo = setup_dir / ".secoc_tmp"
            tmp_sudo.write_text(key_hex + "\n", encoding="utf-8")
            os.chmod(tmp_sudo, 0o600)
            subprocess.run(["sudo", "cp", str(tmp_sudo), str(target)], check=True, timeout=5)
            subprocess.run(["sudo", "chmod", "600", str(target)], check=True, timeout=5)
            tmp_sudo.unlink()
            print(f"[install] ✓ Wrote {target} (via sudo)")

    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  ✓ SecOCKey Installed!                                   ║")
    print("╠══════════════════════════════════════════════════════════╣")
    print("║  Next steps:                                            ║")
    print("║    1. sudo reboot                                       ║")
    print("║    2. Test LKAS/LTA engage                              ║")
    print("║    3. Test ACC engage                                   ║")
    print("╚══════════════════════════════════════════════════════════╝")

    mark_step(state, "install_key", "complete",
              key_hash=key_hash,
              targets=[str(t) for t in INSTALL_TARGETS],
              backup_dir=str(backup_dir))
    return True

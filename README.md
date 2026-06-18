# Toyota Dataflash SecOC Setup

Automated tool for Toyota TSK / ECU Security Key / SecOC key extraction and installation on comma/openpilot devices using an EPS dataflash workflow.

This repository is intentionally named around the method, not a specific model or Toyota Safety Sense generation. Toyota's official 2024 Sienna materials list the vehicle with Toyota Safety Sense 2.0, not TSS3, and TSS versioning is not a reliable proxy for TSK / ECU Security Key behavior.

The currently validated target for this workflow is a 4th-gen Toyota Sienna with EPS part `8965B4514000`.

## Step-by-Step Instructions

### Prerequisites

- A comma device (comma 3/3X) with openpilot/sunnypilot installed
- SSH access to the comma device
- Currently validated target: 4th-gen Toyota Sienna with EPS part `8965B4514000`
- The `payload_dataflash_ff200000_ff208000.bin` payload file (included in this repo at the root)

### 1. Get the Code on Comma Device

SSH into your comma device and clone the repo:

```bash
ssh comma@<COMMA_IP>
cd /data
git clone https://github.com/Bk2ol/tsk_extraction_by_can_log.git toyota_dataflash_secoc_setup
```

### 2. Run the Wizard

```bash
cd /data/toyota_dataflash_secoc_setup
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 toyota_dataflash_secoc_setup.py
```

> **Note:** If you get dependency errors, the script will tell you the correct Python path. Common alternatives:
> - `/data/openpilot/.venv/bin/python3`
> - `/usr/local/bin/python3` (may need `pip install pycryptodome`)

### 3. Follow the Prompts

The wizard guides you through 6 steps. Each step tells you what vehicle state is needed:

```
╔══════════════════════════════════════════════════════════╗
║  Toyota Dataflash SecOC Setup                           ║
╠══════════════════════════════════════════════════════════╣
║  [ ] Step 1: Collect CAN Log (READY mode)               ║
║  [ ] Step 2: Fingerprint Patch                          ║
║  [ ] Step 3: EPS Probe (IG-ON)                          ║
║  [ ] Step 4: DataFlash Dump (IG-ON)                     ║
║  [ ] Step 5: SecOC Key Extraction & Verification        ║
║  [ ] Step 6: Install SecOC Key                          ║
╚══════════════════════════════════════════════════════════╝
```

---

## Detailed Walkthrough

### Session 1: CAN Collection (car in READY)

1. Start the car in **READY mode** (foot on brake + push start)
2. SSH into comma and run the wizard
3. Step 1 collects 60 seconds of CAN frames
4. Step 2 checks/patches the fingerprint (may tell you to reboot)

**Vehicle state:** READY (hybrid system on, dashboard shows READY)

```bash
cd /data/toyota_dataflash_secoc_setup
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 toyota_dataflash_secoc_setup.py --yes
```

If fingerprint was patched, reboot and re-run:
```bash
sudo reboot
# ... wait for reboot ...
ssh comma@<COMMA_IP>
cd /data/toyota_dataflash_secoc_setup
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 toyota_dataflash_secoc_setup.py --yes
```

### Session 2: EPS Dump (car in IG-ON)

1. Turn car **completely OFF**
2. Wait **30 seconds**
3. Press start button **WITHOUT** foot on brake → IG-ON (dash lit, no READY indicator)
4. SSH in and re-run the wizard

**Vehicle state:** IG-ON only (electronics on, hybrid NOT started)

```bash
cd /data/toyota_dataflash_secoc_setup
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 toyota_dataflash_secoc_setup.py --yes
```

Steps 3-5 will run:
- Step 3: Confirms EPS firmware
- Step 4: Dumps EPS memory (32KB)
- Step 5: Extracts and verifies SecOCKey

> **If Step 4 gets only 1 frame (⚡ primed):**
> Turn car OFF → wait 30s → IG-ON → re-run wizard. Second run gets the full dump.

### Session 3: Key Installation

Step 6 installs the verified key. Then reboot:

```bash
sudo reboot
```

After reboot, test:
- LKAS/LTA engage
- ACC engage

---

## CLI Options

```bash
# Check current progress without running anything
python3 toyota_dataflash_secoc_setup.py --status

# Skip to a specific step
python3 toyota_dataflash_secoc_setup.py --step dump_dataflash

# Re-run a completed step
python3 toyota_dataflash_secoc_setup.py --redo collect_can

# Auto-confirm all prompts (no Y/n questions)
python3 toyota_dataflash_secoc_setup.py --yes

# Use a custom directory
python3 toyota_dataflash_secoc_setup.py --setup-dir /data/my_setup
```

---

## Troubleshooting

### "MISSING DEPENDENCIES"

The script auto-detects missing packages and suggests the fix. Usually:

```bash
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 toyota_dataflash_secoc_setup.py
```

If `/usr/local/venv/bin/python3` doesn't exist, try:
```bash
PYTHONPATH=/data/openpilot /data/openpilot/.venv/bin/python3 toyota_dataflash_secoc_setup.py
```

### "DIAGNOSTIC_SESSION_CONTROL - conditions not correct"

EPS refuses programming session. Fix:
1. Car must be in **IG-ON only** (NOT READY)
2. Turn car OFF, wait 30 seconds, then IG-ON (no brake)

### "USB connect error / LIBUSB_ERROR_BUSY"

Openpilot/boardd is holding the Panda USB. The wizard handles this automatically by stopping openpilot before each step. If it persists:

```bash
sudo systemctl stop comma
pkill -f boardd
```

### "Permission denied: /cache/params/SecOCKey"

The wizard handles this with a sudo fallback. If it still fails:

```bash
sudo cp /data/params/d/SecOCKey /cache/params/SecOCKey
sudo chmod 600 /cache/params/SecOCKey
```

### Dump only gets 1 frame (4 bytes)

This is the "prime + power cycle" pattern:
1. First run primes the EPS (normal, expected)
2. Turn car OFF → wait 30 seconds → IG-ON
3. Re-run wizard — second run gets full 32KB dump

---

## What Gets Generated

```
/data/toyota_dataflash_secoc_setup/
├── state.json              # Wizard progress (resumable)
├── can_oracle.ndjson       # CAN sync/protected frames (Step 1)
├── can_fingerprint.ndjson  # All CAN frames (Step 1)
├── eps_probe.json          # EPS DID readings (Step 3)
├── payload_dataflash_ff200000_ff208000.bin  # Payload
├── dump_ff200000_ff208000.bin              # EPS memory dump (Step 4)
├── SecOCKey.hex            # The verified key (Step 5)
└── key_backups/            # Previous key backups (Step 6)
```

---

## Security Notes

- The **SecOCKey** is vehicle-specific — do not share `SecOCKey.hex` or the dump `.bin` publicly
- The source code does NOT contain any vehicle-specific keys
- `SEED_KEY_SECRET` in the code is EPS-model-specific (same for all `8965B4514000` units), not vehicle-specific
- `state.json` only stores key hash (SHA256 prefix), never the raw key

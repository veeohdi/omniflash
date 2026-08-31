# OmniFlash (Pixel 4 XL `coral` Factory Image Flasher)

A high-stakes, defensive local web tool for safely flashing official Google Android factory images (Android 10, 11, 12, and 13) onto a physical **Google Pixel 4 XL (codename: `coral`)** for QA testing.

---

## Built-in Safety Guardrails

- **Strict Codename Enforcement**: Before any action, verifies `product: coral` / `ro.product.device == coral`. Non-coral devices are immediately halted.
- **Single-Device Lockdown**: Stops immediately if 0 or >1 devices are connected to prevent targeting the wrong device.
- **Battery Safety Check**: Blocks flashing if the battery level is below 30%.
- **Local Official Images Only**: Computes the SHA-256 checksum and requires manual verification against Google's official factory images page.
- **Deterministic Direct Flash Script**: Generates and executes a deterministic `omniflash_run.sh` / `.bat` flash script that flashes each partition individually with explicit serial targeting (`-s`), pre-extracts firmware images to avoid USB timeout during decompression, and routes `system_other` to `--slot=other`. Based on and validated against Google's official `flash-all` scripts.
- **Immediate Halt on Error**: Any critical failure halts the sequence without blind retries.
- **Typed Confirmations**: Requires typing `UNLOCK`, `FLASH`, or `LOCK` for destructive operations.
- **Post-Flash Boot Verification**: Confirms `sys.boot_completed == 1` and checks the Android release version before offering an optional relock step.
- **Session Disk Logs**: Writes full command outputs, timestamps, and exit codes to `flash_logs/`.

---

## Quick Start

### Ubuntu / Linux
```bash
./install_ubuntu.sh
./run.sh
```

### macOS
```bash
./install_mac.sh
./run.sh
```

### Windows
1. Double-click `install_windows.bat`
2. Double-click `run.bat`

---

## Flashing Workflow

1. **Acknowledge Risk Banner**: Confirm backup and device understanding.
2. **Connect Device**: Plug in your Pixel 4 XL via a direct USB connection.
3. **Inspect Image**: Specify the path to your downloaded `coral-*-factory-*.zip`.
4. **Verify SHA-256**: Check the computed hash against Google's official page and check the confirmation box.
5. **Unlock Bootloader (if needed)**: Type `UNLOCK` and follow on-screen phone prompts.
6. **Flash Firmware**: Type `FLASH` and keep USB connected while output streams live.
7. **Verify & Optional Relock**: Confirm successful boot into stock Android, then optionally relock the bootloader.

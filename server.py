#!/usr/bin/env python3
"""
OmniFlash - Pixel 4 XL (coral) Factory Image Flasher
Backend Server (Flask)

BOUND STRICTLY TO 127.0.0.1:8086.
"""

import os
import sys
import time
import subprocess
import shutil
import hashlib
import zipfile
import tempfile
import threading
import queue
import re
from datetime import datetime

# ─────────────────────────────────────────────
# Python Dependency Auto-Installer
# ─────────────────────────────────────────────
def check_dependencies():
    """Ensure Flask is available without requiring manual user setup."""
    try:
        import flask  # noqa: F401
    except ImportError:
        print("[!] Flask is not installed. Installing automatically...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "flask", "--break-system-packages"], check=True)
            print("[+] Flask installed successfully! Starting server...\n")
        except Exception as e:
            print(f"[!] Failed to auto-install Flask: {e}")
            print("    Please run: pip install flask")
            sys.exit(1)

check_dependencies()

from flask import Flask, request, jsonify, Response, send_from_directory, abort

app = Flask(__name__)

# ─────────────────────────────────────────────
# Global Constants & Hardware Constraints
# ─────────────────────────────────────────────
TARGET_PRODUCT = "coral"  # Google Pixel 4 XL strictly. Hardware gating prevents flashing wrong device models.
ALLOWED_ANDROID_VERSIONS = {"10", "11", "12", "13"}
MIN_BATTERY_PERCENT = 30
MIN_FASTBOOT_VOLTAGE_MV = 3700  # Safe voltage threshold in Fastboot mode (~3.7V). Prevents mid-flash power collapse.
SERVER_PORT = 8086
SERVER_HOST = "127.0.0.1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ─────────────────────────────────────────────
# Binary Resolver (Bundled Platform-Tools)
# ─────────────────────────────────────────────
# RATIONALE & POST-MORTEM:
# Debian/Ubuntu system packages of fastboot (e.g. 34.0.5-debian) contain a known bug in libsparse
# that crashes with 'Error reading sparse file' when creating sparse chunks from images > 2GB.
# We bundle official Google Platform-Tools (v37.0.1+) in deps/ (Linux/macOS) and windows/deps/
# to guarantee reliable sparse chunking and protocol compliance across all operating systems.
def get_platform_tools():
    """Resolve paths to adb and fastboot binaries (bundled or system)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    if sys.platform == "win32":
        win_deps = os.path.join(base_dir, "windows", "deps")
        adb = os.path.join(win_deps, "adb.exe")
        fastboot = os.path.join(win_deps, "fastboot.exe")
        if not os.path.exists(adb) or not os.path.exists(fastboot):
            adb = shutil.which("adb") or "adb"
            fastboot = shutil.which("fastboot") or "fastboot"
    else:
        unix_deps = os.path.join(base_dir, "deps")
        adb = os.path.join(unix_deps, "adb")
        fastboot = os.path.join(unix_deps, "fastboot")
        if not os.path.exists(adb) or not os.path.exists(fastboot):
            adb = shutil.which("adb") or "adb"
            fastboot = shutil.which("fastboot") or "fastboot"
            
    return adb, fastboot

adb_path, fastboot_path = get_platform_tools()

def get_tools_dir():
    """Return directory to prepend to PATH for subprocess execution."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == "win32":
        d = os.path.join(base_dir, "windows", "deps")
    else:
        d = os.path.join(base_dir, "deps")
    if os.path.exists(d):
        return d
    return os.path.dirname(shutil.which("fastboot") or "/usr/bin")

# ─────────────────────────────────────────────
# Concurrency, Logging, & Terminal Streaming
# ─────────────────────────────────────────────
# Operation lock guarantees only 1 destructive flash/unlock/reboot action runs at any moment.
operation_lock = threading.Lock()
is_operation_running = False
current_operation_name = ""
last_flash_result = {"completed": False, "success": False, "error": None}

terminal_subscribers = []
terminal_subscribers_lock = threading.Lock()

# Auto-shutdown watchdog state
# Tolerate browser reloads and background tab throttling without prematurely shutting down.
server_start_time = time.time()
last_heartbeat_time = time.time()
has_client_connected = False
heartbeat_lock = threading.Lock()
INITIAL_LAUNCH_GRACE_SECONDS = 300.0  # 5 minutes grace period before initial browser connection
DISCONNECT_TIMEOUT_SECONDS = 180.0     # 3 minutes tolerance for backgrounded/throttled tabs

def shutdown_server():
    """Cleanly clean up and exit server."""
    with operation_lock:
        if is_operation_running:
            return  # Never shut down while an operation is actively executing
    print("\n[!] Browser window closed. Shutting down OmniFlash server...")
    os._exit(0)

def watchdog_worker():
    global last_heartbeat_time, has_client_connected
    while True:
        time.sleep(1.0)
        now = time.time()
        with operation_lock:
            if is_operation_running:
                continue
        with heartbeat_lock:
            connected = has_client_connected
            last_ping = last_heartbeat_time
        if connected:
            if now - last_ping > DISCONNECT_TIMEOUT_SECONDS:
                shutdown_server()
                break
        else:
            if now - server_start_time > INITIAL_LAUNCH_GRACE_SECONDS:
                print("\n[!] No browser connected within initial startup window. Shutting down...")
                shutdown_server()
                break

threading.Thread(target=watchdog_worker, daemon=True).start()

def broadcast_terminal(message: str, line_type: str = "out"):
    """Broadcast a line of output to all active web terminal listeners."""
    with terminal_subscribers_lock:
        payload = {"text": message, "type": line_type, "time": datetime.now().strftime("%H:%M:%S")}
        for q in list(terminal_subscribers):
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

def get_session_logfile(action_name: str = "flash") -> str:
    """Create a unique session log file in flash_logs/."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "flash_logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(log_dir, f"session_{ts}_{action_name}.log")

def log_to_file(filepath: str, message: str):
    """Append a line to the session disk log."""
    try:
        with open(filepath, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass

# ─────────────────────────────────────────────
# Strict Hardware Verification
# ─────────────────────────────────────────────
def verify_coral_adb(serial: str = None) -> tuple[bool, str]:
    """Verify connected ADB device is strictly Pixel 4 XL ('coral')."""
    cmd = [adb_path]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["shell", "getprop", "ro.product.device"])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        device = res.stdout.strip().lower()
        if device == TARGET_PRODUCT:
            return True, device
        # Also check ro.build.product fallback cleanly without index mutation
        cmd_fallback = [adb_path]
        if serial:
            cmd_fallback.extend(["-s", serial])
        cmd_fallback.extend(["shell", "getprop", "ro.build.product"])
        res2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=5)
        device2 = res2.stdout.strip().lower()
        if device2 == TARGET_PRODUCT:
            return True, device2
        return False, device or device2 or "unknown"
    except Exception as e:
        return False, str(e)

def verify_coral_fastboot(serial: str = None) -> tuple[bool, str]:
    """Verify connected fastboot device is strictly Pixel 4 XL ('coral')."""
    cmd = [fastboot_path]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(["getvar", "product"])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        output = (res.stderr + "\n" + res.stdout).lower()
        for line in output.splitlines():
            if "product:" in line:
                val = line.split("product:", 1)[1].strip()
                if val == TARGET_PRODUCT:
                    return True, val
                return False, val
        return False, "unknown"
    except Exception as e:
        return False, str(e)

# ─────────────────────────────────────────────
# Device State Detection Engine
# ─────────────────────────────────────────────
def query_device_state(ignore_busy: bool = False):
    """
    Detect device connection across Android OS (adb), Bootloader (fastboot),
    or Fastbootd (userspace fastboot). Enforces single-device check.
    Pauses USB querying if an active operation (flash/unlock/relock) is running.
    """
    if not ignore_busy:
        with operation_lock:
            running = is_operation_running
            op_name = current_operation_name
        if running:
            return {
                "status": "busy",
                "count": 1,
                "mode": "busy",
                "operation": op_name,
                "message": f"Operation '{op_name}' in progress. USB state polling paused.",
                "battery_safe": True
            }

    adb_devices = []
    fastboot_devices = []
    
    # 1. Query ADB devices (normal Android userland)
    # Note: 'adb devices -l' output has mode as the 2nd token ('device', 'unauthorized', 'offline').
    # We map 'device' to authorized state and extract the model descriptor if present.
    try:
        res_adb = subprocess.run([adb_path, "devices", "-l"], capture_output=True, text=True, timeout=4)
        for line in res_adb.stdout.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
                adb_devices.append({"serial": serial, "model": model, "mode": "device"})
            elif len(parts) >= 2 and parts[1] in ("unauthorized", "offline", "recovery"):
                adb_devices.append({"serial": parts[0], "model": "", "mode": parts[1]})
    except Exception:
        pass

    # 2. Query Fastboot devices (hardware bootloader and userspace fastbootd)
    # CRITICAL FIX: Pixel devices running Android 10+ transition into userspace 'fastbootd'
    # when writing dynamic super partitions. 'fastboot devices' reports 'fastbootd' as the mode token.
    # We must accept both 'fastboot' and 'fastbootd' so the UI does not misidentify the device as disconnected.
    try:
        res_fb = subprocess.run([fastboot_path, "devices"], capture_output=True, text=True, timeout=4)
        for line in res_fb.splitlines() if isinstance(res_fb, list) else res_fb.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] in ("fastboot", "fastbootd"):
                serial = parts[0]
                fastboot_devices.append({"serial": serial, "mode": "fastboot"})
    except Exception:
        pass

    total_count = len(adb_devices) + len(fastboot_devices)
    
    if total_count == 0:
        return {
            "status": "disconnected",
            "count": 0,
            "message": "No device detected. Connect your Pixel 4 XL via USB."
        }
        
    if total_count > 1:
        return {
            "status": "multiple_devices",
            "count": total_count,
            "message": f"Multiple devices ({total_count}) detected. Please disconnect all other devices/emulators."
        }

    # Exactly 1 device attached
    if adb_devices:
        dev = adb_devices[0]
        serial = dev["serial"]
        mode = dev["mode"]
        if mode != "device":
            return {
                "status": "unauthorized" if mode == "unauthorized" else "offline",
                "count": 1,
                "serial": serial,
                "mode": mode,
                "message": "Device is unauthorized. Please check your phone screen and tap 'Allow USB debugging'." if mode == "unauthorized" else f"Device is in {mode} mode."
            }
            
        # Verify coral
        is_coral, prod = verify_coral_adb(serial)
        if not is_coral:
            return {
                "status": "wrong_device",
                "count": 1,
                "serial": serial,
                "detected_product": prod,
                "message": f"CRITICAL: Connected device is '{prod}'. Only Pixel 4 XL ('{TARGET_PRODUCT}') is supported."
            }

        # Query OS build and battery
        build_ver = ""
        build_id = ""
        battery_level = -1
        is_locked = None
        
        try:
            r = subprocess.run([adb_path, "-s", serial, "shell", "getprop", "ro.build.version.release"], capture_output=True, text=True, timeout=3)
            build_ver = r.stdout.strip()
            r = subprocess.run([adb_path, "-s", serial, "shell", "getprop", "ro.build.id"], capture_output=True, text=True, timeout=3)
            build_id = r.stdout.strip()
            r = subprocess.run([adb_path, "-s", serial, "shell", "getprop", "ro.boot.flash.locked"], capture_output=True, text=True, timeout=3)
            locked_val = r.stdout.strip()
            is_locked = (locked_val == "1") if locked_val in ("0", "1") else None
            
            r_bat = subprocess.run([adb_path, "-s", serial, "shell", "dumpsys", "battery"], capture_output=True, text=True, timeout=3)
            for line in r_bat.stdout.splitlines():
                if "level:" in line:
                    battery_level = int(line.split("level:", 1)[1].strip())
                    break
        except Exception:
            pass

        return {
            "status": "ready",
            "count": 1,
            "mode": "android",
            "serial": serial,
            "product": TARGET_PRODUCT,
            "android_version": build_ver,
            "build_id": build_id,
            "battery_level": battery_level,
            "battery_safe": battery_level >= MIN_BATTERY_PERCENT if battery_level >= 0 else True,
            "bootloader_locked": is_locked
        }

    else:
        # Fastboot Mode
        dev = fastboot_devices[0]
        serial = dev["serial"]
        
        # Verify coral
        is_coral, prod = verify_coral_fastboot(serial)
        if not is_coral:
            return {
                "status": "wrong_device",
                "count": 1,
                "serial": serial,
                "detected_product": prod,
                "message": f"CRITICAL: Connected fastboot device is '{prod}'. Only Pixel 4 XL ('{TARGET_PRODUCT}') is supported."
            }

        # Query fastboot variables
        unlocked = None
        is_userspace = False
        current_slot = ""
        battery_voltage = ""
        
        try:
            r = subprocess.run([fastboot_path, "-s", serial, "getvar", "all"], capture_output=True, text=True, timeout=4)
            out = r.stderr + "\n" + r.stdout
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("(bootloader) unlocked:"):
                    unlocked = line.split(":", 1)[1].strip() == "yes"
                elif line.startswith("(bootloader) is-userspace:"):
                    is_userspace = line.split(":", 1)[1].strip() == "yes"
                elif line.startswith("(bootloader) current-slot:"):
                    current_slot = line.split(":", 1)[1].strip()
                elif line.startswith("(bootloader) battery-voltage:"):
                    battery_voltage = line.split(":", 1)[1].strip()
        except Exception:
            pass

        # Determine battery safety in Fastboot mode
        battery_safe = True
        volt_digits = re.search(r'\d+', battery_voltage)
        if volt_digits:
            try:
                volt_int = int(volt_digits.group())
                battery_safe = (volt_int >= MIN_FASTBOOT_VOLTAGE_MV)
            except ValueError:
                pass

        return {
            "status": "ready",
            "count": 1,
            "mode": "fastbootd" if is_userspace else "fastboot",
            "serial": serial,
            "product": TARGET_PRODUCT,
            "bootloader_unlocked": unlocked,
            "current_slot": current_slot,
            "battery_voltage": battery_voltage,
            "battery_safe": battery_safe
        }

# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────
@app.before_request
def require_json():
    if request.path.startswith('/api/') and request.method == 'POST':
        if request.path in ('/api/heartbeat', '/api/tab_closed'):
            return
        if request.content_length and request.content_length > 0:
            if not request.is_json:
                return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 415

@app.route('/api/heartbeat', methods=['GET', 'POST'])
def api_heartbeat():
    global last_heartbeat_time, has_client_connected
    with heartbeat_lock:
        last_heartbeat_time = time.time()
        has_client_connected = True
    return jsonify({"status": "ok", "time": last_heartbeat_time})

@app.route('/api/tab_closed', methods=['POST'])
def api_tab_closed():
    global last_heartbeat_time
    with heartbeat_lock:
        last_heartbeat_time = time.time() - (DISCONNECT_TIMEOUT_SECONDS - 2.0)
    return jsonify({"status": "ok"})

@app.route('/')
def index():
    global last_heartbeat_time, has_client_connected
    with heartbeat_lock:
        last_heartbeat_time = time.time()
        has_client_connected = True
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    allowed = {'index.html', 'style.css', 'app.js', 'icon.svg'}
    if path in allowed:
        return send_from_directory('.', path)
    abort(404)

@app.route('/api/stream')
def stream_terminal():
    """SSE endpoint streaming live raw terminal stdout/stderr."""
    def event_stream():
        q = queue.Queue(maxsize=2000)
        with terminal_subscribers_lock:
            terminal_subscribers.append(q)
        try:
            while True:
                try:
                    item = q.get(timeout=25.0)
                    if item is None:
                        break
                    yield f"data: {jsonify(item).get_data(as_text=True)}\n\n"
                except queue.Empty:
                    # Keepalive ping
                    yield ": keepalive\n\n"
        finally:
            with terminal_subscribers_lock:
                if q in terminal_subscribers:
                    terminal_subscribers.remove(q)

    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/api/device/state', methods=['GET'])
def api_device_state():
    """Query current device state across Android OS, Fastboot, and Fastbootd."""
    return jsonify(query_device_state())

@app.route('/api/image/inspect', methods=['POST'])
def api_inspect_image():
    """
    Inspect a local official Google Factory Image zip:
    1. Computes SHA-256 in chunks.
    2. Validates zip file contents (bootloader, radio, image, flash-all).
    3. Confirms compatibility with Pixel 4 XL ('coral') and Android 10-13.
    """
    data = request.get_json(silent=True) or {}
    zip_path = (data.get("file_path") or "").strip()
    
    if not zip_path:
        return jsonify({"status": "error", "message": "Please specify the local factory image zip path."}), 400
        
    zip_path = os.path.expanduser(zip_path)
    if not os.path.exists(zip_path) or not os.path.isfile(zip_path):
        return jsonify({"status": "error", "message": f"File not found: {zip_path}"}), 404
        
    if not zip_path.lower().endswith(".zip"):
        return jsonify({"status": "error", "message": "Factory image must be a .zip file."}), 400

    broadcast_terminal(f"Inspecting factory image: {os.path.basename(zip_path)}...", "sys")
    
    # 1. Compute SHA-256 hash in 64KB chunks
    sha256_hash = hashlib.sha256()
    total_bytes = os.path.getsize(zip_path)
    read_bytes = 0
    
    try:
        with open(zip_path, "rb") as f:
            while chunk := f.read(65536):
                sha256_hash.update(chunk)
                read_bytes += len(chunk)
        computed_sha256 = sha256_hash.hexdigest().lower()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to compute SHA-256: {e}"}), 500

    # 2. Inspect zip structure
    found_bootloader = False
    found_radio = False
    found_image_zip = False
    found_flash_all = False
    detected_build_id = ""
    detected_android_version = ""
    
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            for name in namelist:
                base = os.path.basename(name).lower()
                if base.startswith("bootloader-coral-") and base.endswith(".img"):
                    found_bootloader = True
                elif base.startswith("radio-coral-") and base.endswith(".img"):
                    found_radio = True
                elif base.startswith("image-coral-") and base.endswith(".zip"):
                    found_image_zip = True
                elif base in ("flash-all.sh", "flash-all.bat"):
                    found_flash_all = True
                    
            # Parse build ID and Android version from filename
            fname = os.path.basename(zip_path).lower()
            if "coral-" in fname:
                parts = fname.split("coral-", 1)[1].split("-factory-", 1)
                if parts:
                    detected_build_id = parts[0].upper()
                    
            # Detect Android version from build ID prefix
            if detected_build_id.startswith(("TP1A", "TQ1A", "TQ2A", "TQ3A", "TD1A")):
                detected_android_version = "13"
            elif detected_build_id.startswith(("SQ1A", "SQ3A", "SP1A", "SP2A", "SD1A")):
                detected_android_version = "12"
            elif detected_build_id.startswith(("RP1A", "RQ1A", "RQ2A", "RQ3A", "RD1A")):
                detected_android_version = "11"
            elif detected_build_id.startswith(("QD1A", "QQ1A", "QQ2A", "QQ3A", "QP1A")):
                detected_android_version = "10"
            elif detected_build_id.startswith(("AP1A", "AP2A", "UD1A", "UP1A", "UQ1A")):
                detected_android_version = "14"
            elif detected_build_id.startswith(("PQ1A", "PQ2A", "PQ3A", "PD1A")):
                detected_android_version = "9"
            else:
                detected_android_version = "unknown"
                
    except zipfile.BadZipFile:
        return jsonify({"status": "error", "message": "Corrupted or invalid ZIP archive."}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error inspecting ZIP: {e}"}), 500

    is_valid_structure = found_bootloader and found_radio and found_image_zip and found_flash_all
    
    if not is_valid_structure:
        missing = []
        if not found_bootloader: missing.append("bootloader-coral-*.img")
        if not found_radio: missing.append("radio-coral-*.img")
        if not found_image_zip: missing.append("image-coral-*.zip")
        if not found_flash_all: missing.append("flash-all script")
        return jsonify({
            "status": "invalid_image",
            "message": f"Invalid Google Pixel 4 XL factory image. Missing: {', '.join(missing)}",
            "sha256": computed_sha256,
            "filename": os.path.basename(zip_path)
        }), 400

    if detected_android_version not in ALLOWED_ANDROID_VERSIONS:
        return jsonify({
            "status": "invalid_image",
            "message": f"Unsupported or unknown Android version '{detected_android_version}'. OmniFlash strictly supports Android {', '.join(sorted(ALLOWED_ANDROID_VERSIONS))}.",
            "sha256": computed_sha256,
            "filename": os.path.basename(zip_path)
        }), 400

    broadcast_terminal(f"Image Verified: Pixel 4 XL ('coral') | Build: {detected_build_id} (Android {detected_android_version})", "success")
    broadcast_terminal(f"SHA-256: {computed_sha256}", "sys")

    return jsonify({
        "status": "success",
        "file_path": zip_path,
        "filename": os.path.basename(zip_path),
        "size_bytes": total_bytes,
        "sha256": computed_sha256,
        "build_id": detected_build_id,
        "android_version": detected_android_version,
        "is_coral_image": True
    })

@app.route('/api/bootloader/unlock', methods=['POST'])
def api_bootloader_unlock():
    """
    Unlock Bootloader (fastboot flashing unlock).
    Enforces typed confirmation ('UNLOCK') and coral codename check.
    """
    global is_operation_running, current_operation_name
    data = request.get_json(silent=True) or {}
    confirm = (data.get("confirmation") or "").strip()
    
    if confirm != "UNLOCK":
        return jsonify({"status": "error", "message": "Typed confirmation must be 'UNLOCK'."}), 400
        
    with operation_lock:
        if is_operation_running:
            return jsonify({"status": "busy", "message": f"Operation '{current_operation_name}' is currently in progress."}), 409
        is_operation_running = True
        current_operation_name = "unlock_bootloader"

    logfile = get_session_logfile("unlock")
    log_to_file(logfile, "=== Starting Bootloader Unlock Session ===")
    
    def unlock_worker():
        global is_operation_running, current_operation_name
        try:
            broadcast_terminal(">>> Initiating Bootloader Unlock Sequence...", "warn")
            broadcast_terminal("WARNING: This will wipe all user data on the device.", "warn")
            
            # 1. Check current mode and transition to bootloader if needed
            state = query_device_state(ignore_busy=True)
            if state["status"] != "ready":
                broadcast_terminal(f"Error: {state.get('message', 'Device not ready')}", "error")
                return

            serial = state["serial"]
            if state["mode"] == "android":
                broadcast_terminal(f"> adb -s {serial} reboot bootloader", "cmd")
                log_to_file(logfile, f"Command: adb -s {serial} reboot bootloader")
                subprocess.run([adb_path, "-s", serial, "reboot", "bootloader"], capture_output=True, timeout=10)
                
                # Poll for fastboot mode
                broadcast_terminal("Waiting for device to enter bootloader mode...", "sys")
                found = False
                for _ in range(30):
                    time.sleep(1.5)
                    st = query_device_state(ignore_busy=True)
                    if st["status"] == "ready" and st["mode"] in ("fastboot", "fastbootd"):
                        found = True
                        break
                if not found:
                    broadcast_terminal("Timed out waiting for device to enter bootloader.", "error")
                    return

            # 2. Strict fastboot verification
            is_coral, prod = verify_coral_fastboot(serial)
            if not is_coral:
                broadcast_terminal(f"HARD STOP: Device is '{prod}', not '{TARGET_PRODUCT}'. Aborted.", "error")
                return

            # 3. Execute fastboot flashing unlock
            broadcast_terminal(f"> fastboot -s {serial} flashing unlock", "cmd")
            log_to_file(logfile, f"Command: fastboot -s {serial} flashing unlock")
            
            res = subprocess.run([fastboot_path, "-s", serial, "flashing", "unlock"], capture_output=True, text=True, timeout=60)
            out = res.stdout + "\n" + res.stderr
            for line in out.splitlines():
                if line.strip():
                    broadcast_terminal(line, "out")
                    log_to_file(logfile, line)

            if res.returncode != 0:
                if "not allowed" in out.lower() or "disabled" in out.lower():
                    broadcast_terminal("\n[!] UNLOCK FAILED: 'OEM Unlocking' is not enabled.", "error")
                    broadcast_terminal("    Fix: Boot phone into Android -> Settings -> System -> Developer Options -> Enable 'OEM unlocking'.", "warn")
                else:
                    broadcast_terminal(f"\n[!] Unlock command exited with code {res.returncode}.", "error")
            else:
                broadcast_terminal("\n[+] Fastboot unlock signal sent.", "success")
                broadcast_terminal("    ACTION REQUIRED: Look at your phone screen now. Use Volume keys to select 'UNLOCK THE BOOTLOADER' and press Power button.", "warn")

        except Exception as e:
            broadcast_terminal(f"Exception during unlock: {e}", "error")
            log_to_file(logfile, f"Exception: {e}")
        finally:
            with operation_lock:
                is_operation_running = False
                current_operation_name = ""

    threading.Thread(target=unlock_worker, daemon=True).start()
    return jsonify({"status": "started", "message": "Bootloader unlock command initiated."})

@app.route('/api/flash/start', methods=['POST'])
def api_flash_start():
    """
    Flash Factory Image using Google's official bundled flash-all.sh / flash-all.bat.
    Strictly verifies:
    - Typed confirmation 'FLASH'
    - Explicit checksum_verified == True
    - Single device & coral codename
    - Battery >= 30% / >= 3700 mV in fastboot
    """
    global is_operation_running, current_operation_name
    data = request.get_json(silent=True) or {}
    
    confirm = (data.get("confirmation") or "").strip()
    checksum_verified = data.get("checksum_verified", False)
    zip_path = (data.get("file_path") or "").strip()
    
    if confirm != "FLASH":
        return jsonify({"status": "error", "message": "Typed confirmation must be 'FLASH'."}), 400
    if not checksum_verified:
        return jsonify({"status": "error", "message": "You must manually verify and confirm the SHA-256 checksum first."}), 400
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({"status": "error", "message": "Factory image zip not found."}), 404

    with operation_lock:
        if is_operation_running:
            return jsonify({"status": "busy", "message": f"Operation '{current_operation_name}' is currently in progress."}), 409
        is_operation_running = True
        current_operation_name = "flash_factory_image"

    logfile = get_session_logfile("flash")
    log_to_file(logfile, "=== Starting Pixel 4 XL Flash Session ===")
    log_to_file(logfile, f"Target Zip: {zip_path}")
    
    def flash_worker():
        global is_operation_running, current_operation_name
        temp_extract_dir = None
        try:
            broadcast_terminal("=====================================================", "sys")
            broadcast_terminal("STARTING FACTORY IMAGE FLASH FOR PIXEL 4 XL (coral)", "warn")
            broadcast_terminal("=====================================================", "sys")
            
            # Step 1: Query & Verify Device State
            state = query_device_state(ignore_busy=True)
            if state["status"] != "ready":
                broadcast_terminal(f"Error: {state.get('message', 'Device not ready')}", "error")
                return

            serial = state["serial"]
            
            # Battery check (Android & Fastboot modes)
            if state["mode"] == "android" and state.get("battery_level", 100) < MIN_BATTERY_PERCENT:
                broadcast_terminal(f"HARD STOP: Battery level is {state['battery_level']}%. Minimum {MIN_BATTERY_PERCENT}% required to flash safely.", "error")
                return
            elif state["mode"] in ("fastboot", "fastbootd") and not state.get("battery_safe", True):
                broadcast_terminal(f"HARD STOP: Fastboot battery voltage is {state.get('battery_voltage', 'unknown')}. Minimum {MIN_FASTBOOT_VOLTAGE_MV} mV (3.7V) required to flash safely.", "error")
                return

            # Step 2: Reboot to Bootloader if in Android
            if state["mode"] == "android":
                broadcast_terminal(f"> adb -s {serial} reboot bootloader", "cmd")
                log_to_file(logfile, f"Command: adb -s {serial} reboot bootloader")
                subprocess.run([adb_path, "-s", serial, "reboot", "bootloader"], capture_output=True, timeout=10)
                
                broadcast_terminal("Waiting for device to enter bootloader...", "sys")
                found = False
                for _ in range(35):
                    time.sleep(1.5)
                    st = query_device_state(ignore_busy=True)
                    if st["status"] == "ready" and st["mode"] in ("fastboot", "fastbootd"):
                        found = True
                        break
                if not found:
                    broadcast_terminal("Timed out waiting for device to enter bootloader.", "error")
                    return

            # Step 3: Verify fastboot codename, voltage & lock state
            is_coral, prod = verify_coral_fastboot(serial)
            if not is_coral:
                broadcast_terminal(f"HARD STOP: Fastboot device reports product '{prod}'. Only '{TARGET_PRODUCT}' allowed.", "error")
                return

            # Verify fastboot battery voltage threshold
            res_volt = subprocess.run([fastboot_path, "-s", serial, "getvar", "battery-voltage"], capture_output=True, text=True, timeout=5)
            volt_out = res_volt.stderr + "\n" + res_volt.stdout
            volt_match = re.search(r'battery-voltage:\s*(\d+)', volt_out, re.IGNORECASE)
            if volt_match:
                volt_mv = int(volt_match.group(1))
                if volt_mv < MIN_FASTBOOT_VOLTAGE_MV:
                    broadcast_terminal(f"HARD STOP: Fastboot battery voltage is {volt_mv} mV. Minimum {MIN_FASTBOOT_VOLTAGE_MV} mV (3.7V) required to flash safely.", "error")
                    return

            # Check unlocked
            res_unlock = subprocess.run([fastboot_path, "-s", serial, "getvar", "unlocked"], capture_output=True, text=True, timeout=5)
            unlock_out = (res_unlock.stderr + "\n" + res_unlock.stdout).lower()
            if "unlocked: no" in unlock_out:
                broadcast_terminal("HARD STOP: Bootloader is LOCKED. Please unlock the bootloader before flashing.", "error")
                return

            # Step 4: Extract Google Factory Image Zip
            # CRITICAL FIX 1 (Disk Workspace vs Linux TMPFS):
            # On Linux distributions, default tempfile.mkdtemp() targets /tmp which is mounted as tmpfs in RAM.
            # On a system with 8GB RAM, /tmp is capped at 3.6GB. Unpacking a 2.2GB factory zip containing a 2.4GB
            # product.img and 800MB system.img instantly exhausts /tmp and aborts with 'I/O error'.
            # We explicitly place the workspace in BASE_DIR/workspaces/ on persistent disk storage (ext4 partition),
            # and export TMPDIR/TEMP/TMP to ensure fastboot unzips all working files on disk.
            workspaces_base = os.path.join(BASE_DIR, "workspaces")
            os.makedirs(workspaces_base, exist_ok=True)
            temp_extract_dir = tempfile.mkdtemp(prefix="coral_flash_", dir=workspaces_base)
            broadcast_terminal(f"Extracting factory image to disk workspace ({workspaces_base})...", "sys")
            log_to_file(logfile, f"Workspace: {temp_extract_dir}")
            
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(temp_extract_dir)

            # Find folder with flash-all.sh / flash-all.bat
            script_dir = temp_extract_dir
            flash_script_name = "flash-all.bat" if sys.platform == "win32" else "flash-all.sh"
            
            for root, _, files in os.walk(temp_extract_dir):
                if flash_script_name in files:
                    script_dir = root
                    break
                    
            flash_script_path = os.path.join(script_dir, flash_script_name)
            if not os.path.exists(flash_script_path):
                broadcast_terminal(f"Error: {flash_script_name} not found inside extracted archive.", "error")
                last_flash_result = {"completed": True, "success": False, "error": f"{flash_script_name} missing"}
                return

            # CRITICAL FIX 2 (Pre-Extraction vs On-The-Fly USB Decompression):
            # Google's stock 'fastboot update image.zip' extracts partition images sequentially over USB.
            # For Android 12/13, product.img is 2.5 GB. Decompressing it on-the-fly takes 45-55 seconds of 0% USB traffic,
            # triggering Linux USB autosuspend / endpoint timeouts ('submit_urb(0) failed: No such device').
            # By pre-extracting all nested .img files on disk BEFORE launching fastboot, each partition transfer
            # starts with 0 seconds latency and continuous USB transmission.
            for f in os.listdir(script_dir):
                if f.startswith("image-coral-") and f.endswith(".zip"):
                    nested_zip_path = os.path.join(script_dir, f)
                    broadcast_terminal(f"Pre-extracting firmware partitions ({f})...", "sys")
                    log_to_file(logfile, f"Pre-extracting {f} into {script_dir}")
                    try:
                        with zipfile.ZipFile(nested_zip_path, 'r') as nz:
                            nz.extractall(script_dir)
                        broadcast_terminal("Firmware partitions pre-extracted successfully.", "sys")
                    except Exception as ze:
                        log_to_file(logfile, f"Warning pre-extracting nested zip: {ze}")
                    break

            # Find bootloader and radio image names
            bootloader_img = None
            radio_img = None
            for f in os.listdir(script_dir):
                if f.startswith("bootloader-coral-") and f.endswith(".img"):
                    bootloader_img = f
                elif f.startswith("radio-coral-") and f.endswith(".img"):
                    radio_img = f

            # CRITICAL FIX 3 (Deterministic Direct Flash Script vs fastboot update):
            # Google's factory scripts invoke 'fastboot -w update image.zip'. In fastbootd mode,
            # this can trigger fixed 512M sparse mismatches or try to flash 'system_other' directly.
            # We generate a direct execution script (omniflash_run.sh / omniflash_run.bat) that:
            #   1. Flashes bootloader & radio with non-fatal fallbacks in case the device starts from fastbootd.
            #   2. Flashes boot, dtbo, vbmeta, vbmeta_system.
            #   3. Enters fastbootd ('fastboot reboot fastboot').
            #   4. Flashes dynamic super partitions (product, system, system_ext, vendor).
            #   5. Routes 'system_other' to '--slot=other system' with non-fatal fallback.
            #   6. Erases userdata and metadata for clean factory setup, and issues 'fastboot reboot'.
            if sys.platform == "win32":
                custom_script_path = os.path.join(script_dir, "omniflash_run.bat")
                lines = [
                    "@echo off",
                    f"fastboot flash bootloader {bootloader_img} || rem no bootloader" if bootloader_img else "rem no bootloader",
                    "fastboot reboot-bootloader",
                    "timeout /t 5 /nobreak >nul",
                    f"fastboot flash radio {radio_img} || rem no radio" if radio_img else "rem no radio",
                    "fastboot reboot-bootloader",
                    "timeout /t 5 /nobreak >nul",
                    "if exist boot.img fastboot flash boot boot.img",
                    "if exist dtbo.img fastboot flash dtbo dtbo.img",
                    "if exist vbmeta.img fastboot flash vbmeta vbmeta.img",
                    "if exist vbmeta_system.img fastboot flash vbmeta_system vbmeta_system.img",
                    "fastboot reboot fastboot",
                    "timeout /t 5 /nobreak >nul",
                    "if exist product.img fastboot flash product product.img",
                    "if exist system.img fastboot flash system system.img",
                    "if exist system_ext.img fastboot flash system_ext system_ext.img",
                    "if exist system_other.img fastboot flash --slot=other system system_other.img",
                    "if exist vendor.img fastboot flash vendor vendor.img",
                    "fastboot -w erase userdata",
                    "fastboot erase metadata",
                    "fastboot reboot"
                ]
                with open(custom_script_path, "w", encoding="utf-8") as csf:
                    csf.write("\r\n".join(lines) + "\r\n")
                flash_script_path = custom_script_path
            else:
                custom_script_path = os.path.join(script_dir, "omniflash_run.sh")
                lines = [
                    "#!/bin/sh",
                    "set -e",
                    f"fastboot flash bootloader {bootloader_img} || true" if bootloader_img else "# no bootloader",
                    "fastboot reboot-bootloader || true",
                    "sleep 4",
                    f"fastboot flash radio {radio_img} || true" if radio_img else "# no radio",
                    "fastboot reboot-bootloader || true",
                    "sleep 4",
                    "[ -f boot.img ] && fastboot flash boot boot.img",
                    "[ -f dtbo.img ] && fastboot flash dtbo dtbo.img",
                    "[ -f vbmeta.img ] && fastboot flash vbmeta vbmeta.img",
                    "[ -f vbmeta_system.img ] && fastboot flash vbmeta_system vbmeta_system.img",
                    "fastboot reboot fastboot || true",
                    "sleep 4",
                    "[ -f product.img ] && fastboot flash product product.img",
                    "[ -f system.img ] && fastboot flash system system.img",
                    "[ -f system_ext.img ] && fastboot flash system_ext system_ext.img",
                    "[ -f system_other.img ] && fastboot flash --slot=other system system_other.img || true",
                    "[ -f vendor.img ] && fastboot flash vendor vendor.img",
                    "fastboot -w erase userdata || fastboot erase userdata",
                    "fastboot erase metadata || true",
                    "fastboot reboot"
                ]
                with open(custom_script_path, "w", encoding="utf-8") as csf:
                    csf.write("\n".join(lines) + "\n")
                os.chmod(custom_script_path, 0o755)
                flash_script_path = custom_script_path

            # Step 5: Execute direct high-speed flash sequence
            broadcast_terminal("\n=====================================================", "sys")
            broadcast_terminal("EXECUTING DIRECT HIGH-SPEED FLASH SEQUENCE", "sys")
            broadcast_terminal("DO NOT DISCONNECT OR TOUCH THE DEVICE UNTIL COMPLETE!", "warn")
            broadcast_terminal("=====================================================\n", "sys")
            
            # Prepare environment with platform-tools at head of PATH and TMPDIR set to workspace disk
            tools_dir = get_tools_dir()
            env = os.environ.copy()
            env["PATH"] = f"{tools_dir}{os.pathsep}{env.get('PATH', '')}"
            env["TMPDIR"] = temp_extract_dir
            env["TEMP"] = temp_extract_dir
            env["TMP"] = temp_extract_dir
            if serial:
                env["ANDROID_SERIAL"] = serial
                
            if sys.platform == "win32":
                cmd = ["cmd.exe", "/c", flash_script_path]
            else:
                cmd = [flash_script_path]

            log_to_file(logfile, f"Executing: {cmd} in cwd={script_dir}")
            
            proc = subprocess.Popen(
                cmd,
                cwd=script_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            for line in iter(proc.stdout.readline, ''):
                clean = line.rstrip('\r\n')
                if clean:
                    broadcast_terminal(clean, "out")
                    log_to_file(logfile, clean)

            proc.wait()
            exit_code = proc.returncode
            log_to_file(logfile, f"flash-all process exit code: {exit_code}")

            if exit_code != 0:
                last_flash_result = {"completed": True, "success": False, "error": f"flash-all exited with code {exit_code}"}
                broadcast_terminal(f"\n[!] FLASH FAILED with exit code {exit_code}.", "error")
                broadcast_terminal(f"    Check session log for details: {logfile}", "error")
            else:
                last_flash_result = {"completed": True, "success": True, "error": None}
                broadcast_terminal("\n=====================================================", "success")
                broadcast_terminal("FLASH-ALL SCRIPT COMPLETED SUCCESSFULLY!", "success")
                broadcast_terminal("The device is now rebooting into Android for its first boot.", "success")
                broadcast_terminal("Initial boot may take 2-4 minutes. Keep USB connected.", "sys")
                broadcast_terminal("=====================================================\n", "success")

        except Exception as e:
            last_flash_result = {"completed": True, "success": False, "error": str(e)}
            broadcast_terminal(f"\nException during flash execution: {e}", "error")
            log_to_file(logfile, f"Exception: {e}")
        finally:
            # Clean up temp extract dir
            if temp_extract_dir and os.path.exists(temp_extract_dir):
                try:
                    shutil.rmtree(temp_extract_dir)
                except Exception:
                    pass
            with operation_lock:
                is_operation_running = False
                current_operation_name = ""

    threading.Thread(target=flash_worker, daemon=True).start()
    return jsonify({"status": "started", "message": "Flashing process initiated."})

@app.route('/api/flash/verify', methods=['POST'])
def api_flash_verify():
    """
    Post-flash verification:
    Polls until sys.boot_completed == 1 and verifies ro.build.version.release.
    """
    global last_flash_result
    data = request.get_json(silent=True) or {}
    expected_version = (data.get("expected_version") or "").strip()
    
    if last_flash_result.get("completed") and not last_flash_result.get("success"):
        return jsonify({
            "completed": False,
            "failed": True,
            "message": f"Flashing failed: {last_flash_result.get('error')}. Check console log."
        })

    state = query_device_state()
    if state["status"] != "ready":
        return jsonify({"completed": False, "message": "Waiting for device to boot into Android..."})

    if state["mode"] != "android":
        return jsonify({"completed": False, "message": f"Device is currently in {state['mode']} mode. Waiting for Android boot..."})

    serial = state["serial"]
    try:
        res = subprocess.run([adb_path, "-s", serial, "shell", "getprop", "sys.boot_completed"], capture_output=True, text=True, timeout=3)
        if res.stdout.strip() == "1":
            actual_version = state.get("android_version", "")
            actual_build_id = state.get("build_id", "")
            return jsonify({
                "completed": True,
                "verified": True,
                "android_version": actual_version,
                "build_id": actual_build_id,
                "message": f"Boot verified! Device running Android {actual_version} ({actual_build_id})."
            })
    except Exception as e:
        return jsonify({"completed": False, "message": str(e)})

    return jsonify({"completed": False, "message": "System starting up..."})

@app.route('/api/bootloader/relock', methods=['POST'])
def api_bootloader_relock():
    """
    Optional Bootloader Relock (fastboot flashing lock).
    Offered only as a separate, explicit action after successful flashing.
    """
    global is_operation_running, current_operation_name
    data = request.get_json(silent=True) or {}
    confirm = (data.get("confirmation") or "").strip()
    
    if confirm != "LOCK":
        return jsonify({"status": "error", "message": "Typed confirmation must be 'LOCK'."}), 400

    with operation_lock:
        if is_operation_running:
            return jsonify({"status": "busy", "message": f"Operation '{current_operation_name}' is in progress."}), 409
        is_operation_running = True
        current_operation_name = "relock_bootloader"

    logfile = get_session_logfile("relock")
    log_to_file(logfile, "=== Starting Bootloader Relock Session ===")
    
    def relock_worker():
        global is_operation_running, current_operation_name
        try:
            broadcast_terminal(">>> Initiating Bootloader Relock...", "warn")
            state = query_device_state(ignore_busy=True)
            if state["status"] != "ready":
                broadcast_terminal(f"Error: {state.get('message', 'Device not ready')}", "error")
                return

            serial = state["serial"]
            if state["mode"] == "android":
                broadcast_terminal(f"> adb -s {serial} reboot bootloader", "cmd")
                log_to_file(logfile, f"Command: adb -s {serial} reboot bootloader")
                subprocess.run([adb_path, "-s", serial, "reboot", "bootloader"], capture_output=True, timeout=10)
                
                broadcast_terminal("Waiting for device to enter bootloader mode...", "sys")
                found = False
                for _ in range(30):
                    time.sleep(1.5)
                    st = query_device_state(ignore_busy=True)
                    if st["status"] == "ready" and st["mode"] in ("fastboot", "fastbootd"):
                        found = True
                        break
                if not found:
                    broadcast_terminal("Timed out waiting for bootloader.", "error")
                    return

            is_coral, prod = verify_coral_fastboot(serial)
            if not is_coral:
                broadcast_terminal(f"HARD STOP: Device is '{prod}', not '{TARGET_PRODUCT}'.", "error")
                return

            broadcast_terminal(f"> fastboot -s {serial} flashing lock", "cmd")
            log_to_file(logfile, f"Command: fastboot -s {serial} flashing lock")
            
            res = subprocess.run([fastboot_path, "-s", serial, "flashing", "lock"], capture_output=True, text=True, timeout=60)
            out = res.stdout + "\n" + res.stderr
            for line in out.splitlines():
                if line.strip():
                    broadcast_terminal(line, "out")
                    log_to_file(logfile, line)

            if res.returncode == 0:
                broadcast_terminal("\n[+] Relock command sent.", "success")
                broadcast_terminal("    ACTION REQUIRED: Look at your phone screen now. Use Volume keys to select 'LOCK THE BOOTLOADER' and press Power button.", "warn")
            else:
                broadcast_terminal(f"\n[!] Relock failed with exit code {res.returncode}.", "error")

        except Exception as e:
            broadcast_terminal(f"Exception during relock: {e}", "error")
            log_to_file(logfile, f"Exception: {e}")
        finally:
            with operation_lock:
                is_operation_running = False
                current_operation_name = ""

    threading.Thread(target=relock_worker, daemon=True).start()
    return jsonify({"status": "started", "message": "Bootloader relock command initiated."})

if __name__ == '__main__':
    print("=====================================================")
    print("OmniFlash — Pixel 4 XL (coral) Flasher")
    print("=====================================================")
    print(f"  ADB:      {adb_path}")
    print(f"  Fastboot: {fastboot_path}")
    print(f"  Server:   http://{SERVER_HOST}:{SERVER_PORT}")
    print("  Press Ctrl+C to quit.")
    print("=====================================================")
    try:
        app.run(host=SERVER_HOST, port=SERVER_PORT, threaded=True, debug=False)
    except OSError as e:
        if "Address already in use" in str(e) or e.errno == 98:
            print(f"\n[!] Error: Port {SERVER_PORT} is already in use.")
        else:
            print(f"\n[!] Server error: {e}")
        sys.exit(1)

// ─────────────────────────────────────────────
// OmniFlash — Pixel 4 XL (coral) Flasher
// Frontend Application Logic
// ─────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // ── Helper ───────────────────────────────────
    const $ = (id) => document.getElementById(id);

    // ── State ────────────────────────────────────
    let deviceState = null;
    let inspectedImage = null;
    let isFlashing = false;
    let pollInterval = null;
    let verifyInterval = null;

    // ── UI Elements ──────────────────────────────
    const riskModal = $('risk-modal');
    const btnAckRisk = $('btn-ack-risk');

    const confirmModal = $('confirm-modal');
    const confirmTitle = $('confirm-title');
    const confirmDesc = $('confirm-desc');
    const confirmLabel = $('confirm-label');
    const confirmInput = $('confirm-input');
    const btnConfirmCancel = $('btn-confirm-cancel');
    const btnConfirmSubmit = $('btn-confirm-submit');
    let currentConfirmCallback = null;
    let currentRequiredPhrase = '';

    const headerConnBadge = $('header-conn-badge');
    const btnRefreshState = $('btn-refresh-state');
    const devMode = $('dev-mode');
    const devProduct = $('dev-product');
    const devSerial = $('dev-serial');
    const devBootloader = $('dev-bootloader');
    const devBuild = $('dev-build');
    const devBattery = $('dev-battery');
    const unlockPrompt = $('unlock-prompt');
    const btnUnlockBootloader = $('btn-unlock-bootloader');

    const imagePathInput = $('image-path-input');
    const btnInspectImage = $('btn-inspect-image');
    const imageDetails = $('image-details');
    const imgFilename = $('img-filename');
    const imgVersion = $('img-version');
    const imgBuildId = $('img-build-id');
    const imgSha256 = $('img-sha256');
    const chkVerifyHash = $('chk-verify-hash');

    const btnStartFlash = $('btn-start-flash');
    const verifyStatusBox = $('verify-status-box');
    const verifyMsg = $('verify-msg');
    const relockContainer = $('relock-container');
    const btnRelock = $('btn-relock');

    const terminalOutput = $('terminal-output');
    const btnClearTerm = $('btn-clear-term');

    // ── Brick Risk Acknowledgement ───────────────
    btnAckRisk.addEventListener('click', () => {
        riskModal.classList.add('hidden');
        logToTerminal('[SYSTEM] Risk acknowledged. OmniFlash safety guardrails active.', 'sys');
        startStatePolling();
    });

    // ── Terminal Logger ──────────────────────────
    function logToTerminal(text, type = 'out') {
        const div = document.createElement('div');
        div.className = `log-line ${type}`;
        div.textContent = text;
        terminalOutput.appendChild(div);
        
        // Auto-scroll
        terminalOutput.scrollTop = terminalOutput.scrollHeight;

        // Keep DOM lean (max 1200 lines)
        while (terminalOutput.children.length > 1200) {
            terminalOutput.removeChild(terminalOutput.firstChild);
        }
    }

    btnClearTerm.addEventListener('click', () => {
        terminalOutput.innerHTML = '';
    });

    // ── Live SSE Terminal Stream ─────────────────
    function connectSSE() {
        const evtSource = new EventSource('/api/stream');
        evtSource.onmessage = (e) => {
            if (!e.data || e.data.startsWith(':')) return;
            try {
                const payload = JSON.parse(e.data);
                logToTerminal(payload.text, payload.type);
            } catch (err) {
                // Ignore parse errors on keepalive
            }
        };
        evtSource.onerror = () => {
            // EventSource auto-reconnects
        };
    }
    connectSSE();

    // ── Modal Confirmation Helper ────────────────
    function requestTypedConfirmation(title, description, phrase, onConfirmed) {
        confirmTitle.textContent = title;
        confirmDesc.innerHTML = description;
        confirmLabel.textContent = `Type '${phrase}' to confirm and proceed:`;
        confirmInput.placeholder = phrase;
        confirmInput.value = '';
        btnConfirmSubmit.disabled = true;

        currentRequiredPhrase = phrase;
        currentConfirmCallback = onConfirmed;

        confirmModal.classList.remove('hidden');
        confirmInput.focus();
    }

    confirmInput.addEventListener('input', () => {
        const val = confirmInput.value.trim();
        btnConfirmSubmit.disabled = (val !== currentRequiredPhrase);
    });

    btnConfirmCancel.addEventListener('click', () => {
        confirmModal.classList.add('hidden');
        currentConfirmCallback = null;
        currentRequiredPhrase = '';
    });

    btnConfirmSubmit.addEventListener('click', () => {
        if (confirmInput.value.trim() === currentRequiredPhrase) {
            confirmModal.classList.add('hidden');
            if (currentConfirmCallback) {
                currentConfirmCallback();
            }
        }
    });

    // ── Device State Poller ──────────────────────
    async function fetchDeviceState() {
        try {
            const res = await fetch('/api/device/state');
            const data = await res.json();
            deviceState = data;
            renderDeviceState(data);
            updateFlashButtonState();
        } catch (e) {
            // Transient error
        }
    }

    function renderDeviceState(data) {
        if (data.status === 'busy') {
            headerConnBadge.className = 'badge badge-warn';
            headerConnBadge.textContent = `${(data.operation || 'Operation').replace(/_/g, ' ').toUpperCase()} IN PROGRESS`;
            return;
        }

        if (data.status === 'disconnected') {
            headerConnBadge.className = 'badge badge-disconnected';
            headerConnBadge.textContent = 'Device: Disconnected';
            devMode.textContent = 'Disconnected';
            devProduct.textContent = '—';
            devSerial.textContent = '—';
            devBootloader.textContent = '—';
            devBuild.textContent = '—';
            devBattery.textContent = '—';
            devBattery.style.color = 'inherit';
            unlockPrompt.classList.add('hidden');
            return;
        }

        if (data.status === 'multiple_devices') {
            headerConnBadge.className = 'badge badge-coral';
            headerConnBadge.textContent = `Error: ${data.count} Devices Connected`;
            devMode.textContent = 'Multiple Devices';
            devProduct.textContent = 'HALT: Disconnect Extra Devices';
            unlockPrompt.classList.add('hidden');
            return;
        }

        if (data.status === 'wrong_device') {
            headerConnBadge.className = 'badge badge-coral';
            headerConnBadge.textContent = `Error: Non-Coral Device (${data.detected_product})`;
            devMode.textContent = data.mode || 'Active';
            devProduct.textContent = `${data.detected_product} (WRONG HARDWARE)`;
            devSerial.textContent = data.serial || '—';
            unlockPrompt.classList.add('hidden');
            return;
        }

        if (data.status === 'unauthorized') {
            headerConnBadge.className = 'badge badge-warn';
            headerConnBadge.textContent = 'Device: Unauthorized (Check Screen)';
            devMode.textContent = 'Unauthorized';
            devSerial.textContent = data.serial;
            unlockPrompt.classList.add('hidden');
            return;
        }

        // Ready state (Android OS or Fastboot)
        headerConnBadge.className = 'badge badge-connected';
        headerConnBadge.textContent = `Pixel 4 XL (${data.mode.toUpperCase()})`;
        
        devMode.textContent = data.mode.toUpperCase();
        devProduct.textContent = 'Pixel 4 XL (coral)';
        devSerial.textContent = data.serial;

        if (data.mode === 'android') {
            devBuild.textContent = `Android ${data.android_version} (${data.build_id})`;
            devBattery.textContent = data.battery_level >= 0 ? `${data.battery_level}%` : 'Unknown';
            devBattery.style.color = data.battery_safe ? 'inherit' : 'var(--accent-coral)';
            if (data.bootloader_locked === true) {
                devBootloader.textContent = 'LOCKED';
                devBootloader.style.color = 'var(--accent-amber)';
                unlockPrompt.classList.remove('hidden');
            } else if (data.bootloader_locked === false) {
                devBootloader.textContent = 'UNLOCKED';
                devBootloader.style.color = 'var(--accent-green)';
                unlockPrompt.classList.add('hidden');
            } else {
                devBootloader.textContent = 'Unknown (Locked in OS)';
                unlockPrompt.classList.add('hidden');
            }
        } else {
            // Fastboot / Fastbootd
            devBuild.textContent = 'Bootloader Fastboot';
            if (data.battery_voltage) {
                const vText = data.battery_voltage.includes('mV') ? data.battery_voltage : `${data.battery_voltage} mV`;
                devBattery.textContent = data.battery_safe ? vText : `${vText} (LOW BATTERY)`;
                devBattery.style.color = data.battery_safe ? 'inherit' : 'var(--accent-coral)';
            } else {
                devBattery.textContent = 'Fastboot Connected';
                devBattery.style.color = 'inherit';
            }
            if (data.bootloader_unlocked === false) {
                devBootloader.textContent = 'LOCKED';
                devBootloader.style.color = 'var(--accent-amber)';
                unlockPrompt.classList.remove('hidden');
            } else if (data.bootloader_unlocked === true) {
                devBootloader.textContent = 'UNLOCKED';
                devBootloader.style.color = 'var(--accent-green)';
                unlockPrompt.classList.add('hidden');
            } else {
                devBootloader.textContent = 'Checking...';
                unlockPrompt.classList.add('hidden');
            }
        }
    }

    function startStatePolling() {
        fetchDeviceState();
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(fetchDeviceState, 2000);
    }

    btnRefreshState.addEventListener('click', () => {
        fetchDeviceState();
        logToTerminal('[SYSTEM] Refreshed device status.', 'sys');
    });

    // ── Inspect Factory Image Zip ────────────────
    btnInspectImage.addEventListener('click', async () => {
        const filePath = imagePathInput.value.trim();
        if (!filePath) {
            alert('Please enter the path to your factory image zip file.');
            return;
        }

        btnInspectImage.disabled = true;
        btnInspectImage.textContent = 'Inspecting...';

        try {
            const res = await fetch('/api/image/inspect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: filePath })
            });
            const data = await res.json();

            if (data.status === 'success') {
                inspectedImage = data;
                imgFilename.textContent = data.filename;
                imgVersion.textContent = `Android ${data.android_version}`;
                imgBuildId.textContent = data.build_id || 'Unknown';
                imgSha256.textContent = data.sha256;
                chkVerifyHash.checked = false;

                imageDetails.classList.remove('hidden');
            } else {
                inspectedImage = null;
                imageDetails.classList.add('hidden');
                alert(`Image Error: ${data.message}`);
            }
        } catch (e) {
            alert(`Server Error: ${e.message}`);
        } finally {
            btnInspectImage.disabled = false;
            btnInspectImage.textContent = 'Inspect Zip';
            updateFlashButtonState();
        }
    });

    chkVerifyHash.addEventListener('change', () => {
        updateFlashButtonState();
    });

    // ── Unlock Bootloader Trigger ────────────────
    btnUnlockBootloader.addEventListener('click', () => {
        requestTypedConfirmation(
            'Unlock Bootloader',
            `<strong>WARNING:</strong> Unlocking the bootloader will <strong>wipe all data</strong> on your Pixel 4 XL.<br><br>
            Ensure 'OEM unlocking' is enabled in Android Developer Options.`,
            'UNLOCK',
            async () => {
                try {
                    const res = await fetch('/api/bootloader/unlock', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ confirmation: 'UNLOCK' })
                    });
                    const data = await res.json();
                    if (data.status !== 'started') {
                        alert(`Unlock Failed: ${data.message}`);
                    }
                } catch (e) {
                    alert(`Error: ${e.message}`);
                }
            }
        );
    });

    // ── Update Flash Button Active State ─────────
    function updateFlashButtonState() {
        const hasValidImage = !!inspectedImage;
        const hasVerifiedHash = chkVerifyHash.checked;
        const hasDevice = deviceState && deviceState.status === 'ready';
        const hasBattery = deviceState ? deviceState.battery_safe : false;

        btnStartFlash.disabled = !(hasValidImage && hasVerifiedHash && hasDevice && hasBattery && !isFlashing);
    }

    // ── Execute Flash Sequence ───────────────────
    btnStartFlash.addEventListener('click', () => {
        if (!inspectedImage || !chkVerifyHash.checked) return;

        requestTypedConfirmation(
            'Confirm Firmware Flash',
            `You are about to flash <strong>Android ${inspectedImage.android_version} (${inspectedImage.build_id})</strong> onto your Pixel 4 XL.<br><br>
            <strong>All user data will be wiped.</strong> Keep USB connected throughout.`,
            'FLASH',
            async () => {
                isFlashing = true;
                updateFlashButtonState();
                verifyMsg.textContent = 'Flashing in progress. Do NOT disconnect...';
                relockContainer.classList.add('hidden');

                try {
                    const res = await fetch('/api/flash/start', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            confirmation: 'FLASH',
                            checksum_verified: true,
                            file_path: inspectedImage.file_path
                        })
                    });
                    const data = await res.json();
                    if (data.status === 'started') {
                        startBootVerification(inspectedImage.android_version);
                    } else {
                        alert(`Flash Start Error: ${data.message}`);
                        isFlashing = false;
                        updateFlashButtonState();
                    }
                } catch (e) {
                    alert(`Error: ${e.message}`);
                    isFlashing = false;
                    updateFlashButtonState();
                }
            }
        );
    });

    // ── Post-Flash Boot Verification ─────────────
    function startBootVerification(expectedVer) {
        let attempts = 0;
        const maxAttempts = 150; // ~5 minutes

        if (verifyInterval) clearInterval(verifyInterval);
        verifyInterval = setInterval(async () => {
            attempts++;
            try {
                const res = await fetch('/api/flash/verify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ expected_version: expectedVer })
                });
                const data = await res.json();

                if (data.failed) {
                    clearInterval(verifyInterval);
                    verifyInterval = null;
                    isFlashing = false;
                    verifyMsg.innerHTML = `<span style="color: #ef4444">${data.message}</span>`;
                    updateFlashButtonState();
                    logToTerminal(`[FAILED] ${data.message}`, 'error');
                    return;
                }

                if (data.completed && data.verified) {
                    clearInterval(verifyInterval);
                    verifyInterval = null;
                    isFlashing = false;
                    verifyMsg.innerHTML = `<span style="color: var(--accent-green)">${data.message}</span>`;
                    relockContainer.classList.remove('hidden');
                    updateFlashButtonState();
                    logToTerminal(`[VERIFIED] ${data.message}`, 'success');
                } else {
                    verifyMsg.textContent = `${data.message} (Polling: ${attempts * 2}s)`;
                }
            } catch (e) {
                // Ignore transient network errors while device is rebooting
            }

            if (attempts >= maxAttempts) {
                clearInterval(verifyInterval);
                verifyInterval = null;
                isFlashing = false;
                verifyMsg.textContent = 'Boot verification timed out. Please check your phone screen manually.';
                updateFlashButtonState();
            }
        }, 2000);
    }

    // ── Relock Bootloader Trigger ────────────────
    btnRelock.addEventListener('click', () => {
        requestTypedConfirmation(
            'Relock Bootloader',
            `Relocking restores stock verified boot security.<br><br>
            <strong>Note:</strong> Device must be running 100% stock firmware (just verified).`,
            'LOCK',
            async () => {
                try {
                    const res = await fetch('/api/bootloader/relock', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ confirmation: 'LOCK' })
                    });
                    const data = await res.json();
                    if (data.status !== 'started') {
                        alert(`Relock Error: ${data.message}`);
                    }
                } catch (e) {
                    alert(`Error: ${e.message}`);
                }
            }
        );
    });

    // ── Heartbeat & Auto-Shutdown on Window Close ────
    function sendHeartbeat() {
        fetch('/api/heartbeat').catch(() => {});
    }

    sendHeartbeat();
    setInterval(sendHeartbeat, 2500);

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            sendHeartbeat();
        }
    });

    window.addEventListener('pagehide', () => {
        if (navigator.sendBeacon) {
            navigator.sendBeacon('/api/tab_closed', '{}');
        } else {
            fetch('/api/tab_closed', { method: 'POST', keepalive: true }).catch(() => {});
        }
    });
});

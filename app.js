// ─────────────────────────────────────────────
// OmniFlash — Pixel 4 XL (coral) Flasher
// Frontend Application Logic
// ─────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // ── Helper ───────────────────────────────────
    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

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

    // ── Batch APK Installer Elements ─────────────
    const apkPathInput = $('apk-path-input');
    const btnScanApks = $('btn-scan-apks');
    const apkDetailsBox = $('apk-details-box');
    const apkCountBadge = $('apk-count-badge');
    const btnToggleAllApks = $('btn-toggle-all-apks');
    const apkItemsList = $('apk-items-list');
    const apkModeWarning = $('apk-mode-warning');
    const btnInstallApks = $('btn-install-apks');
    let discoveredApks = [];
    let isInstallingApks = false;

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
            updateApkInstallButtonState();
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
            devBattery.style.color = data.battery_safe ? 'inherit' : 'var(--accent-red)';
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
                devBattery.style.color = data.battery_safe ? 'inherit' : 'var(--accent-red)';
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
            `You are about to flash <strong>Android ${esc(inspectedImage.android_version)} (${esc(inspectedImage.build_id)})</strong> onto your Pixel 4 XL.<br><br>
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
    // ARCHITECTURAL NOTE:
    // After fastboot sends 'fastboot reboot', the device enters Android initial boot sequence
    // (first-time boot decryption, DEX compilation, and sys.boot_completed signal).
    // This process can take between 90s to 240s.
    // We poll /api/flash/verify every 2 seconds:
    //   - If the background flash worker logged an exit code != 0, it returns 'failed: true' immediately,
    //     canceling the interval and stopping false infinite polling.
    //   - Once ADB reconnects and reports sys.boot_completed == 1 with matching ro.build.version.release,
    //     the UI marks the flash as 100% verified and unlocks the optional bootloader relock button.
    function startBootVerification(expectedVer) {
        let attempts = 0;
        const maxAttempts = 150; // ~5 minutes timeout window

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

                // If backend worker reported failure (e.g. fastboot command error or USB drop)
                if (data.failed) {
                    clearInterval(verifyInterval);
                    verifyInterval = null;
                    isFlashing = false;
                    verifyMsg.innerHTML = `<span style="color: #ef4444">${data.message}</span>`;
                    updateFlashButtonState();
                    logToTerminal(`[FAILED] ${data.message}`, 'error');
                    return;
                }

                // Successful boot completion & OS release verification
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
                // Ignore transient network errors while server is processing USB events or rebooting
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

    // ── Batch APK Installer Logic ───────────────
    function updateApkInstallButtonState() {
        if (isFlashing || isInstallingApks) {
            btnInstallApks.disabled = true;
            return;
        }

        const isAndroidMode = deviceState && deviceState.status === 'ready' && deviceState.mode === 'android';
        if (!isAndroidMode) {
            apkModeWarning.classList.remove('hidden');
            btnInstallApks.disabled = true;
            return;
        }

        apkModeWarning.classList.add('hidden');
        const checkedCount = document.querySelectorAll('.chk-apk-item:checked').length;
        btnInstallApks.disabled = (checkedCount === 0);
        btnInstallApks.textContent = checkedCount > 0 
            ? `Install ${checkedCount} Selected Package${checkedCount > 1 ? 's' : ''} Over ADB`
            : 'Select Packages to Install';
    }

    btnScanApks.addEventListener('click', async () => {
        const pathVal = apkPathInput.value.trim();
        if (!pathVal) {
            alert('Please specify a directory or APK file path.');
            return;
        }

        btnScanApks.disabled = true;
        btnScanApks.textContent = 'Scanning...';
        logToTerminal(`[SCAN] Searching for installable APKs in: ${pathVal}...`, 'sys');

        try {
            const res = await fetch('/api/apk/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: pathVal })
            });
            const data = await res.json();

            if (data.status !== 'ok') {
                alert(`Scan Error: ${data.message}`);
                apkDetailsBox.classList.add('hidden');
                discoveredApks = [];
                updateApkInstallButtonState();
                logToTerminal(`[SCAN] ${data.message}`, 'error');
                return;
            }

            discoveredApks = data.items;
            apkCountBadge.textContent = data.count;
            apkItemsList.innerHTML = '';

            data.items.forEach((item, index) => {
                const row = document.createElement('div');
                row.className = 'apk-item-row';
                row.innerHTML = `
                    <div class="apk-item-left">
                        <input type="checkbox" class="chk-apk-item" id="apk-chk-${index}" data-path="${esc(item.file_path)}" checked>
                        <div class="apk-item-info">
                            <label for="apk-chk-${index}" class="apk-item-name" title="${esc(item.filename)}">${esc(item.filename)}</label>
                            <span class="apk-item-meta font-mono">${item.size_mb} MB &bull; ${item.type === 'bundle' ? 'Split Bundle (.apkm/.apks)' : 'Single APK'}</span>
                        </div>
                    </div>
                    <div class="apk-item-right">
                        <span class="badge ${item.type === 'bundle' ? 'badge-yellow' : 'badge-cyan'}">${item.type === 'bundle' ? 'BUNDLE' : 'APK'}</span>
                    </div>
                `;
                apkItemsList.appendChild(row);
            });

            apkDetailsBox.classList.remove('hidden');
            logToTerminal(`[SCAN] Discovered ${data.count} package(s) ready to install.`, 'success');

            // Wire up checkbox change listeners
            document.querySelectorAll('.chk-apk-item').forEach(chk => {
                chk.addEventListener('change', updateApkInstallButtonState);
            });

            updateApkInstallButtonState();

        } catch (e) {
            alert(`Error: ${e.message}`);
            logToTerminal(`[SCAN] Exception: ${e.message}`, 'error');
        } finally {
            btnScanApks.disabled = false;
            btnScanApks.textContent = 'Scan APKs';
        }
    });

    btnToggleAllApks.addEventListener('click', () => {
        const checkboxes = document.querySelectorAll('.chk-apk-item');
        if (checkboxes.length === 0) return;
        const anyUnchecked = Array.from(checkboxes).some(c => !c.checked);
        checkboxes.forEach(c => c.checked = anyUnchecked);
        btnToggleAllApks.textContent = anyUnchecked ? 'Deselect All' : 'Select All';
        updateApkInstallButtonState();
    });

    btnInstallApks.addEventListener('click', async () => {
        const selectedPaths = Array.from(document.querySelectorAll('.chk-apk-item:checked')).map(c => c.dataset.path);
        if (selectedPaths.length === 0) return;

        if (!deviceState || deviceState.status !== 'ready' || deviceState.mode !== 'android') {
            alert('Device must be booted in Android mode with USB Debugging enabled.');
            return;
        }

        isInstallingApks = true;
        updateApkInstallButtonState();

        try {
            const res = await fetch('/api/apk/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ files: selectedPaths })
            });
            const data = await res.json();
            if (data.status !== 'started') {
                alert(`Install Error: ${data.message}`);
                isInstallingApks = false;
                updateApkInstallButtonState();
            } else {
                // Poll until operation finishes
                let installPollCount = 0;
                const checkInterval = setInterval(async () => {
                    installPollCount++;
                    try {
                        const st = await (await fetch('/api/device/state')).json();
                        if (st.status !== 'busy') {
                            clearInterval(checkInterval);
                            isInstallingApks = false;
                            updateApkInstallButtonState();
                            if (st.status === 'disconnected') {
                                logToTerminal('[WARNING] Device disconnected during APK installation.', 'warn');
                            }
                        }
                    } catch (e) {}
                    if (installPollCount >= 600) {
                        clearInterval(checkInterval);
                        isInstallingApks = false;
                        updateApkInstallButtonState();
                        logToTerminal('[WARNING] APK installation poll timed out.', 'warn');
                    }
                }, 1500);
            }
        } catch (e) {
            alert(`Error: ${e.message}`);
            isInstallingApks = false;
            updateApkInstallButtonState();
        }
    });

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

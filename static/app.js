(() => {
  const $ = (id) => document.getElementById(id);

  const pillDeepface = $("pill-deepface");
  const pillEnrolled = $("pill-enrolled");
  const pillScene = $("pill-scene");
  const idleCount = $("idle-count");
  const idleFill = $("idle-fill");
  const enrollMsg = $("enroll-msg");
  const engineLine = $("engine-line");

  const notifToast = $("notif-toast");
  const notifIcon = $("notif-icon");
  const notifText = $("notif-text");
  let notifTimer = null;

  const btnCaptureEnroll = $("btn-capture-enroll");
  const fileInp = $("file-inp");
  const btnClear = $("btn-clear");

  const btnToggle = $("btn-toggle");
  const btnToggle2 = $("btn-toggle2");
  const btnVoice = $("btn-voice");
const btnMicMute = $("btn-mic-mute");
const micMutedRow = $("mic-muted-row");
  const btnIntruderWarning = $("btn-intruder-warning");
  const camSelect = $("cam-select");
  const camManual = $("cam-manual");
  const btnCamGo = $("btn-cam-go");
  const btnRefreshCam = $("btn-refresh-cam");
  const camUrl = $("cam-url");
  const btnCamUrl = $("btn-cam-url");

  const camStatusEl = $("cam-status");
  const micStatusEl = $("mic-status");
  const micErrorRow = $("mic-error-row");
  const micErrorEl = $("mic-error");
  const voiceMsg = $("voice-msg");
  const voiceLastEl = $("voice-last");

  const inpPassword = $("inp-password");
  const btnSetPassword = $("btn-set-password");
  const btnClearPassword = $("btn-clear-password");
  const pwdMsg = $("pwd-msg");
  const pwdClearRow = $("pwd-clear-row");

  const btnZoomIn = $("btn-zoom-in");
  const btnZoomOut = $("btn-zoom-out");
  const zoomLabel = $("zoom-label");

  let currentZoom = 1.0;

  let ws;

  let cameraActive = false;
const fcCanvas = $("face-canvas");
const fcCtx = fcCanvas ? fcCanvas.getContext("2d") : null;
const fcBadge = $("face-count-badge");
let faceBoxes = [];
  const micEq = $("mic-eq");
  const micEqBars = micEq ? Array.from(micEq.children) : [];

  function setMicLevel(level) {
    if (!micEqBars.length) return;
    const t = performance.now() / 1000;
    // Each bar bobs on its own phase so the meter feels alive, and the
    // overall scale follows the real microphone level from the backend.
    const scale = Math.max(0.03, Math.min(1, Number(level) || 0));
    for (let i = 0; i < micEqBars.length; i++) {
      const bob = 0.55 + 0.45 * Math.sin(t * 6 + i * 1.05);
      const h = Math.max(4, scale * bob * 100);
      micEqBars[i].style.transform = "scaleY(" + h / 100 + ")";
    }
  }

  function flashMic(on) {
    const el = micStatusEl;
    if (!el) return;
    el.classList.toggle("voice-flash", on);
    if (micEq) micEq.classList.toggle("voice-flash", on);
  }

  function setVoiceLast(text) {
    if (!voiceLastEl) return;
    voiceLastEl.classList.remove("off");
    voiceLastEl.textContent = text;
    voiceLastEl.classList.add("voice-last-pop");
    clearTimeout(setVoiceLast._t);
    setVoiceLast._t = setTimeout(() => {
      voiceLastEl.classList.remove("voice-last-pop");
      voiceLastEl.classList.add("off");
    }, 2600);
  }

  function applyEnabledUI(enabled) {
    const setBtn = (btn) => {
      if (!btn) return;
      btn.textContent = enabled ? "ON" : "OFF";
      btn.classList.toggle("toggle-on", enabled);
      btn.classList.toggle("toggle-off", !enabled);
    };
    setBtn(btnToggle);
    setBtn(btnToggle2);
  }

  function showNotification(text, type) {
    if (notifTimer) clearTimeout(notifTimer);
    notifIcon.textContent = type === "match" ? "✓" : type === "lock" ? "!" : type === "voice" ? "⌘" : "i";
    notifText.textContent = text;
    notifToast.className = "notif-toast notif-" + type;
    notifToast.classList.remove("hidden");
    // Every notification auto-dismisses (lock toasts included) so a stale
    // "Lock screen triggered!" never stays stuck on screen after the owner
    // returns or monitoring resumes.
    notifTimer = setTimeout(() => {
      notifToast.classList.add("hidden");
    }, 3500);
  }

  function hideNotification() {
    if (notifTimer) clearTimeout(notifTimer);
    notifToast.classList.add("hidden");
  }

  function setScenePill(scene, guardActive, verifyLeft) {
    var label, cls;
    if (scene === "owner") {
      label = "You are here (guard OFF)";
      cls = "pill-ok";
    } else if (scene === "armed") {
      label = guardActive ? "GUARD ACTIVE — watching" : "Armed";
      cls = "pill-warn";
    } else if (scene === "stranger") {
      label = "INTRUDER — locking!";
      cls = "pill-bad";
    } else if (scene === "verifying") {
      label = "Unknown face — reading " + (verifyLeft || "?") + "s";
      cls = "pill-warn";
    } else if (scene === "nobody") {
      label = "Nobody";
      cls = "pill-muted";
    } else if (scene === "no_camera") {
      label = "Camera issue";
      cls = "pill-muted";
    } else if (scene === "disabled") {
      label = "Monitoring OFF";
      cls = "pill-muted";
    } else if (scene === "locked") {
      label = "LOCKED — camera off";
      cls = "pill-bad";
    } else {
      label = scene;
      cls = "pill-muted";
    }
    pillScene.textContent = label;
    pillScene.classList.remove("pill-bad", "pill-warn", "pill-ok", "pill-muted");
    pillScene.classList.add(cls);
  }

  function applyStatus(s) {
    if (s.deepface_ok) {
      pillDeepface.textContent = "AI core ready";
      pillDeepface.classList.add("pill-ok");
      pillDeepface.classList.remove("pill-muted", "pill-bad");
    } else {
      pillDeepface.textContent = "AI core offline";
      pillDeepface.classList.add("pill-bad");
      pillDeepface.classList.remove("pill-muted", "pill-ok");
    }

    if (s.enrolled) {
      pillEnrolled.textContent = "Face enrolled";
      pillEnrolled.classList.add("pill-ok");
      pillEnrolled.classList.remove("pill-muted");
    } else {
      pillEnrolled.textContent = "Not enrolled";
      pillEnrolled.classList.remove("pill-ok");
      pillEnrolled.classList.add("pill-muted");
    }

    setScenePill(s.scene || "…", s.guard_active, s.verify_left);

    if (s.enabled != null) {
      applyEnabledUI(s.enabled);
    }

    if (s.voice_enabled != null) {
      const on = !!s.voice_enabled;
      btnVoice.textContent = on ? "ON" : "OFF";
      btnVoice.classList.toggle("toggle-on", on);
      btnVoice.classList.toggle("toggle-off", !on);
      if (voiceMsg && !voiceMsg.classList.contains("err")) {
        voiceMsg.textContent = on
          ? "Microphone listening for voice commands."
          : "Voice control is OFF.";
      }
    }

    if (s.mic_muted != null) {
      const muted = !!s.mic_muted;
      btnMicMute.textContent = muted ? "UNMUTE" : "MUTE";
      btnMicMute.classList.toggle("toggle-danger", muted);
      btnMicMute.classList.toggle("toggle-off", !muted);
      if (micMutedRow) micMutedRow.style.display = muted ? "flex" : "none";
      if (voiceMsg && !voiceMsg.classList.contains("err")) {
        voiceMsg.textContent = muted
          ? "Microphone is MUTED - the AI hears NO ONE. Click UNMUTE to let the AI listen for voice commands again."
          : (s.voice_enabled ? "Microphone listening for voice commands." : "Voice control is OFF.");
      }
    }

    if (s.camera_active != null) {
      const wasActive = cameraActive;
      cameraActive = !!s.camera_active;
      camStatusEl.textContent = cameraActive ? "ON" : "OFF";
      camStatusEl.classList.toggle("on", cameraActive);
      camStatusEl.classList.toggle("off", !cameraActive);
      const img = $("preview-img");
      if (img) {
        if (!cameraActive) {
          img.src = "";
        } else if (!wasActive) {
          // Just turned on - seed the first frame (the interval pump with
          // preload-swap takes over from here, so no tearing on updates).
          img.src = "/api/preview?" + Date.now();
        }
      }
    }

    if (s.face_boxes) {
      faceBoxes = s.face_boxes;
      if (fcBadge) {
        fcBadge.textContent = faceBoxes.length + (faceBoxes.length === 1 ? " face" : " faces");
        fcBadge.style.display = cameraActive && faceBoxes.length > 0 ? "block" : "none";
      }
    } else if (fcBadge) {
      faceBoxes = [];
      fcBadge.style.display = "none";
    }

    if (s.mic_active != null) {
      micStatusEl.textContent = s.mic_active ? "ACTIVE" : "INACTIVE";
      micStatusEl.classList.toggle("on", s.mic_active);
      micStatusEl.classList.toggle("off", !s.mic_active);
      const row = $("mic-eq-row");
      if (row) row.style.display = s.mic_active ? "flex" : "none";
    }

    if (s.mic_level != null) {
      setMicLevel(s.mic_level);
    }
    if (s.mic_flash != null) {
      flashMic(!!s.mic_flash);
    }

    if (s.mic_error) {
      micErrorEl.textContent = s.mic_error;
      micErrorRow.style.display = "block";
      if (voiceMsg) {
        voiceMsg.textContent = "Microphone problem - " + s.mic_error;
        voiceMsg.classList.add("err");
      }
    } else {
      micErrorRow.style.display = "none";
      if (voiceMsg) voiceMsg.classList.remove("err");
    }

    if (s.voice_event === "locked") {
      showNotification("\"Esta lock PC\" heard — locking the PC", "lock");
      setVoiceLast("Heard: \"Esta lock PC\" → locking PC");
    } else if (s.voice_event === "activated") {
      showNotification("Security Mode activated by voice", "voice");
      setVoiceLast("Heard: \"camera on\" → Security Mode ON");
    } else if (s.voice_event === "deactivated") {
      showNotification("Security Mode deactivated by voice", "voice");
      setVoiceLast("Heard: \"camera off\" → Security Mode OFF");
    } else if (s.voice_event === "voice_on") {
      showNotification("Voice control turned ON by voice", "voice");
      setVoiceLast("Heard: \"voice on\" → Voice control ON");
    } else if (s.voice_event === "voice_off") {
      showNotification("Voice control turned OFF by voice", "voice");
      setVoiceLast("Heard: \"voice off\" → Voice control OFF");
    } else if (s.voice_event === "unknown") {
      showNotification("Not recognized - say \"camera on\", \"camera off\", \"voice on/off\", or \"Esta lock PC\"", "voice");
      setVoiceLast("Heard: unrecognized phrase");
    }

    if (s.intruder_warning != null) {
      if (s.intruder_warning) {
        btnIntruderWarning.textContent = "ON";
        btnIntruderWarning.classList.remove("toggle-off");
        btnIntruderWarning.classList.add("toggle-on");
      } else {
        btnIntruderWarning.textContent = "OFF";
        btnIntruderWarning.classList.remove("toggle-on");
        btnIntruderWarning.classList.add("toggle-off");
      }
    }

    if (s.face_match) {
      showNotification("Face match — welcome back!", "match");
    }

    if (s.scene === "armed" && s.guard_active) {
      idleCount.textContent = "GUARD ACTIVE";
      idleFill.style.width = "100%";
      idleFill.style.background = "linear-gradient(90deg, #fbbf24, #fb7185)";
    } else if (s.scene === "stranger") {
      idleCount.textContent = "LOCKING";
      idleFill.style.width = "100%";
      idleFill.style.background = "var(--red)";
      if (s.scene === "stranger") {
        showNotification("Lock screen triggered!", "lock");
      }
    } else if (s.scene === "verifying" && s.guard_active) {
      var secs = s.verify_seconds || 10;
      var remain = Math.max(0, Math.min(secs, s.verify_left || 0));
      idleCount.textContent = "Lock in " + remain + "s — unless it is you";
      idleFill.style.width = (remain / secs * 100) + "%";
      idleFill.style.background = "linear-gradient(90deg, #fb7185, var(--red))";
    } else {
      idleCount.textContent = "—";
      idleFill.style.width = "0%";
      idleFill.style.background = "linear-gradient(90deg, var(--cyan), var(--violet))";
    }

    if (engineLine) {
      var info = s.last_error || "";
      if (!info && s.camera_source != null) {
        var label = s.camera_name || "Camera " + s.camera_source;
        if (s.camera_source != null && camSelect) {
          var opt = camSelect.options[camSelect.selectedIndex];
          if (opt && opt.text && !opt.text.startsWith("Loading") && !opt.text.startsWith("No")) {
            label = opt.text;
          }
        }
        info = "Camera: " + label;
      }
      engineLine.textContent = info;
      engineLine.classList.toggle("err", !!s.last_error);
    }
  }

  let wsRetries = 0;
  function connectWs() {
    if (wsRetries > 30) {
      document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:1rem;color:#888;"><h2 style="color:#f44;">Server Offline</h2><p>The Python backend is not responding. Close and relaunch SecureCam.</p></div>';
      return;
    }
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onmessage = (ev) => {
      try {
        wsRetries = 0;
        applyStatus(JSON.parse(ev.data));
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => { wsRetries++; setTimeout(connectWs, 2000); };
    ws.onerror = () => ws.close();
  }

  async function postBlob(blob) {
    enrollMsg.textContent = "Saving encrypted embedding…";
    enrollMsg.classList.remove("ok", "err");
    const fd = new FormData();
    fd.append("file", blob, "enroll.jpg");
    const r = await fetch("/api/register", { method: "POST", body: fd });
    let j = {};
    try {
      j = await r.json();
    } catch {
      /* ignore */
    }
    const detail = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? j.detail[0]?.msg : "";
    if (!r.ok) {
      enrollMsg.textContent = detail || "Enrollment failed.";
      enrollMsg.classList.add("err");
      return;
    }
    enrollMsg.textContent = "Saved — monitoring uses this face on the next frames.";
    enrollMsg.classList.remove("err");
    enrollMsg.classList.add("ok");
  }

  btnCaptureEnroll.addEventListener("click", async () => {
    enrollMsg.textContent = "Hold still — capturing from the desk camera…";
    enrollMsg.classList.remove("ok", "err");
    try {
      const r = await fetch("/api/capture_register", { method: "POST" });
      let j = {};
      try {
        j = await r.json();
      } catch {
        /* ignore */
      }
      const detail = typeof j.detail === "string" ? j.detail : "";
      if (!r.ok) {
        enrollMsg.textContent = detail || "Capture failed.";
        enrollMsg.classList.add("err");
        return;
      }
      enrollMsg.textContent = "Face saved securely — you can step back and let it guard the desk.";
      enrollMsg.classList.remove("err");
      enrollMsg.classList.add("ok");
    } catch {
      enrollMsg.textContent = "Network error talking to SecureCam.";
      enrollMsg.classList.add("err");
    }
  });

  fileInp.addEventListener("change", async () => {
    const f = fileInp.files && fileInp.files[0];
    if (!f) return;
    await postBlob(f);
    fileInp.value = "";
  });

  btnClear.addEventListener("click", async () => {
    const r = await fetch("/api/register", { method: "DELETE" });
    if (r.ok) {
      enrollMsg.textContent = "Enrollment removed.";
      enrollMsg.classList.remove("err");
      enrollMsg.classList.add("ok");
    }
  });

  function updatePwdUI(hasPwd) {
    if (hasPwd) {
      pwdClearRow.style.display = "block";
    } else {
      pwdClearRow.style.display = "none";
    }
  }

  btnSetPassword.addEventListener("click", async () => {
    const pwd = inpPassword.value;
    if (!pwd || pwd.length < 4) {
      pwdMsg.textContent = "Password must be at least 4 characters.";
      pwdMsg.classList.add("err");
      pwdMsg.classList.remove("ok");
      return;
    }
    pwdMsg.textContent = "Saving password...";
    pwdMsg.classList.remove("ok", "err");
    try {
      const r = await fetch("/api/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pwd }),
      });
      const j = await r.json();
      if (!r.ok) {
        pwdMsg.textContent = j.detail || "Failed to set password.";
        pwdMsg.classList.add("err");
        return;
      }
      pwdMsg.textContent = "Lock screen password saved.";
      pwdMsg.classList.remove("err");
      pwdMsg.classList.add("ok");
      inpPassword.value = "";
      updatePwdUI(true);
    } catch {
      pwdMsg.textContent = "Network error.";
      pwdMsg.classList.add("err");
    }
  });

  btnClearPassword.addEventListener("click", async () => {
    try {
      const r = await fetch("/api/password", { method: "DELETE" });
      if (r.ok) {
        pwdMsg.textContent = "Password removed.";
        pwdMsg.classList.remove("err");
        pwdMsg.classList.add("ok");
        updatePwdUI(false);
      }
    } catch {
      pwdMsg.textContent = "Network error.";
      pwdMsg.classList.add("err");
    }
  });

  function handleToggleEnabled() {
    const currentlyOn = btnToggle.textContent === "ON";
    fetch("/api/enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !currentlyOn }),
    })
      .then((r) => r.json())
      .then((j) => {
        if (j.enabled != null) applyEnabledUI(j.enabled);
      })
      .catch(() => {});
  }

  btnToggle.addEventListener("click", handleToggleEnabled);
  btnToggle2.addEventListener("click", handleToggleEnabled);

  btnVoice.addEventListener("click", async () => {
    const currentlyOn = btnVoice.textContent === "ON";
    voiceMsg.classList.remove("ok", "err");
    voiceMsg.textContent = currentlyOn ? "Releasing microphone…" : "Starting microphone…";
    try {
      const r = await fetch("/api/voice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentlyOn }),
      });
      let j = {};
      try {
        j = await r.json();
      } catch {
        /* ignore */
      }
      if (!r.ok) {
        voiceMsg.textContent = typeof j.detail === "string" ? j.detail : "Could not change voice control.";
        voiceMsg.classList.add("err");
        return;
      }
      const on = j.voice_enabled != null ? j.voice_enabled : !currentlyOn;
      btnVoice.textContent = on ? "ON" : "OFF";
      btnVoice.classList.toggle("toggle-on", on);
      btnVoice.classList.toggle("toggle-off", !on);
      voiceMsg.textContent = on ? "Microphone listening for voice commands." : "Microphone released.";
      voiceMsg.classList.add("ok");
    } catch {
      voiceMsg.textContent = "Network error changing voice control.";
      voiceMsg.classList.add("err");
    }
  });

  btnMicMute.addEventListener("click", async () => {
    const currentlyMuted = btnMicMute.textContent === "UNMUTE";
    const muted = !currentlyMuted;
    voiceMsg.classList.remove("ok", "err");
    voiceMsg.textContent = muted
      ? "Muting microphone - the AI will hear NO ONE until you unmute."
      : "Unmuting microphone - the AI will listen for voice commands again.";
    try {
      const r = await fetch("/api/voice/mute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ muted }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Could not mute microphone.");
      const m = j.mic_muted != null ? !!j.mic_muted : muted;
      btnMicMute.textContent = m ? "UNMUTE" : "MUTE";
      btnMicMute.classList.toggle("toggle-danger", m);
      btnMicMute.classList.toggle("toggle-off", !m);
      if (micMutedRow) micMutedRow.style.display = m ? "flex" : "none";
      const on = j.voice_enabled != null ? !!j.voice_enabled : btnVoice.textContent === "ON";
      btnVoice.textContent = on ? "ON" : "OFF";
      btnVoice.classList.toggle("toggle-on", on);
      btnVoice.classList.toggle("toggle-off", !on);
      voiceMsg.textContent = m
        ? "Microphone is MUTED - the AI hears NO ONE. Click UNMUTE to let the AI listen for voice commands again."
        : "Microphone unmuted.";
      voiceMsg.classList.add("ok");
    } catch (e) {
      voiceMsg.textContent = e.message || "Network error changing microphone.";
      voiceMsg.classList.add("err");
    }
  });

  btnIntruderWarning.addEventListener("click", async () => {
    const currentlyOn = btnIntruderWarning.textContent === "ON";
    try {
      const r = await fetch("/api/intruder-warning", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !currentlyOn }),
      });
      const j = await r.json();
      if (j.intruder_warning != null) {
        btnIntruderWarning.textContent = j.intruder_warning ? "ON" : "OFF";
        btnIntruderWarning.classList.toggle("toggle-on", j.intruder_warning);
        btnIntruderWarning.classList.toggle("toggle-off", !j.intruder_warning);
      }
    } catch {
      /* ignore */
    }
  });

  async function refreshCamList() {
    try {
      const r = await fetch("/api/cameras");
      const j = await r.json();
      if (!j.devices || !j.devices.length) {
        camSelect.innerHTML = '<option value="">No cameras found</option>';
        return;
      }
      var html = '<option value="">Select a camera...</option>';
      for (var i = 0; i < j.devices.length; i++) {
        var d = j.devices[i];
        var sel = d.index === j.current ? " selected" : "";
        html += '<option value="' + d.index + '"' + sel + ">" + d.name + " (" + d.width + "x" + d.height + ")</option>";
      }
      camSelect.innerHTML = html;
      if (typeof j.current === "string" && j.current.startsWith("http")) {
        camUrl.value = j.current;
      }
    } catch {
      camSelect.innerHTML = '<option value="">Error loading cameras</option>';
    }
  }

  camSelect.addEventListener("change", async function () {
    var idx = parseInt(this.value, 10);
    if (isNaN(idx)) return;
    camManual.value = idx;
    try {
      await fetch("/api/camera", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: idx }),
      });
    } catch {
      /* ignore */
    }
  });

  btnRefreshCam.addEventListener("click", () => refreshCamList());

  btnCamGo.addEventListener("click", async function () {
    var idx = parseInt(camManual.value, 10);
    if (isNaN(idx) || idx < 0) return;
    try {
      await fetch("/api/camera", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: idx }),
      });
    } catch {
      /* ignore */
    }
  });

  camManual.addEventListener("keydown", async function (ev) {
    if (ev.key === "Enter") {
      btnCamGo.click();
    }
  });

  btnCamUrl.addEventListener("click", async function () {
    var url = camUrl.value.trim();
    if (!url) return;
    try {
      await fetch("/api/camera", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: url }),
      });
    } catch {
      /* ignore */
    }
  });

  camUrl.addEventListener("keydown", async function (ev) {
    if (ev.key === "Enter") {
      btnCamUrl.click();
    }
  });

  async function setZoom(z) {
    try {
      const r = await fetch("/api/zoom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zoom: z }),
      });
      const j = await r.json();
      if (j.zoom != null) {
        currentZoom = j.zoom;
        zoomLabel.textContent = j.zoom.toFixed(1) + "×";
      }
    } catch { /* ignore */ }
  }

  btnZoomIn.addEventListener("click", () => setZoom(Math.min(5, Math.round((currentZoom + 0.5) * 10) / 10)));
  btnZoomOut.addEventListener("click", () => setZoom(Math.max(1, Math.round((currentZoom - 0.5) * 10) / 10)));

  function drawFaceFx(t) {
    requestAnimationFrame(drawFaceFx);
    if (!fcCanvas || !fcCtx) return;
    const pImg = $("preview-img");
    if (pImg && pImg.clientWidth > 0) {
      if (fcCanvas.width !== pImg.clientWidth) fcCanvas.width = pImg.clientWidth;
      if (fcCanvas.height !== pImg.clientHeight) fcCanvas.height = pImg.clientHeight;
    }
    fcCtx.clearRect(0, 0, fcCanvas.width, fcCanvas.height);
    if (!cameraActive || !faceBoxes.length || !fcCanvas.width || !fcCanvas.height) return;
    const W = fcCanvas.width, H = fcCanvas.height;
    const sweep = (t % 1500) / 1500;
    for (let i = 0; i < faceBoxes.length; i++) {
      const b = faceBoxes[i];
      const x = b[0] * W, y = b[1] * H;
      const bw2 = Math.max(0, (b[2] - b[0]) * W), bh2 = Math.max(0, (b[3] - b[1]) * H);
      if (bw2 < 3 || bh2 < 3) continue;
      const pulse = 0.65 + 0.35 * Math.sin(t / 400 + i * 1.7);
      fcCtx.save();
      fcCtx.shadowColor = "rgba(34,211,238,0.85)";
      fcCtx.shadowBlur = 8 + 9 * pulse;
      fcCtx.strokeStyle = "rgba(34,211,238," + (0.5 + 0.5 * pulse) + ")";
      fcCtx.lineWidth = 1.5;
      fcCtx.strokeRect(x, y, bw2, bh2);
      // pulsing corner brackets - the "strip of line" around the face
      fcCtx.lineWidth = 3.5;
      fcCtx.strokeStyle = "#22d3ee";
      fcCtx.shadowBlur = 6;
      const len = Math.min(bw2, bh2) * 0.22;
      fcCtx.beginPath();
      fcCtx.moveTo(x, y + len); fcCtx.lineTo(x, y); fcCtx.lineTo(x + len, y);
      fcCtx.moveTo(x + bw2 - len, y); fcCtx.lineTo(x + bw2, y); fcCtx.lineTo(x + bw2, y + len);
      fcCtx.moveTo(x + bw2, y + bh2 - len); fcCtx.lineTo(x + bw2, y + bh2); fcCtx.lineTo(x + bw2 - len, y + bh2);
      fcCtx.moveTo(x + len, y + bh2); fcCtx.lineTo(x, y + bh2); fcCtx.lineTo(x, y + bh2 - len);
      fcCtx.stroke();
      // sweeping scanline strip that covers the face box top to bottom
      const sy = y + (0.05 + 0.9 * sweep) * bh2;
      const stripH = bh2 * 0.22;
      const grad = fcCtx.createLinearGradient(0, sy - stripH / 2, 0, sy + stripH / 2);
      grad.addColorStop(0, "rgba(34,211,238,0)");
      grad.addColorStop(0.5, "rgba(34,211,238,0.45)");
      grad.addColorStop(1, "rgba(34,211,238,0)");
      fcCtx.shadowBlur = 4;
      fcCtx.fillStyle = grad;
      fcCtx.fillRect(x, sy - stripH / 2, bw2, stripH);
      fcCtx.fillStyle = "rgba(74,222,128," + (0.5 + 0.5 * pulse) + ")";
      fcCtx.fillRect(x, sy - 1, bw2, 2);
      fcCtx.restore();
    }
  }
  requestAnimationFrame(drawFaceFx);

  connectWs();

  setInterval(() => {
    if (!cameraActive) return;
    var img = document.getElementById("preview-img");
    if (!img || img.dataset.loading === "1") return;
    // Preload the next frame off-screen and swap only when it is fully
    // decoded - swapping src directly mid-download is what caused the
    // torn / half-drawn "glitch" frames.
    var pre = new Image();
    img.dataset.loading = "1";
    pre.onload = () => { img.src = pre.src; img.dataset.loading = ""; };
    pre.onerror = () => { img.dataset.loading = ""; };
    pre.src = "/api/preview?" + Date.now();
  }, 250);

  fetch("/api/status")
    .then((r) => r.json())
    .then(applyStatus)
    .catch(() => {});

  fetch("/api/meta")
    .then((r) => r.json())
    .then((m) => { if (m.has_password != null) updatePwdUI(m.has_password); })
    .catch(() => {});

  setTimeout(() => refreshCamList(), 1000);
})();

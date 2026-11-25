/* Real-time Speech-to-Text — browser client (no build step, vanilla JS). */

(() => {
  "use strict";

  // ---- DOM ---------------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const startBtn = $("start-btn");
  const stopBtn = $("stop-btn");
  const langSel = $("language-hint");
  const statusEl = $("status");
  const alertEl = $("alert");
  const liveEl = $("live-transcript");
  const meterFill = $("meter-fill");
  const timerEl = $("timer");
  const connPill = $("conn-pill");
  const copyLiveBtn = $("copy-live-btn");
  const downloadLiveBtn = $("download-live-btn");

  const rowsEl = $("rows");
  const emptyEl = $("empty-state");
  const pageInfoEl = $("page-info");
  const prevBtn = $("prev-btn");
  const nextBtn = $("next-btn");
  const searchInput = $("search-input");
  const searchBtn = $("search-btn");

  const editDialog = $("edit-dialog");
  const editForm = $("edit-form");
  const editTitle = $("edit-title");
  const editText = $("edit-text");
  const editCancel = $("edit-cancel");
  const editSaveBtn = editForm.querySelector('button[type="submit"]');
  const toastEl = $("toast");

  // ---- Recording state ---------------------------------------------------
  let ws = null;
  let wsReady = false;
  let reconnectDelay = 1000;
  let reconnectTimer = null;

  let audioCtx = null;
  let mediaStream = null;
  let sourceNode = null;
  let workletNode = null;
  let analyser = null;
  let silentGain = null;

  let recording = false;
  let liveText = "";
  let elapsed = 0;
  let timerId = null;
  let meterRaf = null;

  // ---- Dashboard state ---------------------------------------------------
  let page = 1;
  const pageSize = 10;
  let search = "";
  let pages = 1;
  let editingId = null;

  // How many trailing words stay at full strength in the live view.
  const LATEST_WORDS = 14;

  // ---- WebSocket ---------------------------------------------------------
  const wsUrl =
    (location.protocol === "https:" ? "wss://" : "ws://") +
    location.host +
    "/ws/transcribe";

  function setPill(kind, label) {
    connPill.className = "pill pill-" + kind;
    connPill.textContent = label;
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    setPill("busy", "Connecting");
    ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      wsReady = true;
      reconnectDelay = 1000;
      setPill("online", "Connected");
    };

    ws.onclose = () => {
      wsReady = false;
      setPill("idle", "Offline");
      if (recording) {
        showAlert("Connection to the server was lost. Recording stopped.", "error");
        stopRecording(true);
      }
      scheduleReconnect();
    };

    ws.onerror = () => {
      if (!wsReady) setPill("idle", "Offline");
    };

    ws.onmessage = (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      handleMessage(msg);
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.6, 15000);
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "status":
        setStatus(friendlyStatus(msg.state, msg.message));
        if (msg.state === "listening") setPill("online", "Recording");
        if (msg.state === "idle") setPill("online", "Connected");
        break;
      case "partial":
        liveText = msg.text || "";
        renderLive();
        break;
      case "final":
        liveText = msg.text || "";
        renderLive();
        break;
      case "saved":
        if (msg.transcription) {
          liveText = msg.transcription.text;
          renderLive();
        }
        toast("Recording saved.");
        loadDashboard();
        break;
      case "error":
        showAlert(msg.message || "Something went wrong.", "error");
        break;
      case "pong":
        break;
      default:
        break;
    }
  }

  // ---- UI helpers --------------------------------------------------------
  // Server states are translated into plain-language copy for the reader.
  function friendlyStatus(state, message) {
    switch (state) {
      case "connecting":
        return "Connecting to Gemini…";
      case "listening":
        return "Listening. Speak in Hindi, English, or mix the two.";
      case "reconnecting":
        return "The connection dropped. Reconnecting…";
      case "saving":
        return "Saving your recording…";
      case "stopping":
        return "Finishing up…";
      case "idle":
        return message && message.indexOf("No speech") === 0
          ? "Nothing was recorded — no speech was detected."
          : "Recording saved.";
      default:
        return message || "";
    }
  }

  function setStatus(text) {
    statusEl.textContent = text;
  }

  function showAlert(message, kind = "error") {
    alertEl.textContent = message;
    alertEl.className = "alert" + (kind === "info" ? " info" : "");
    alertEl.hidden = false;
  }

  function clearAlert() {
    alertEl.hidden = true;
    alertEl.textContent = "";
  }

  // The newest words read at full strength; everything before them recedes.
  function renderLive() {
    liveEl.textContent = "";

    if (liveText) {
      const tokens = liveText.split(/(\s+)/);
      let seen = 0;
      let cut = tokens.length;
      for (let i = tokens.length - 1; i >= 0; i--) {
        if (/\S/.test(tokens[i])) {
          seen += 1;
          if (seen > LATEST_WORDS) {
            cut = i + 1;
            break;
          }
        }
      }

      const older = tokens.slice(0, cut).join("");
      const latest = tokens.slice(cut).join("");

      if (older) {
        const olderSpan = document.createElement("span");
        olderSpan.className = "live-older";
        olderSpan.textContent = older;
        liveEl.appendChild(olderSpan);
      }
      if (latest) {
        const latestSpan = document.createElement("span");
        latestSpan.className = "live-latest";
        latestSpan.textContent = latest;
        liveEl.appendChild(latestSpan);
      }
    }

    // A caret only exists while there is something to follow.
    if (liveText || recording) {
      const caret = document.createElement("span");
      caret.className = "live-caret";
      caret.setAttribute("aria-hidden", "true");
      liveEl.appendChild(caret);
    }

    const has = liveText.trim().length > 0;
    copyLiveBtn.disabled = !has;
    downloadLiveBtn.disabled = !has;
  }

  let toastTimer = null;
  function toast(message) {
    toastEl.textContent = message;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toastEl.hidden = true;
    }, 2500);
  }

  function updateButtons() {
    startBtn.disabled = recording;
    stopBtn.disabled = !recording;
    langSel.disabled = recording;
    liveEl.classList.toggle("recording", recording);
  }

  function formatTimer(seconds) {
    const m = String(Math.floor(seconds / 60)).padStart(2, "0");
    const s = String(seconds % 60).padStart(2, "0");
    return `${m}:${s}`;
  }

  // ---- Recording ---------------------------------------------------------
  async function startRecording() {
    clearAlert();
    liveText = "";
    renderLive();

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showAlert("This browser does not support microphone capture.");
      return;
    }
    if (!window.AudioWorkletNode) {
      showAlert("This browser does not support AudioWorklet. Try Chrome or Edge.");
      return;
    }

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      const name = err && err.name;
      if (name === "NotAllowedError" || name === "SecurityError") {
        showAlert(
          "Microphone access was blocked. Allow it in your browser settings, then try again — the page also needs HTTPS or localhost."
        );
      } else if (name === "NotFoundError") {
        showAlert("No microphone was found on this device.");
      } else {
        showAlert("Could not access the microphone: " + (name || err));
      }
      return;
    }

    // Make sure the socket is open before we start streaming audio.
    if (!wsReady) {
      connect();
      for (let i = 0; i < 40 && !wsReady; i++) {
        await sleep(100);
      }
      if (!wsReady) {
        showAlert("Could not reach the transcription server. Is it running?");
        releaseMedia();
        return;
      }
    }

    try {
      audioCtx = createAudioContext();
      await audioCtx.audioWorklet.addModule("/static/pcm-worklet.js");

      sourceNode = audioCtx.createMediaStreamSource(mediaStream);
      workletNode = new AudioWorkletNode(audioCtx, "pcm16-worklet", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        channelCount: 1,
      });

      workletNode.port.onmessage = (event) => {
        if (recording && ws && ws.readyState === WebSocket.OPEN) {
          ws.send(event.data);
        }
      };

      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 1024;

      silentGain = audioCtx.createGain();
      silentGain.gain.value = 0; // never play the mic back

      sourceNode.connect(analyser);
      sourceNode.connect(workletNode);
      workletNode.connect(silentGain);
      silentGain.connect(audioCtx.destination);
    } catch (err) {
      showAlert("Audio setup failed: " + (err && err.message ? err.message : err));
      releaseMedia();
      return;
    }

    ws.send(JSON.stringify({ type: "start", language_hint: langSel.value }));

    recording = true;
    elapsed = 0;
    timerEl.textContent = formatTimer(0);
    timerId = setInterval(() => {
      elapsed += 1;
      timerEl.textContent = formatTimer(elapsed);
    }, 1000);
    startMeter();
    updateButtons();
    setStatus("Listening. Speak in Hindi, English, or mix the two.");
  }

  function stopRecording(silent = false) {
    if (!recording) return;
    recording = false;

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
    }

    if (!silent) setStatus("Wrapping up…");
    stopMeter();
    clearInterval(timerId);
    timerId = null;
    releaseMedia();
    updateButtons();
    setPill("online", "Connected");
  }

  function releaseMedia() {
    try {
      if (mediaStream) mediaStream.getTracks().forEach((t) => t.stop());
    } catch {}
    try {
      if (sourceNode) sourceNode.disconnect();
      if (workletNode) workletNode.disconnect();
      if (silentGain) silentGain.disconnect();
    } catch {}
    if (audioCtx && audioCtx.state !== "closed") {
      audioCtx.close().catch(() => {});
    }
    audioCtx = null;
    mediaStream = null;
    sourceNode = null;
    workletNode = null;
    analyser = null;
    silentGain = null;
  }

  function startMeter() {
    const data = new Uint8Array(analyser.fftSize);
    const tick = () => {
      if (!analyser) return;
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / data.length);
      const pct = Math.min(100, Math.round(rms * 220));
      meterFill.style.width = pct + "%";
      meterRaf = requestAnimationFrame(tick);
    };
    meterRaf = requestAnimationFrame(tick);
  }

  function stopMeter() {
    if (meterRaf) cancelAnimationFrame(meterRaf);
    meterRaf = null;
    meterFill.style.width = "0%";
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Ask for 16 kHz so the worklet has nothing to resample; fall back to the
  // hardware default if the browser refuses that rate (the worklet handles both).
  function createAudioContext() {
    try {
      return new AudioContext({ sampleRate: 16000, latencyHint: "interactive" });
    } catch {
      return new AudioContext();
    }
  }

  // ---- Dashboard ---------------------------------------------------------
  async function loadDashboard() {
    try {
      const url =
        `/api/transcriptions?page=${page}&page_size=${pageSize}` +
        (search ? `&search=${encodeURIComponent(search)}` : "");
      const res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      pages = Math.max(1, data.pages || 1);
      page = Math.min(page, pages);
      renderRows(data.items || []);

      const total = data.total || 0;
      if (total === 0) {
        pageInfoEl.textContent = "No recordings";
      } else {
        const from = (data.page - 1) * data.page_size + 1;
        const to = Math.min(data.page * data.page_size, total);
        pageInfoEl.textContent = `${from} to ${to} of ${total}`;
      }

      prevBtn.disabled = data.page <= 1;
      nextBtn.disabled = data.page >= pages;
    } catch (err) {
      showAlert("Could not load transcriptions: " + err.message);
    }
  }

  function renderRows(items) {
    rowsEl.textContent = "";
    emptyEl.hidden = items.length > 0;

    for (const item of items) {
      const tr = document.createElement("tr");

      const titleTd = document.createElement("td");
      titleTd.textContent = item.title;
      tr.appendChild(titleTd);

      const previewTd = document.createElement("td");
      previewTd.className = "preview";
      previewTd.textContent = truncate(item.text, 140);
      tr.appendChild(previewTd);

      const langTd = document.createElement("td");
      langTd.textContent = item.language_hint || "—";
      tr.appendChild(langTd);

      const dateTd = document.createElement("td");
      dateTd.textContent = formatDate(item.created_at);
      tr.appendChild(dateTd);

      const actionsTd = document.createElement("td");
      actionsTd.className = "col-actions";
      actionsTd.appendChild(button("View", () => openDialog(item, true)));
      actionsTd.appendChild(button("Edit", () => openDialog(item, false)));
      actionsTd.appendChild(button("Copy", () => copyText(item.text)));
      actionsTd.appendChild(button("Delete", () => deleteItem(item), "danger"));
      tr.appendChild(actionsTd);

      rowsEl.appendChild(tr);
    }
  }

  function button(label, onClick, variant) {
    const b = document.createElement("button");
    b.className = "btn " + (variant === "danger" ? "btn-danger" : "btn-ghost");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  function truncate(text, max) {
    const clean = (text || "").replace(/\s+/g, " ").trim();
    return clean.length > max ? clean.slice(0, max - 1) + "…" : clean;
  }

  function formatDate(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  }

  function openDialog(item, readonly) {
    editingId = item.id;
    editTitle.value = item.title || "";
    editText.value = item.text || "";
    editTitle.disabled = readonly;
    editText.disabled = readonly;
    editSaveBtn.hidden = readonly;
    editDialog.showModal();
  }

  async function saveEdit(event) {
    event.preventDefault();
    if (editingId == null) return;
    try {
      const res = await fetch(`/api/transcriptions/${editingId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: editTitle.value, text: editText.value }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      editDialog.close();
      toast("Changes saved.");
      loadDashboard();
    } catch (err) {
      showAlert("Update failed: " + err.message);
    }
  }

  async function deleteItem(item) {
    if (!confirm(`Delete this recording?\n\n“${item.title}” will be permanently removed.`)) return;
    try {
      const res = await fetch(`/api/transcriptions/${item.id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) throw new Error("HTTP " + res.status);
      toast("Recording deleted.");
      loadDashboard();
    } catch (err) {
      showAlert("Delete failed: " + err.message);
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Transcript copied.");
    } catch {
      showAlert("Copying to the clipboard needs HTTPS. You can download the file instead.");
    }
  }

  function downloadText(text) {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "transcript-" + new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-") + ".txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // ---- Events ------------------------------------------------------------
  startBtn.addEventListener("click", startRecording);
  stopBtn.addEventListener("click", () => stopRecording(false));
  copyLiveBtn.addEventListener("click", () => copyText(liveText));
  downloadLiveBtn.addEventListener("click", () => downloadText(liveText));

  searchBtn.addEventListener("click", () => {
    search = searchInput.value.trim();
    page = 1;
    loadDashboard();
  });
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      search = searchInput.value.trim();
      page = 1;
      loadDashboard();
    }
  });
  searchInput.addEventListener("search", () => {
    if (searchInput.value === "") {
      search = "";
      page = 1;
      loadDashboard();
    }
  });

  prevBtn.addEventListener("click", () => {
    if (page > 1) {
      page -= 1;
      loadDashboard();
    }
  });
  nextBtn.addEventListener("click", () => {
    if (page < pages) {
      page += 1;
      loadDashboard();
    }
  });

  editForm.addEventListener("submit", saveEdit);
  editCancel.addEventListener("click", () => editDialog.close());

  window.addEventListener("beforeunload", () => {
    if (recording && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
    }
  });

  // ---- Boot --------------------------------------------------------------
  connect();
  setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 20000);
  updateButtons();
  loadDashboard();
})();

"use strict";
  // ═════════════════════════════════════════════════════════
  // 导航与状态应用
  // ═════════════════════════════════════════════════════════
  function setPage(next) {
    if (page === "log" && next !== "log") selectedLogFile = "";
    page = next;
    for (const btn of $$(".nav-btn")) btn.classList.toggle("active", btn.dataset.nav === next);
    for (const id of ["home", "log", "settings"]) {
      $(`#page-${id}`).classList.toggle("hidden", id !== next);
    }
    if (next === "log") {
      loadLogFiles().finally(loadLogs);
    }
    if (next === "home") syncHome();
    if (next === "settings") syncSettings();
  }

  function applyState(next) {
    const json = JSON.stringify(next);
    if (json === lastStateJson) return;
    lastStateJson = json;
    const prevOn = state?.speaker?.on;
    state = next;
    if (powerPending && prevOn !== undefined && state.speaker && state.speaker.on !== prevOn) {
      clearPowerPending();
    }
    // Updating hidden pages for every state event needlessly makes a burst of
    // controller events expensive. The destination page is refreshed once in
    // setPage(), so only repaint the page the user can currently see.
    if (page === "home") syncHome();
    else if (page === "settings") syncSettings();
  }

  function handleToast(msg) {
    if (msg.kind === "event") { showEventResult(msg); return; }
    if (msg.kind === "scan") {
      if (!dlg || msg.scan_id !== dlg.scanId) return;
      if (msg.state === "progress") {
        dlg.checked = Math.max(dlg.checked, Number(msg.checked) || 0);
      } else if (msg.state === "candidate" || msg.state === "complete") {
        dlg.found = [...dlg.found, ...(msg.devices || [])]
          .filter((d, i, a) => d.ip && a.findIndex(x => x.ip === d.ip) === i);
        if (msg.state === "complete") dlg.phase = "done";
      } else if (msg.state === "failed") {
        dlg.phase = "done";
        dlg.failed = msg.code === "scan_busy" ? t("scan_busy") : (msg.detail || t("scan_failed"));
      }
      syncDialog();
      return;
    }
    const powerAction = String(msg.action || "").toUpperCase();
    const powerPhase = String(msg.phase || "");
    const wakeStarted = powerAction === "WAKE" && powerPhase === "started";
    const wakeNeedsLiveState = powerAction === "WAKE" && powerPhase === "finished" && msg.success === true;
    if (powerPending && powerPendingTarget && wakeStarted) {
      powerPendingStage = "confirming";
      syncHome();
    }
    if (powerPending && powerPendingTarget && wakeNeedsLiveState) {
      powerPendingStage = "confirming";
      armPowerPendingTimeout(3000);
      syncHome();
    }
    if (msg.kind === "toast" && powerAction && powerPhase === "finished" && powerPending && !wakeNeedsLiveState) {
      clearPowerPending();
      syncHome();
    }
    const copy = localizedMessage(msg);
    const visualKind = msg.channel === "power" && powerAction.includes("STANDBY") ? "standby" : "";
    toast(msg.level, copy.title, copy.detail, msg.channel || "general", 3600, visualKind);
  }

  // ═════════════════════════════════════════════════════════
  // 启动 + 更新轮询
  // ═════════════════════════════════════════════════════════
  document.querySelector("nav").addEventListener("click", e => {
    const btn = e.target.closest("[data-nav]");
    if (btn) setPage(btn.dataset.nav);
  });

  // Keep this above WebApiServer._updates_since's 15 s long-poll hold so a
  // healthy idle connection always responds before the browser aborts it.
  // Do not pause this local long-poll when Chromium hides an occluded or
  // minimized WebView: it is the renderer heartbeat.  Python separately
  // pauses the expensive speaker poll when the native window is minimized.
  const UPDATE_REQUEST_TIMEOUT_MS = 20000;
  let updateCursor = 0, polling = false, updateRetryTimer = null, updateFailures = 0;
  function setControllerConnectionLost(lost) {
    const note = $("#connection-note");
    if (note) note.classList.toggle("hidden", !lost);
  }
  async function pollUpdates() {
    if (polling) return;
    polling = true;
    const controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, UPDATE_REQUEST_TIMEOUT_MS);
    try {
      const batch = await bridge.updates(updateCursor, controller.signal);
      updateFailures = 0;
      setControllerConnectionLost(false);
      updateCursor = batch.cursor;
      // A state is a snapshot, so applying every historical one is redundant.
      // Coalesce a batch to its latest state, while preserving all new logs and
      // notifications in order.
      let latestState = null, latestSettings = null;
      const messages = [];
      const lines = [];
      for (const item of batch.updates) {
        if (item.channel === "state") latestState = item.payload;
        else if (item.channel === "settings") latestSettings = item.payload;
        else if (item.channel === "toast") messages.push(item.payload);
        else if (item.channel === "log") lines.push(item.payload);
      }
      if (latestState || latestSettings) {
        applyState({
          ...(state || {}),
          ...(latestState ? JSON.parse(latestState) : {}),
          ...(latestSettings ? JSON.parse(latestSettings) : {}),
        });
      }
      for (const payload of messages) handleToast(JSON.parse(payload));
      for (const line of lines) appendLogLine(line);
    } catch (error) {
      if (error.name !== "AbortError" || timedOut) {
        updateFailures += 1;
        if (updateFailures >= 3) setControllerConnectionLost(true);
        updateRetryTimer = setTimeout(() => {
          updateRetryTimer = null;
          pollUpdates();
        }, 1000);
      }
    } finally {
      clearTimeout(timeout);
      polling = false;
      if (!updateRetryTimer) pollUpdates();
    }
  }
  function ensureUpdatePolling() {
    pollUpdates();
  }

  bridge.bootstrap().then(boot => {
    state = boot.state;
    updateCursor = Number(boot.cursor) || 0;
    uiLanguage = state?.settings?.general?.ui_language === "en" ? "en" : "zh";
    document.documentElement.lang = uiLanguage === "en" ? "en" : "zh-CN";
    lastStateJson = JSON.stringify(state);
    buildShell();
    syncHome();
    window.addEventListener("blur", () => finishVolumeDrag());
    document.addEventListener("keydown", e => {
      if (e.key === "Escape" && dlg) closeSpeakerDialog();
    });
    ensureUpdatePolling();
  }).catch(() => {
    $("#main").removeAttribute("aria-busy");
    $("#main").innerHTML = `<div class="boot-error"><h1>${t("controller_unavailable")}</h1><p>${t("controller_unavailable_desc")}</p></div>`;
  });


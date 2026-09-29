  "use strict";
  // ═════════════════════════════════════════════════════════
  // 常量与工具
  // ═════════════════════════════════════════════════════════
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
  const esc = s => String(s ?? "").replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c]));

  // UI 文案与 JSX 保持同一套中英文语义；语言本身由 config.json 持久化。
  let uiLanguage = "zh";
  const t = (key, ...args) => {
    let value = (TEXT[key] || [key, key])[uiLanguage === "en" ? 1 : 0];
    for (const arg of args) value = value.replace("$", String(arg));
    return value;
  };
  const localized = value => Array.isArray(value) ? value[uiLanguage === "en" ? 1 : 0] : value;

  const ICONS = {
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.7V21h14V9.7"/>',
    log: '<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5"/><path d="M8.5 12.5h7m-7 4h7"/>',
    settings: '<circle cx="12" cy="12" r="3.1"/><path d="M12 2.8v2.6m0 13.2v2.6M4.9 4.9l1.9 1.9m10.4 10.4 1.9 1.9M2.8 12h2.6m13.2 0h2.6M4.9 19.1l1.9-1.9M17.2 6.8l1.9-1.9"/>',
    power: '<path d="M12 2v10"/><path d="M7.2 5.8a8 8 0 1 0 9.6 0"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>',
    monitor: '<rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M8 21h8m-4-4v4"/>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    unlock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 7.7-1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4 6.5 6.5 0 0 0 20 14.5z"/>',
    laptop: '<rect x="4.5" y="5" width="15" height="10.5" rx="1.5"/><path d="M2 19h20"/>',
    monitorOff: '<rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M8 21h8m-4-4v4"/><path d="m5.5 6.5 13 8"/>',
    flask: '<path d="M9.5 3h5M10 3v6l-5.2 8.7A2 2 0 0 0 6.5 21h11a2 2 0 0 0 1.7-3.3L14 9V3"/><path d="M7.6 15h8.8"/>',
    chevronRight: '<path d="m9.5 6.5 5.5 5.5-5.5 5.5"/>',
    chevronDown: '<path d="m6.5 9.5 5.5 5.5 5.5-5.5"/>',
    check: '<path d="m5 12.5 5 5 9-10.5"/>',
    minus: '<path d="M6 12h12"/>',
    plus: '<path d="M12 6v12M6 12h12"/>',
    x: '<path d="m6 6 12 12M18 6 6 18"/>',
    volume: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15 9a4 4 0 0 1 0 6m3-9a8 8 0 0 1 0 12"/>',
    volumeX: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="m16 9 5 6m0-6-5 6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.6-4.6"/>',
    refresh: '<path d="M20 6v5h-5"/><path d="M19 11a7.5 7.5 0 1 0 .2 4"/>',
    folder: '<path d="M3 7.5h6l2 2h10v8.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M3 9.5V6a2 2 0 0 1 2-2h4l2 2h5"/>',
    rotateCcw: '<path d="M3 3v5h5"/><path d="M3.5 8A9 9 0 1 1 3 12"/>',
    alert: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4.5m0 3.2v.1"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8v.1"/>',
    loader: '<path d="M12 3a9 9 0 1 1-9 9"/>',
    wifi: '<path d="M3.5 9a13 13 0 0 1 17 0"/><path d="M6.5 12.5a8.5 8.5 0 0 1 11 0"/><path d="M9.7 16a3.5 3.5 0 0 1 4.6 0"/><circle cx="12" cy="19" r=".7" fill="currentColor" stroke="none"/>',
    bluetooth: '<path d="m8 7 8 10V3L8 13l8 8V7z"/>',
    tv: '<rect x="3" y="4" width="18" height="13" rx="1.5"/><path d="M8 21h8m-4-4v4"/>',
    optical: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4m8.4-8.4a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14m14-14a10 10 0 0 1 0 14"/>',
    coaxial: '<path d="M8 3v5a4 4 0 0 0 8 0V3M6 3h4v4H6zm8 0h4v4h-4zM12 12v9m0-4h6v4"/>',
    analog: '<path d="M7 5v14m10-14v14M3 9h4m10 6h4"/><circle cx="3" cy="9" r="1.5"/><circle cx="21" cy="15" r="1.5"/>',
    usb: '<path d="M12 3v13m0-10 4 4m-4 2-4 4m4 0a3 3 0 1 0 3 3"/><path d="m16 6-1-2h2z"/><circle cx="8" cy="16" r="1.3"/>',
  };
  const icon = (name, size = 15, cls = "") =>
    `<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  const INPUT_META = {
    wifi: ["Wi-Fi", "Wi-Fi"], bluetooth: ["蓝牙", "Bluetooth"], tv: ["TV (eARC)", "TV (eARC)"], optical: ["光纤", "Optical"],
    coaxial: ["同轴", "Coaxial"], analog: ["模拟", "Analog"], usb: ["USB", "USB"],
  };
  const INPUT_ORDER = ["wifi", "bluetooth", "tv", "optical", "coaxial", "analog", "usb"];
  const inputLabel = v => localized(INPUT_META[v] || [v, v]);

  // 电源行为矩阵：每行对应后端 config 字段与可模拟的事件
  const EVENT_ROWS = [
    { id: "app_start", label: ["App 启动", "App Startup"], desc: ["打开 KEF Controller 时", "When KEF Controller opens"], icon: "play",
      wake: "wake_on_startup", standby: null,
      tests: [{ key: "startup", label: ["启动", "Start"], col: "wake" }] },
    { id: "screen", label: ["屏幕开关", "Display Power"], desc: ["显示器点亮 / 熄灭时", "When the display turns on / off"], icon: "monitor",
      wake: "wake_on_display_on", standby: "standby_on_display_off",
      tests: [{ key: "display-off", label: ["熄屏", "Off"], col: "standby" }, { key: "display-on", label: ["亮屏", "On"], col: "wake" }] },
    { id: "lock", label: ["Windows 锁屏", "Windows Lock"], desc: ["锁定 / 解锁时", "When Windows locks / unlocks"], icon: "lock",
      wake: "wake_on_unlock_only", standby: "standby_on_lock",
      tests: [{ key: "lock", label: ["锁定", "Lock"], col: "standby" }, { key: "unlock", label: ["解锁", "Unlock"], col: "wake" }] },
    { id: "sleep", label: ["Windows 睡眠", "Windows Sleep"], desc: ["进入睡眠时", "When Windows sleeps"], icon: "moon",
      wake: null, standby: "standby_on_sleep",
      tests: [{ key: "sleep", label: ["睡眠", "Sleep"], col: "standby" }] },
    { id: "lid", label: ["笔记本合盖", "Laptop Lid Close"], desc: ["合上笔记本盖子时", "When the laptop lid closes"], icon: "laptop",
      wake: null, standby: "standby_on_lid_close", tests: [{ key: "lid-close", label: ["合盖", "Lid close"], col: "standby" }] },
    { id: "shutdown", label: ["Windows 关机", "Windows Shutdown"], desc: ["关机或注销时", "When Windows shuts down or signs out"], icon: "power",
      wake: null, standby: "endsession_standby_on_shutdown",
      tests: [{ key: "shutdown", label: ["关机", "Shutdown"], col: "standby" }] },
  ];
  const RULE_KEYS = EVENT_ROWS.flatMap(r => [r.wake, r.standby]).filter(Boolean);
  const eventLabel = key => {
    const event = EVENT_ROWS.flatMap(row => row.tests).find(test => test.key === String(key || ""));
    return event ? localized(event.label) : String(key || "");
  };
  const PRESETS = {
    recommended: { label: ["推荐", "Recommended"], desc: ["跟随电脑状态自动开关", "Follow PC state automatically"],
      values: Object.fromEntries(RULE_KEYS.map(k => [k, true])) },
    conservative: { label: ["保守", "Conservative"], desc: ["只在睡眠、合盖和关机时待机", "Only standby for sleep, lid-close, and shutdown"],
      values: Object.fromEntries(RULE_KEYS.map(k =>
        [k, ["standby_on_sleep", "standby_on_lid_close", "endsession_standby_on_shutdown"].includes(k)])) },
    manual: { label: ["手动", "Manual"], desc: ["全部关闭，只手动控制", "Disable all rules and control manually"],
      values: Object.fromEntries(RULE_KEYS.map(k => [k, false])) },
  };

  // ═════════════════════════════════════════════════════════
  // API（本地回环 HTTP → Python 桥）
  // ═════════════════════════════════════════════════════════
  const apiToken = new URLSearchParams(window.location.search).get("token") || "";
  const api = async (method, args = [], { signal = undefined, silent = false } = {}) => {
    const response = await fetch(`/api/${method}?token=${encodeURIComponent(apiToken)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ args }), signal,
    });
    const payload = await response.json();
    if (!response.ok || payload.error) {
      const detail = payload.error || t("request_failed");
      if (!silent) toast("error", t("controller_error"), detail);
      throw new Error(detail);
    }
    return payload.result;
  };
  const bridge = {
    bootstrap: () => api("bootstrap"),
    initialState: () => api("initialState"),
    updates: (cursor, signal) => api("updates", [cursor], { signal, silent: true }),
    logs: name => api("logs", [name || ""]),
    logFiles: () => api("logFiles"),
    refresh: () => api("refresh"),
    togglePower: () => api("togglePower"),
    setVolume: v => api("setVolume", [v]),
    changeInput: v => api("changeInput", [v]),
    updateSettings: v => api("updateSettings", [v]),
    applyTarget: (ip, mac) => api("applyTarget", [ip, mac]),
    updateStartup: (mode, enabled) => api("updateStartup", [mode, enabled]),
    runEvent: key => api("runEvent", [key]),
    scanSpeakers: id => api("scanSpeakers", [id]),
    cancelScan: id => api("cancelScan", [id]),
    openLogFolder: () => api("openLogFolder"),
  };

  // ═════════════════════════════════════════════════════════
  // 全局状态
  // ═════════════════════════════════════════════════════════
  let state = null, lastStateJson = "";
  let page = "home";
  let muted = false, lastAudible = 40, volumeTimer = null, editingVol = false, draggingVol = false, activeVolumePointer = null, lastMuteIcon = null;
  let volumeHoldUntil = 0; // 用户刚改过音量时，短暂忽略远端快照，避免被旧值覆盖
  let powerPending = false, powerPendingTarget = null, powerPendingStage = "", powerPendingTimer = null;
  let infoOpen = false;
  let testMode = false, runningTest = null;
  let dlg = null, dialogReturnFocus = null; // 选择扬声器弹窗：{ phase: "scanning"|"done", found: [], checked: 0, failed: "" }
  let inputHoldUntil = 0, localInput = null; // 与音量同理：刚点过的输入源短暂优先于远端快照
  let logItems = [], logQuery = "", logSearchTimer = null, logFiles = [], selectedLogFile = "";
  let pendingLogItems = [], logFlushFrame = 0;
  const SEVERITY = ["INFO", "STEP", "STATE", "EVENT", "WARN", "ERROR"];
  const LEVEL_TEXT_KEYS = { INFO: "level_info", STEP: "level_step", STATE: "level_state", EVENT: "level_event", WARN: "level_warn", ERROR: "level_error" };
  let minLogLevel = "INFO";
  let savedFlashTimer = null, volumeToastTimer = null;
  const toastTimers = new Map(), toastNodes = new Map();

  const ruleOn = key => {
    if (!state || !key) return false;
    const s = state.settings || {};
    for (const section of ["wake", "standby_triggers", "end_session"]) {
      if (s[section] && key in s[section]) return !!s[section][key];
    }
    return false;
  };

  function clearPowerPending() {
    powerPending = false;
    powerPendingTarget = null;
    powerPendingStage = "";
    clearTimeout(powerPendingTimer);
  }
  function armPowerPendingTimeout(delay) {
    clearTimeout(powerPendingTimer);
    powerPendingTimer = setTimeout(() => { clearPowerPending(); syncHome(); }, delay);
  }

  // ═════════════════════════════════════════════════════════
  // Toast
  // ═════════════════════════════════════════════════════════
  const localTitle = title => String(title || "");
  const localDetail = detail => String(detail || "");
  const actionLabel = action => ({
    WAKE: t("wake"), STANDBY: t("standby"), EARLY_STANDBY: t("standby"), ENDSESSION_STANDBY: t("standby"),
  }[String(action || "").toUpperCase()] || String(action || ""));
  const formatMessage = (template, params) => Object.entries(params || {}).reduce(
    (text, [key, value]) => text.replaceAll(`{${key}}`, String(value ?? "")), String(template || ""),
  ).trim();
  const localizedMessage = msg => {
    const spec = MESSAGE_TEXT[msg.code];
    if (!spec) return { title: localTitle(msg.title), detail: localDetail(msg.detail) };
    const params = { ...(msg.params || {}) };
    if ("source" in params) params.source = inputLabel(params.source);
    if ("action" in params) params.action = actionLabel(params.action);
    if ("event" in params) params.event = eventLabel(params.event);
    if ("speaker" in params) params.speaker = ({ On: t("power_on"), Standby: t("power_standby"), Unknown: "—" })[params.speaker] || params.speaker;
    const languageIndex = uiLanguage === "en" ? 1 : 0;
    return {
      title: formatMessage(spec.title[languageIndex], params),
      detail: formatMessage(spec.detail[languageIndex], params),
    };
  };
  const toastKind = (level, channel) => {
    if (level === "error") return "error";
    if (channel === "volume") return "volume";
    if (channel === "power") return "success";
    if (channel === "save" || level === "success") return "success";
    if (level === "warning") return "warning";
    return "info";
  };
  const toastIcon = kind => ({
    success: "check", standby: "monitorOff", volume: "volume", mute: "volumeX",
    error: "alert", warning: "alert", info: "info",
  }[kind] || "info");
  function dismissToast(channel) {
    clearTimeout(toastTimers.get(channel));
    toastTimers.delete(channel);
    const node = toastNodes.get(channel);
    if (node) node.remove();
    toastNodes.delete(channel);
  }
  function toast(level, title, detail = "", channel = "general", ttl = 3600, visualKind = "") {
    let el = toastNodes.get(channel);
    if (!el) {
      const area = $("#toasts");
      while (area.children.length >= 3) {
        const oldest = area.firstElementChild;
        const oldestChannel = oldest?.dataset.channel;
        if (oldestChannel) dismissToast(oldestChannel); else oldest?.remove();
      }
      el = document.createElement("div");
      el.dataset.channel = channel;
      el.innerHTML = `<span class="toast-icon"></span><div class="toast-body"><b></b><small></small></div><button class="toast-close" title="${t("close")}" aria-label="${t("close")}">${icon("x", 13)}</button>`;
      el.querySelector(".toast-close").addEventListener("click", () => dismissToast(channel));
      area.append(el);
      toastNodes.set(channel, el);
    }
    const kind = visualKind || toastKind(level, channel);
    el.className = `toast ${level || "info"} kind-${kind}`;
    if (level === "error") el.setAttribute("role", "alert");
    else el.removeAttribute("role");
    $(".toast-icon", el).innerHTML = icon(toastIcon(kind), 17);
    $("b", el).textContent = localTitle(title);
    const small = $("small", el);
    small.textContent = localDetail(detail);
    small.style.display = detail ? "" : "none";
    clearTimeout(toastTimers.get(channel));
    toastTimers.set(channel, setTimeout(() => dismissToast(channel), ttl));
  }
  function flashSaved() {
    const el = $("#saved-flash");
    if (!el) return;
    el.classList.remove("hidden");
    clearTimeout(savedFlashTimer);
    savedFlashTimer = setTimeout(() => el.classList.add("hidden"), 1600);
    toast("success", t("settings_saved"), "", "save", 2400);
  }

  // ═════════════════════════════════════════════════════════
  // 页面骨架（只构建一次，之后定点更新，避免打断滚动/点击）
  // ═════════════════════════════════════════════════════════
  function buildShell() {
    const navMeta = { home: ["home", t("nav_home")], log: ["log", t("nav_log")], settings: ["settings", t("nav_settings")] };
    for (const btn of $$(".nav-btn")) {
      const [ic, label] = navMeta[btn.dataset.nav];
      btn.innerHTML = `${icon(ic, 17)}<span>${label}</span>`;
    }
    $("#connection-note").innerHTML = `${icon("alert", 15)}<span>${t("controller_reconnecting")}</span>`;
    $("#main").removeAttribute("aria-busy");
    $("#main").innerHTML = `
      <div id="page-home" class="page">${homeSkeleton()}</div>
      <div id="page-log" class="page log-page hidden">${logSkeleton()}</div>
      <div id="page-settings" class="page hidden">${settingsSkeleton()}</div>`;
    bindHome();
    bindLog();
    bindSettings();
  }

  // ── 主页 ──
  function homeSkeleton() {
    const inputs = [...(state.inputs || [])]
      .sort((a, b) => INPUT_ORDER.indexOf(a.value) - INPUT_ORDER.indexOf(b.value));
    return `
      <div class="status-bar">
        <span id="dot" class="dot"></span>
        <div style="min-width:0">
          <div id="dev-name" class="status-name">—</div>
          <div id="dev-sub" class="status-sub">—</div>
        </div>
        <button id="power-btn" class="power-switch" role="switch">${icon("power", 14)}<span>—</span></button>
      </div>

      <div id="standby-note" class="standby-note hidden">${icon("monitorOff", 15)}<span>${t("standby_help")}</span></div>
      <div id="setup-note" class="standby-note setup-note hidden">${icon("info", 15)}<span>${t("configure_speaker_help")}</span><button id="setup-speaker" class="ghost-btn">${t("configure_speaker")}</button></div>

      <section id="vol-card" class="card">
        <div class="vol-head">
          <span class="card-label">${t("volume")}</span>
          <button id="vol-display" class="vol-display" title="${t("direct_volume")}" aria-label="${t("direct_volume")}">—</button>
          <input id="vol-edit" class="vol-edit" style="display:none" inputmode="numeric">
        </div>
        <div class="vol-row">
          <button id="mute-btn" class="vol-icon-btn" title="${t("mute")}">${icon("volume", 19)}</button>
          <button id="vol-down" class="nudge-btn" title="${t("volume_down")}">${icon("minus", 15)}</button>
          <div class="slider-wrap">
            <input id="vol-slider" class="slider" type="range" min="0" max="100" step="1" value="0" aria-label="${t("volume")}">
            <div class="ticks"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
          </div>
          <button id="vol-up" class="nudge-btn" title="${t("volume_up")}">${icon("plus", 15)}</button>
        </div>
        <div id="mute-note" class="mute-note hidden">${icon("volumeX", 12)} ${t("mute_note")}</div>
      </section>

      <section id="input-card" class="card">
        <span class="card-label">${t("input_source")}</span>
        <div class="input-grid">
          ${inputs.map(x => `
            <button class="input-btn" data-input="${esc(x.value)}">
              ${icon(x.value in ICONS ? x.value : "analog", 15)}<span>${esc(inputLabel(x.value))}</span>
            </button>`).join("")}
        </div>
      </section>

      <section class="card-flat">
        <button id="info-head" class="info-head">
          <span id="info-chevron">${icon("chevronRight", 15)}</span>
          <span class="info-title">${t("device_info")}</span>
          <span id="info-summary" class="info-summary">—</span>
        </button>
        <dl id="info-grid" class="info-grid hidden">
          ${["ip_address", "mac_address", "model", "firmware"].map((k, i) => `
            <div class="info-row"><dt>${t(k)}</dt><dd id="info-v${i}">—</dd></div>`).join("")}
          <div class="info-row health"><dt>${t("health_last_check")}</dt><dd id="health-poll">—</dd></div>
          <div class="info-row health"><dt>${t("health_last_action")}</dt><dd id="health-action">—</dd></div>
          <div class="info-row health"><dt>${t("health_last_issue")}</dt><dd id="health-failure">—</dd></div>
        </dl>
      </section>`;
  }

  function bindHome() {
    $("#power-btn").addEventListener("click", () => {
      if (powerPending) return;
      powerPending = true;
      powerPendingTarget = !Boolean(state?.speaker?.on);
      powerPendingStage = "sending";
      armPowerPendingTimeout(12000);
      syncHome();
      bridge.togglePower().catch(() => { clearPowerPending(); syncHome(); });
    });

    const slider = $("#vol-slider");
    slider.addEventListener("pointerdown", e => {
      draggingVol = true;
      activeVolumePointer = e.pointerId;
      if (slider.setPointerCapture) slider.setPointerCapture(e.pointerId);
    });
    slider.addEventListener("lostpointercapture", e => finishVolumeDrag(e.pointerId));
    slider.addEventListener("input", () => {
      muted = false;
      previewVolume(+slider.value);
    });
    $("#vol-down").addEventListener("click", () => nudge(-1));
    $("#vol-up").addEventListener("click", () => nudge(1));
    $("#mute-btn").addEventListener("click", () => {
      const current = currentVolume();
      if (!muted && current > 0) { muted = true; lastAudible = current; sendVolume(0, { mutedAction: true }); }
      else { muted = false; sendVolume(lastAudible || 40); }
      syncHome();
    });

    $("#vol-display").addEventListener("click", () => {
      editingVol = true;
      const edit = $("#vol-edit");
      edit.value = String(currentVolume());
      $("#vol-display").style.display = "none";
      edit.style.display = "";
      edit.focus(); edit.select();
    });
    const commitVol = () => {
      if (!editingVol) return;
      if (!state?.speaker?.on) { cancelVolumeEdit(); return; }
      editingVol = false;
      const edit = $("#vol-edit");
      const n = Math.max(0, Math.min(100, parseInt(edit.value, 10) || 0));
      edit.style.display = "none";
      $("#vol-display").style.display = "";
      muted = false;
      sendVolume(n);
      syncHome();
    };
    $("#vol-edit").addEventListener("blur", commitVol);
    $("#vol-edit").addEventListener("keydown", e => {
      if (e.key === "Enter") commitVol();
      if (e.key === "Escape") cancelVolumeEdit();
    });
    $("#vol-edit").addEventListener("input", e => {
      e.target.value = e.target.value.replace(/\D/g, "").slice(0, 3);
    });

    $(".input-grid").addEventListener("click", e => {
      const btn = e.target.closest("[data-input]");
      if (!btn || btn.disabled) return;
      localInput = btn.dataset.input;
      inputHoldUntil = Date.now() + 2500;
      bridge.changeInput(localInput).catch(() => {});
      if (state && state.speaker) { state.speaker.input = localInput; syncHome(); }
    });

    $("#setup-speaker").addEventListener("click", () => {
      setPage("settings");
      openSpeakerDialog();
    });

    $("#info-head").addEventListener("click", () => {
      infoOpen = !infoOpen;
      $("#info-grid").classList.toggle("hidden", !infoOpen);
      $("#info-chevron").innerHTML = icon(infoOpen ? "chevronDown" : "chevronRight", 15);
    });
  }

  function finishVolumeDrag(pointerId = null) {
    if (pointerId != null && activeVolumePointer != null && pointerId !== activeVolumePointer) return;
    draggingVol = false;
    activeVolumePointer = null;
  }
  function cancelVolumeEdit() {
    editingVol = false;
    const edit = $("#vol-edit"), display = $("#vol-display");
    if (edit) edit.style.display = "none";
    if (display) display.style.display = "";
  }
  const currentVolume = () => {
    const slider = $("#vol-slider");
    return slider ? +slider.value : Number(state?.speaker?.volume || 0);
  };
  function paintVolume(v) {
    const value = Math.max(0, Math.min(100, Math.round(Number(v) || 0)));
    const slider = $("#vol-slider");
    slider.value = value;
    slider.style.setProperty("--volume", `${value}%`);
    $("#vol-display").textContent = String(value);
    const mute = $("#mute-btn");
    const muteIcon = value <= 0 ? "volumeX" : "volume";
    if (lastMuteIcon !== muteIcon) {
      mute.innerHTML = icon(muteIcon, 19);
      lastMuteIcon = muteIcon;
      mute.querySelector("svg").style.color = value <= 0 ? "var(--danger)" : "";
    }
    mute.title = value <= 0 ? t("unmute") : t("mute");
    $("#mute-note").classList.toggle("hidden", !(muted && value <= 0));
    if (value > 0) lastAudible = value;
    return value;
  }
  function previewVolume(v) {
    const value = paintVolume(v);
    if (state && state.speaker) state.speaker.volume = value;
    volumeHoldUntil = Date.now() + 2500;
    clearTimeout(volumeTimer);
    volumeTimer = setTimeout(() => bridge.setVolume(value).catch(() => {}), 180);
    clearTimeout(volumeToastTimer);
    volumeToastTimer = setTimeout(
      () => toast("info", t("volume_updated", value), "", "volume", 2600, "volume"),
      350,
    );
  }
  function sendVolume(v, { mutedAction = false } = {}) {
    clearTimeout(volumeTimer);
    const value = paintVolume(v);
    if (state && state.speaker) state.speaker.volume = value;
    volumeHoldUntil = Date.now() + 2500;
    bridge.setVolume(value).catch(() => {});
    clearTimeout(volumeToastTimer);
    toast(
      mutedAction ? "warning" : "info",
      mutedAction ? t("muted") : t("volume_updated", value),
      "",
      "volume",
      2600,
      mutedAction ? "mute" : "volume",
    );
  }
  function nudge(delta) {
    muted = false;
    sendVolume(currentVolume() + delta);
  }

  const healthAge = seconds => {
    if (seconds == null) return t("health_none");
    const rounded = Math.max(0, Math.round(Number(seconds) || 0));
    return rounded < 5 ? t("health_now") : t("health_seconds_ago", rounded);
  };
  const healthActionLabel = action => ({
    WAKE: t("wake"), STANDBY: t("standby"), EARLY_STANDBY: t("standby"), ENDSESSION_STANDBY: t("standby"),
  }[action] || String(action || ""));

  function syncHome() {
    if (!state) return;
    const s = state.speaker || {};
    const on = !!s.on;
    const configured = Boolean(s.ip || state?.settings?.device?.kef_ip || state?.settings?.device?.kef_mac);
    $("#dot").className = `dot ${on ? "on" : ""}`;
    $("#dev-name").textContent = s.name || t("no_device");
    const link = s.ip ? (on ? t("connected_on") : t("connected_standby")) : t("disconnected");
    $("#dev-sub").textContent = `${link}${s.ip ? " · " + s.ip : ""}`;

    const pb = $("#power-btn");
    pb.className = `power-switch ${on ? "on" : ""} ${powerPending ? "pending" : ""}`;
    pb.disabled = powerPending || !configured;
    pb.setAttribute("aria-checked", String(on));
    pb.innerHTML = powerPending
      ? `${icon("loader", 14, "spin")}<span>${t(powerPendingTarget ? (powerPendingStage === "confirming" ? "waiting_confirmation" : "waking") : "entering_standby")}</span>`
      : `${icon("power", 14)}<span>${on ? t("power_on") : t("power_standby")}</span>`;

    $("#setup-note").classList.toggle("hidden", configured);
    $("#standby-note").classList.toggle("hidden", !configured || on || powerPendingTarget === true);

    if (!on) {
      finishVolumeDrag();
      cancelVolumeEdit();
    }
    if (!editingVol && !draggingVol && s.volume != null && Date.now() > volumeHoldUntil) {
      if (muted && Number(s.volume) > 0) muted = false;
      paintVolume(s.volume);
    }
    $("#vol-card").classList.toggle("dim", !on);
    $("#input-card").classList.toggle("dim", !on);
    for (const control of [$("#mute-btn"), $("#vol-down"), $("#vol-slider"), $("#vol-up"), $("#vol-display"), $("#vol-edit")]) {
      control.disabled = !on;
    }
    const shownInput = (Date.now() < inputHoldUntil && localInput) ? localInput : s.input;
    for (const btn of $$("[data-input]")) {
      btn.classList.toggle("active", btn.dataset.input === shownInput);
      btn.disabled = !on;
    }

    $("#info-summary").textContent = [s.model, s.firmware].filter(Boolean).join(" · ") || "—";
    const vals = [s.ip, s.mac, s.model, s.firmware];
    vals.forEach((v, i) => { $(`#info-v${i}`).textContent = v || "—"; });
    const health = state.health || {};
    $("#health-poll").textContent = healthAge(health.last_heartbeat_age_s ?? health.last_poll_age_s);
    const action = health.last_action;
    $("#health-action").textContent = action
      ? `${healthActionLabel(action.name)} · ${action.elapsed_ms || 0} ms${action.success ? "" : ` · ${t("failed")}`}`
      : t("health_none");
    const failure = health.last_failure;
    const heartbeatFailure = health.heartbeat_error;
    $("#health-failure").textContent = failure
      ? `${localDetail(failure.detail)} · ${healthAge(failure.age_s)}`
      : heartbeatFailure
        ? `${localDetail(heartbeatFailure)} · ${t("level_warn")} ${health.heartbeat_failures || 1}`
        : t("health_none");
    $("#info-grid").classList.toggle("hidden", !infoOpen);
    $("#info-chevron").innerHTML = icon(infoOpen ? "chevronDown" : "chevronRight", 15);
  }

  // ── 日志页 ──

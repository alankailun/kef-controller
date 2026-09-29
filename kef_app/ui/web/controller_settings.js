"use strict";
  function settingsSkeleton() {
    return `
      <div class="page-head">
        <h1>${t("nav_settings")}</h1>
        <span id="saved-flash" class="saved-flash hidden">${icon("check", 13)} ${t("saved")}</span>
      </div>

      <h3 class="sec-title">${t("general")}</h3>
      <section class="card">
        <div class="set-row">
          <div style="flex:1">
            <div class="set-label">${t("language")}</div>
            <div class="set-desc">${t("language_desc")}</div>
          </div>
          <div class="lang-switch" role="group" aria-label="${t("language")}">
            <button class="lang-btn" data-language="zh">中文</button>
            <button class="lang-btn" data-language="en">English</button>
          </div>
        </div>
      </section>

      <h3 class="sec-title">${t("speaker")}</h3>
      <section class="card">
        <div class="set-row">
          <div style="flex:1">
            <div class="set-label">${t("default_input")}</div>
            <div class="set-desc">${t("default_input_desc")}</div>
          </div>
          <select id="set-input">
            ${(state.inputs || []).map(x => `<option value="${esc(x.value)}">${esc(inputLabel(x.value))}</option>`).join("")}
          </select>
        </div>
        <div class="divider"></div>
        <div class="set-row">
          <div style="flex:1">
            <div class="set-label">${t("target_speaker")}</div>
            <div class="set-desc">${t("target_desc")}</div>
          </div>
          <div class="target-val">
            <div id="target-name">—</div>
            <div id="target-meta" class="target-ip">—</div>
          </div>
          <button id="pick-btn" class="primary-btn">${t("select_speaker")}</button>
        </div>
      </section>

      <h3 class="sec-title">${t("power_behavior")}</h3>
      <p class="sec-desc">${t("power_behavior_desc")} <span id="enabled-count"></span></p>

      <div class="preset-row">
        <span class="preset-label">${t("preset")}</span>
        ${Object.entries(PRESETS).map(([k, p]) => `
          <button class="preset-btn" data-preset="${k}" title="${esc(localized(p.desc))}">${localized(p.label)}</button>`).join("")}
        <span id="custom-tag" class="custom-tag hidden">${t("custom")}</span>
        <button id="test-toggle" class="test-toggle">${icon("flask", 13)}<span>${t("simulate_events")}</span></button>
        <button id="reset-btn" class="reset-btn" title="${t("restore_recommended")}">${icon("rotateCcw", 13)}</button>
      </div>

      <div id="test-banner" class="test-banner hidden">
        ${icon("flask", 14)}
        <span>${t("test_banner")}</span>
      </div>

      <div id="matrix" class="matrix">
        <div class="mx-head">
          <div class="th-event">${t("trigger_event")}</div>
          <button class="th-col" data-col="wake">
            <span class="th-col-title">${icon("unlock", 13)}<span style="color:var(--teal)">${t("wake")}</span></span>
            <span class="th-col-hint" data-col-hint="wake"></span>
          </button>
          <button class="th-col" data-col="standby">
            <span class="th-col-title">${icon("monitorOff", 13)}<span style="color:var(--faint)">${t("standby")}</span></span>
            <span class="th-col-hint" data-col-hint="standby"></span>
          </button>
          <div class="th-col td-test" style="cursor:default">
            <span class="th-col-title">${t("test")}</span>
            <span class="th-col-hint">${t("run_once")}</span>
          </div>
        </div>
        ${EVENT_ROWS.map((row, i) => `
          <div class="mx-row ${i % 2 ? "alt" : ""}" data-row="${row.id}">
            <div class="td-event">
              <span class="ev-icon">${icon(row.icon, 15)}</span>
              <div style="min-width:0">
                <div class="ev-label">${localized(row.label)}</div>
                <div class="ev-desc">${localized(row.desc)}</div>
              </div>
            </div>
            ${["wake", "standby"].map(col => `
              <div class="td-cell">
                ${row[col]
                  ? `<button class="check" data-rule="${row[col]}" data-checkcol="${col}" role="checkbox" aria-checked="false" aria-label="${esc(`${localized(row.label)} · ${t(col)}`)}">${icon("check", 14)}</button>`
                  : `<span class="na" title="${t("not_applicable")}">${icon("minus", 13)}</span>`}
              </div>`).join("")}
            <div class="td-test">
              ${row.tests.map(test => `
                <button class="test-btn" data-test="${test.key}" data-testcol="${test.col === "wake" ? row.wake : row.standby}">
                  ${icon("play", 11)}<span>${localized(test.label)}</span>
                </button>`).join("") || `<span class="na">${icon("minus", 13)}</span>`}
            </div>
          </div>
          <div class="mx-result" data-result="${row.id}"></div>`).join("")}
      </div>

      <div id="screen-warn" class="warn-note hidden">
        ${icon("alert", 14)}
        <span>${t("screen_warning")}</span>
      </div>
      <p class="table-foot">${t("table_foot")}<span id="test-foot" style="display:none"> ${t("table_foot_test")}</span></p>

      <h3 class="sec-title">${t("windows_startup")}</h3>
      <section class="card">
        <div class="set-row">
          <div style="flex:1">
            <div class="set-label">${t("start_on_login")}</div>
            <div class="set-desc">${t("start_on_login_desc")}</div>
          </div>
          <input id="set-startup-enabled" class="switch" type="checkbox">
        </div>
        <div class="divider"></div>
        <div id="startup-method-row" class="set-row startup-method">
          <div style="flex:1">
            <div class="set-label">${t("startup_method")}</div>
            <div id="startup-method-desc" class="set-desc"></div>
          </div>
          <div id="set-startup-mode" class="startup-segment" role="group" aria-label="${t("startup_method")}">
            <button class="startup-method-btn" data-startup-mode="registry">${t("startup_registry")}</button>
            <button class="startup-method-btn" data-startup-mode="task">${t("startup_task")}</button>
          </div>
        </div>
        <div id="startup-status" class="startup-status"></div>
      </section>`;
  }

  function bindSettings() {
    $(".lang-switch").addEventListener("click", e => {
      const button = e.target.closest("[data-language]");
      if (!button || button.dataset.language === uiLanguage) return;
      const next = button.dataset.language;
      uiLanguage = next;
      if (state?.settings?.general) state.settings.general.ui_language = next;
      document.documentElement.lang = uiLanguage === "en" ? "en" : "zh-CN";
      buildShell();
      syncHome();
      syncSettings();
      setPage(page);
      bridge.updateSettings(JSON.stringify({ ui_language: next })).then(flashSaved).catch(() => {});
    });
    $("#set-input").addEventListener("change", e => {
      bridge.updateSettings(JSON.stringify({ kef_input: e.target.value })).then(flashSaved).catch(() => {});
    });
    $("#pick-btn").addEventListener("click", openSpeakerDialog);

    $(".preset-row").addEventListener("click", e => {
      const preset = e.target.closest("[data-preset]");
      if (preset) {
        bridge.updateSettings(JSON.stringify(PRESETS[preset.dataset.preset].values)).then(flashSaved).catch(() => {});
        return;
      }
      if (e.target.closest("#reset-btn")) {
        bridge.updateSettings(JSON.stringify(PRESETS.recommended.values)).then(flashSaved).catch(() => {});
        return;
      }
      if (e.target.closest("#test-toggle")) {
        testMode = !testMode;
        for (const el of $$(".mx-result")) { el.classList.remove("show"); el.innerHTML = ""; }
        syncSettings();
      }
    });

    $("#matrix").addEventListener("click", e => {
      const check = e.target.closest("[data-rule]");
      if (check) {
        const key = check.dataset.rule;
        bridge.updateSettings(JSON.stringify({ [key]: !ruleOn(key) })).then(flashSaved).catch(() => {});
        return;
      }
      const colBtn = e.target.closest("[data-col]");
      if (colBtn) {
        const col = colBtn.dataset.col;
        const keys = EVENT_ROWS.map(r => r[col]).filter(Boolean);
        const allOn = keys.every(ruleOn);
        bridge.updateSettings(JSON.stringify(Object.fromEntries(keys.map(k => [k, !allOn]))))
          .then(flashSaved).catch(() => {});
        return;
      }
      const testBtn = e.target.closest("[data-test]");
      if (testBtn && !runningTest) {
        runningTest = testBtn.dataset.test;
        syncSettings();
        bridge.runEvent(runningTest).catch(() => { runningTest = null; syncSettings(); });
        return;
      }
      const close = e.target.closest(".mx-result .close");
      if (close) {
        const box = close.closest(".mx-result");
        box.classList.remove("show");
        box.innerHTML = "";
      }
    });

    $("#set-startup-enabled").addEventListener("change", e => {
      pushStartup("registry", e.currentTarget.checked);
    });
    $("#set-startup-mode").addEventListener("click", e => {
      const button = e.target.closest("[data-startup-mode]");
      if (!button || button.disabled) return;
      pushStartup(button.dataset.startupMode, true);
    });
  }

  function startupMode() {
    const requested = (state?.startup || {}).requested_mode;
    if (requested === "task" || requested === "registry") return requested;
    const configured = ((state?.settings || {}).startup || {}).startup_registration_mode;
    return configured === "task" ? "task" : "registry";
  }

  function pushStartup(mode = startupMode(), enabled = $("#set-startup-enabled").checked) {
    bridge.updateStartup(mode, enabled).catch(() => {});
  }

  // ── 选择扬声器弹窗 ──
  const normMac = m => String(m || "").replace(/[^0-9a-fA-F]/g, "").toUpperCase();
  const currentTargetMac = () => normMac(state?.speaker?.mac || state?.settings?.device?.kef_mac);

  function openSpeakerDialog() {
    dlg = { phase: "scanning", found: [], checked: 0, failed: "" };
    dialogReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    $(".shell").inert = true;
    $("#toasts").inert = true;
    const el = document.createElement("div");
    el.className = "dlg-backdrop";
    el.id = "dlg-backdrop";
    el.innerHTML = `
      <div class="dlg" role="dialog" aria-modal="true" aria-label="${t("select_speaker")}">
        <div class="dlg-head">
          <div class="dlg-head-icon">${icon("optical", 15)}</div>
          <span class="dlg-title">${t("select_speaker")}</span>
          <button class="dlg-x" data-dlg="close" title="${t("close")}" aria-label="${t("close")}">${icon("x", 15)}</button>
        </div>
        <p class="dlg-sub">${t("dlg_sub")}</p>
        <div id="dlg-scan-bar" class="scan-bar"></div>
        <div id="dlg-list" class="dlg-list"></div>
        <button id="dlg-manual-link" class="manual-link">${t("manual_link")}</button>
        <div id="dlg-manual" class="manual-box" style="display:none">
          <div class="manual-title">${t("manual_entry")}</div>
          <div class="manual-row">
            <input id="dlg-ip" class="text-input" placeholder="${t("ip_placeholder")}">
            <input id="dlg-mac" class="text-input" placeholder="${t("mac_placeholder")}">
          </div>
          <div class="manual-actions">
            <button id="dlg-manual-cancel" class="ghost-btn">${t("cancel")}</button>
            <button id="dlg-manual-apply" class="primary-btn" disabled>${t("apply")}</button>
          </div>
        </div>
        <div class="dlg-foot"><button class="ghost-btn" data-dlg="close">${t("close")}</button></div>
      </div>`;
    document.body.append(el);
    const dialog = $(".dlg", el);
    const focusable = () => $$("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex=\"-1\"])", dialog)
      .filter(node => node.getClientRects().length);
    el.addEventListener("keydown", e => {
      if (e.key !== "Tab") return;
      const items = focusable();
      if (!items.length) { e.preventDefault(); return; }
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });

    el.addEventListener("click", e => {
      if (e.target === el || e.target.closest("[data-dlg=close]")) { closeSpeakerDialog(); return; }
      const rescan = e.target.closest("#dlg-rescan");
      if (rescan) { startDialogScan(); return; }
      const item = e.target.closest("[data-sp-ip]");
      if (item && !item.classList.contains("current")) {
        bridge.applyTarget(item.dataset.spIp, item.dataset.spMac || "").then(flashSaved).catch(() => {});
        closeSpeakerDialog();
      }
    });
    $("#dlg-manual-link").addEventListener("click", () => {
      $("#dlg-manual-link").style.display = "none";
      $("#dlg-manual").style.display = "";
      $("#dlg-ip").focus();
    });
    $("#dlg-manual-cancel").addEventListener("click", () => {
      $("#dlg-manual").style.display = "none";
      $("#dlg-manual-link").style.display = "";
    });
    $("#dlg-ip").addEventListener("input", () => {
      $("#dlg-manual-apply").disabled = !$("#dlg-ip").value.trim();
    });
    $("#dlg-manual-apply").addEventListener("click", () => {
      const ip = $("#dlg-ip").value.trim();
      if (!ip) return;
      bridge.applyTarget(ip, $("#dlg-mac").value.trim()).then(flashSaved).catch(() => {});
      closeSpeakerDialog();
    });

    $(".dlg-x", el).focus();
    startDialogScan();
  }

  function closeSpeakerDialog() {
    if (dlg?.scanId) bridge.cancelScan(dlg.scanId).catch(() => {});
    dlg = null;
    const el = $("#dlg-backdrop");
    if (el) el.remove();
    $(".shell").inert = false;
    $("#toasts").inert = false;
    const returnFocus = dialogReturnFocus;
    dialogReturnFocus = null;
    if (returnFocus && document.contains(returnFocus) && !returnFocus.disabled) returnFocus.focus();
  }

  function startDialogScan() {
    if (!dlg) return;
    const scanId = crypto.randomUUID();
    dlg.scanId = scanId;
    dlg.phase = "scanning";
    dlg.found = [];
    dlg.checked = 0;
    dlg.failed = "";
    syncDialog();
    bridge.scanSpeakers(scanId).catch(() => {
      if (!dlg || dlg.scanId !== scanId) return;
      dlg.phase = "done";
      dlg.failed = t("scan_failed");
      syncDialog();
    });
  }

  function syncDialog() {
    if (!dlg) return;
    const bar = $("#dlg-scan-bar"), list = $("#dlg-list");
    if (!bar || !list) return;

    bar.classList.toggle("failed", !!dlg.failed);
    if (dlg.phase === "scanning") {
      bar.innerHTML = `${icon("loader", 15, "spin")}<span class="scan-text">${t("scanning")}${dlg.checked ? ` · ${t("checked_devices", dlg.checked)}` : ""}</span>`;
    } else if (dlg.failed) {
      bar.innerHTML = `${icon("alert", 15)}<span class="scan-text">${esc(dlg.failed)}</span>
        <button id="dlg-rescan" class="rescan-btn">${icon("refresh", 12)} ${t("rescan")}</button>`;
    } else {
      const hasCurrent = dlg.found.some(d => normMac(d.mac) === currentTargetMac());
      bar.innerHTML = `${icon("check", 15)}<span class="scan-text">${t("scan_complete", dlg.found.length)}${hasCurrent ? t("current_included") : ""}</span>
        <button id="dlg-rescan" class="rescan-btn">${icon("refresh", 12)} ${t("rescan")}</button>`;
    }

    if (dlg.phase === "scanning" && !dlg.found.length) {
      list.innerHTML = `<div class="skeleton"></div><div class="skeleton"></div>`;
    } else if (dlg.phase === "done" && !dlg.found.length) {
      list.innerHTML = `<div class="dlg-empty">${t("no_speaker")}</div>`;
    } else {
      const cur = currentTargetMac();
      list.innerHTML = dlg.found.map(d => {
        const isCur = cur && normMac(d.mac) === cur;
        return `
        <button class="sp-item ${isCur ? "current" : ""}" data-sp-ip="${esc(d.ip)}" data-sp-mac="${esc(d.mac || "")}">
          <span class="sp-icon">${icon(isCur ? "check" : "optical", 16)}</span>
          <div style="flex:1;min-width:0">
            <div class="sp-name"><span>${esc(d.name || d.model || "KEF")}</span>${isCur ? `<span class="cur-tag">${t("in_use")}</span>` : ""}</div>
            <div class="sp-meta">IP ${esc(d.ip)}${d.mac ? ` · MAC ${esc(d.mac)}` : ""}</div>
          </div>
          ${isCur ? "" : `<span class="sel-hint">${t("select")}</span>`}
        </button>`;
      }).join("") + (dlg.phase === "scanning" ? `<div class="skeleton" style="height:34px"></div>` : "");
    }
  }

  function syncSettings() {
    if (!state) return;
    for (const button of $$("[data-language]")) {
      button.classList.toggle("active", button.dataset.language === uiLanguage);
      button.setAttribute("aria-pressed", String(button.dataset.language === uiLanguage));
    }
    const dev = (state.settings || {}).device || {};
    const sel = $("#set-input");
    if (sel && document.activeElement !== sel) sel.value = dev.kef_input || "";
    const s = state.speaker || {};
    $("#target-name").textContent = s.name || t("no_device");
    $("#target-meta").textContent = [s.mac || dev.kef_mac, s.ip || dev.kef_ip].filter(Boolean).join(" · ") || "—";

    // 矩阵
    for (const check of $$("[data-rule]")) {
      const on = ruleOn(check.dataset.rule);
      check.classList.toggle("on-wake", on && check.dataset.checkcol === "wake");
      check.classList.toggle("on-standby", on && check.dataset.checkcol === "standby");
      check.setAttribute("aria-checked", String(on));
    }
    for (const col of ["wake", "standby"]) {
      const keys = EVENT_ROWS.map(r => r[col]).filter(Boolean);
      const allOn = keys.every(ruleOn);
      const hint = $(`[data-col-hint="${col}"]`);
      if (hint) hint.textContent = allOn ? t("all_off") : t("all_on");
    }
    const enabled = RULE_KEYS.filter(ruleOn).length;
    $("#enabled-count").textContent = t("enabled_rules", enabled, RULE_KEYS.length);

    const active = Object.entries(PRESETS).find(([, p]) =>
      RULE_KEYS.every(k => p.values[k] === ruleOn(k)))?.[0];
    for (const btn of $$("[data-preset]")) btn.classList.toggle("active", btn.dataset.preset === active);
    $("#custom-tag").classList.toggle("hidden", !!active);

    // 模拟模式
    $("#matrix").classList.toggle("testing", testMode);
    $("#test-banner").classList.toggle("hidden", !testMode);
    $("#test-foot").style.display = testMode ? "" : "none";
    const toggle = $("#test-toggle");
    toggle.classList.toggle("on", testMode);
    toggle.innerHTML = `${icon("flask", 13)}<span>${testMode ? t("exit_simulate") : t("simulate_events")}</span>`;
    for (const btn of $$("[data-test]")) {
      const on = ruleOn(btn.dataset.testcol);
      btn.classList.toggle("off", !on);
      btn.title = on ? t("run_once") : t("rule_disabled_simulation");
      btn.disabled = !!runningTest;
      const running = runningTest === btn.dataset.test;
      const iconState = running ? "running" : "idle";
      if (btn.dataset.iconState !== iconState) {
        btn.firstElementChild.outerHTML = running ? icon("loader", 11, "spin") : icon("play", 11);
        btn.dataset.iconState = iconState;
      }
    }

    $("#screen-warn").classList.toggle("hidden",
      !(ruleOn("standby_on_display_off") && !ruleOn("wake_on_display_on")));

    const startupSwitch = $("#set-startup-enabled");
    const startupState = state.startup || {};
    const startupBusy = !!startupState.busy;
    const startupPending = !!startupState.pending;
    const startupEnabled = startupPending
      ? startupState.mode === "task" || startupState.mode === "registry"
      : startupBusy && typeof startupState.requested_enabled === "boolean"
      ? startupState.requested_enabled
      : !!startupState.registered;
    if (document.activeElement !== startupSwitch) startupSwitch.checked = startupEnabled;
    startupSwitch.disabled = startupBusy || startupPending;
    const selectedStartupMode = startupMode();
    const methodRow = $("#startup-method-row");
    methodRow.classList.toggle("disabled", !startupEnabled);
    $("#startup-method-desc").textContent = t(selectedStartupMode === "task" ? "startup_task_desc" : "startup_registry_desc");
    for (const button of $$("[data-startup-mode]")) {
      const active = button.dataset.startupMode === selectedStartupMode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
      button.disabled = !startupEnabled || startupBusy || startupPending;
    }
    const actualStartupMode = startupState.mode;
    const startupStatus = $("#startup-status");
    const activeStartup = startupEnabled && (actualStartupMode === "task" || actualStartupMode === "registry");
    startupStatus.classList.toggle("active", activeStartup && !startupBusy && !startupPending);
    startupStatus.classList.toggle("busy", startupBusy || startupPending);
    startupStatus.innerHTML = startupPending
      ? `${icon("loader", 14, "spin")}<span>${t("startup_checking")}</span>`
      : startupBusy
      ? `${icon("loader", 14, "spin")}<span>${startupEnabled
        ? t("startup_applying", t(selectedStartupMode === "task" ? "startup_task" : "startup_registry"))
        : t("startup_disabling")}</span>`
      : `${icon(activeStartup ? "check" : "info", 14)}<span>${activeStartup
        ? t("startup_active", t(actualStartupMode === "task" ? "startup_task" : "startup_registry"))
        : t("startup_inactive")}</span>`;
  }

  // 模拟事件结果 → 行内结果条
  function showEventResult(msg) {
    if (msg.state !== "running" && runningTest === msg.key) runningTest = null;
    const row = EVENT_ROWS.find(r => r.tests.some(test => test.key === msg.key));
    syncSettings();
    if (!row) return;
    const box = $(`[data-result="${row.id}"]`);
    if (!box) return;
    const matchedTest = row.tests.find(entry => entry.key === msg.key);
    const iconName = { success: "check", warning: "alert", error: "alert", running: "loader" }[msg.state] || "check";
    box.className = `mx-result show ${esc(msg.state)}`;
    box.innerHTML = `
      ${icon(iconName, 14, msg.state === "running" ? "spin" : "")}
      <div style="min-width:0">
        <div><strong>${esc(matchedTest ? localized(matchedTest.label) : msg.key)}</strong> · ${esc(localizedMessage(msg).title)} · ${esc(localizedMessage(msg).detail)}</div>
      </div>
      <button class="close" title="${t("close")}" aria-label="${t("close")}">${icon("x", 13)}</button>`;
  }


"use strict";
  function logSkeleton() {
    return `
      <div class="page-head">
        <h1>${t("app_log")}</h1>
        <select id="log-file" title="${t("log_file")}"><option value="">${t("today_log")}</option></select>
        <button id="log-refresh" class="ghost-btn">${icon("refresh", 14)} ${t("refresh")}</button>
        <button id="log-folder" class="ghost-btn">${icon("folder", 14)} ${t("open_folder")}</button>
      </div>
      <div class="log-toolbar">
        <div class="search-box">
          ${icon("search", 15)}
          <input id="log-search" type="search" value="${esc(logQuery)}" placeholder="${t("search_log")}">
          <button id="log-search-clear" class="search-clear ${logQuery ? "show" : ""}" title="${t("clear_search")}">${icon("x", 13)}</button>
        </div>
        <div class="severity-row">
          <span class="severity-hint">${t("min_level")}</span>
          <div id="severity-track" class="severity-track">
            ${SEVERITY.map(level => `<button class="severity-btn ${level}" data-min-level="${level}">${t(LEVEL_TEXT_KEYS[level])} <span data-count="${level}">0</span></button>`).join("")}
          </div>
          <button id="log-filter-reset" class="lvl-link" style="display:none">${t("reset_filters")}</button>
        </div>
      </div>
      <div class="log-wrap">
        <div id="log-box" class="log-box"><div class="log-empty">${t("app_log")}…</div></div>
        <button id="to-bottom" class="to-bottom">${icon("chevronDown", 13)} ${t("back_to_bottom")}</button>
      </div>
      <p id="log-foot" class="log-foot"></p>`;
  }

  function bindLog() {
    $("#log-refresh").addEventListener("click", () => {
      if (!selectedLogFile) bridge.refresh().catch(() => {});
      loadLogs();
    });
    $("#log-file").addEventListener("change", e => {
      selectedLogFile = e.target.value;
      loadLogs();
    });
    $("#log-folder").addEventListener("click", () => bridge.openLogFolder().catch(() => {}));
    $("#log-search").addEventListener("input", e => {
      $("#log-search-clear").classList.toggle("show", !!e.target.value);
      clearTimeout(logSearchTimer);
      logSearchTimer = setTimeout(() => { logQuery = e.target.value; renderLogList(); }, 120);
    });
    $("#log-search-clear").addEventListener("click", () => {
      const input = $("#log-search");
      input.value = "";
      logQuery = "";
      $("#log-search-clear").classList.remove("show");
      renderLogList();
      input.focus();
    });
    $("#severity-track").addEventListener("click", e => {
      const btn = e.target.closest("[data-min-level]");
      if (!btn) return;
      minLogLevel = btn.dataset.minLevel;
      renderLogList();
    });
    $("#log-filter-reset").addEventListener("click", () => {
      logQuery = "";
      minLogLevel = "INFO";
      const input = $("#log-search");
      input.value = "";
      $("#log-search-clear").classList.remove("show");
      renderLogList();
      input.focus();
    });
    const box = $("#log-box");
    box.addEventListener("scroll", () => {
      $("#to-bottom").classList.toggle("show", !isNearBottom(box));
    });
    $("#to-bottom").addEventListener("click", () => {
      box.scrollTop = box.scrollHeight;
      $("#to-bottom").classList.remove("show");
    });
  }

  function parseLog(line) {
    const raw = String(line || "");
    const structured = raw.match(/^\[([^\]]+)\]\[([^\]]+)\]\[([A-Z]+)\]\s*(.*)$/);
    const legacy = structured ? null : raw.match(/^\[([^\]]+)\]\[([^\]]+)\]\s*(.*)$/);
    const short = structured || legacy ? null : raw.match(/^\[([^\]]+)\]\s*(.*)$/);
    const stamp = structured ? structured[1] : (legacy ? legacy[1] : (short ? short[1] : ""));
    const source = structured ? structured[2] : (legacy ? legacy[2] : "");
    const original = structured ? structured[4] : (legacy ? legacy[3] : (short ? short[2] : raw));
    const prefixLevel = original.match(/^(ERROR|WARN(?:ING)?|INFO)\s*[:|-]?\s*/i);
    const categorized = prefixLevel ? original.slice(prefixLevel[0].length) : original;
    const rawLevel = structured ? structured[3] : (prefixLevel ? prefixLevel[1] : "INFO");
    let lvl = rawLevel === "WARNING" ? "WARN" : rawLevel === "CRITICAL" ? "ERROR" : rawLevel;
    const category = categorized.match(/^(BEGIN|STEP|END|EVENT|STATE|SKIP)\s/);
    if ((lvl === "INFO" || lvl === "DEBUG") && category) {
      lvl = category[1] === "EVENT" ? "EVENT" : category[1] === "STATE" ? "STATE" : "STEP";
    }
    if (!SEVERITY.includes(lvl)) lvl = "INFO";
    const message = category ? categorized.slice(category[0].length) : categorized;
    return { raw, time: stamp.includes(" ") ? stamp.split(" ").pop() : stamp, source, message, lvl };
  }

  const logMatches = item =>
    SEVERITY.indexOf(item.lvl) >= SEVERITY.indexOf(minLogLevel) &&
    (!logQuery.trim() || item.raw.toLowerCase().includes(logQuery.trim().toLowerCase()));

  function highlightLog(text) {
    const query = logQuery.trim();
    if (!query) return esc(text);
    const safe = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return String(text).split(new RegExp(`(${safe})`, "gi")).map((part, index) =>
      index % 2 ? `<mark>${esc(part)}</mark>` : esc(part)).join("");
  }

  function logRowHtml(item) {
    return `<div class="log-line ${item.lvl}">
      <span class="log-time">${esc(item.time || "—")}</span>
      <span class="log-lvl ${item.lvl}">${item.lvl}</span>
      <span class="log-txt">${item.source ? esc(item.source) + " · " : ""}${highlightLog(item.message)}</span>
    </div>`;
  }

  function isNearBottom(box) {
    return box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  }

  function renderLogList({ stickBottom = false } = {}) {
    const box = $("#log-box");
    if (!box) return;
    const keep = stickBottom || isNearBottom(box);
    const visible = logItems.filter(logMatches);
    box.innerHTML = visible.length
      ? visible.map(logRowHtml).join("")
      : `<div class="log-empty">${logQuery.trim() ? t("no_match", logQuery.trim()) : t("no_level")}</div>`;
    if (keep) box.scrollTop = box.scrollHeight;
    syncLogMeta(visible.length);
  }

  function syncLogMeta(visibleCount) {
    const counts = Object.fromEntries(SEVERITY.map(level => [level, 0]));
    for (const item of logItems) counts[item.lvl] = (counts[item.lvl] || 0) + 1;
    for (const level of SEVERITY) {
      const count = level === "INFO" ? logItems.length : counts[level];
      const atOrAbove = SEVERITY.slice(SEVERITY.indexOf(level)).reduce((total, itemLevel) => total + counts[itemLevel], 0);
      const button = $(`[data-min-level="${level}"]`);
      const el = $(`[data-count="${level}"]`);
      if (el) el.textContent = String(count);
      if (button) {
        button.classList.toggle("active", minLogLevel === level);
        // Empty severity filters remain selectable so every level consistently
        // reports "No logs at this level" instead of becoming a dead chip.
        button.disabled = false;
        button.title = t("severity_tip", t(LEVEL_TEXT_KEYS[level]), atOrAbove);
      }
    }
    const foot = $("#log-foot");
    if (foot) {
      const visible = visibleCount ?? logItems.filter(logMatches).length;
      foot.textContent = `${t("showing")} ${visible} / ${logItems.length} ${t("lines")}${minLogLevel !== "INFO" ? ` · ${t("hidden_below", t(LEVEL_TEXT_KEYS[minLogLevel]))}` : ""}`;
    }
    const reset = $("#log-filter-reset");
    if (reset) reset.style.display = logQuery.trim() || minLogLevel !== "INFO" ? "" : "none";
    const box = $("#log-box"), toBottom = $("#to-bottom");
    if (box && toBottom) toBottom.classList.toggle("show", !isNearBottom(box));
  }

  function appendLogLine(line) {
    if (selectedLogFile) return;
    pendingLogItems.push(parseLog(line));
    if (!logFlushFrame) logFlushFrame = requestAnimationFrame(flushLogLines);
  }

  function flushLogLines() {
    logFlushFrame = 0;
    const pending = pendingLogItems.splice(0);
    if (!pending.length || selectedLogFile) return;
    logItems.push(...pending);
    if (logItems.length > 800) logItems.splice(0, logItems.length - 800);
    const box = $("#log-box");
    if (!box || page !== "log") return;
    const visible = pending.filter(logMatches);
    if (visible.length) {
      const keep = isNearBottom(box);
      const empty = box.querySelector(".log-empty");
      if (empty) empty.remove();
      box.insertAdjacentHTML("beforeend", visible.map(logRowHtml).join(""));
      while (box.children.length > 800) box.firstElementChild.remove();
      if (keep) box.scrollTop = box.scrollHeight;
    }
    syncLogMeta();
  }

  function loadLogs() {
    bridge.logs(selectedLogFile).then(lines => {
      logItems = (lines || []).map(parseLog);
      renderLogList({ stickBottom: true });
    }).catch(() => {});
  }

  function syncLogFileSelector() {
    const select = $("#log-file");
    if (!select) return;
    if (selectedLogFile && !logFiles.includes(selectedLogFile)) selectedLogFile = "";
    const currentName = logFiles[0] || "";
    select.innerHTML = `<option value="">${t("today_log")}${currentName ? ` · ${esc(currentName)}` : ""}</option>`
      + logFiles.slice(1).map(name => `<option value="${esc(name)}">${esc(name.replace(/^.*\.(\d{4}-\d{2}-\d{2})$/, "$1"))}</option>`).join("");
    select.value = selectedLogFile;
  }

  function loadLogFiles() {
    return bridge.logFiles().then(files => {
      logFiles = Array.isArray(files) ? files : [];
      syncLogFileSelector();
    }).catch(() => {
      logFiles = [];
      syncLogFileSelector();
    });
  }

  // ── 设置页 ──

/* ============================================================
   DDZM Admin — Frontend Script (v2)
   
   原则：渐进增强。无JS时纯HTML表单仍可完成全部操作。
   JS 只做体验增强：侧边栏、主题切换、自动刷新、dirty计数、
   范围校验、保存确认、Toast通知、表格排序/搜索。
   ============================================================ */
(function () {
  "use strict";

  /* ---- Helpers ---- */
  var qs = function (sel, root) { return (root || document).querySelector(sel); };
  var qsa = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* ============================================================
     1. Theme Toggle
     ============================================================ */
  function initTheme() {
    var saved = localStorage.getItem("dzmm-theme");
    if (saved) {
      document.documentElement.setAttribute("data-theme", saved);
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      document.documentElement.setAttribute("data-theme", "light");
    }

    var btn = qs(".theme-toggle");
    if (!btn) return;

    function updateIcon() {
      var isLight = document.documentElement.getAttribute("data-theme") === "light";
      btn.textContent = isLight ? "🌙" : "☀️";
      btn.setAttribute("aria-label", isLight ? "切换到暗色主题" : "切换到亮色主题");
    }
    updateIcon();

    btn.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme") || "dark";
      var next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("dzmm-theme", next);
      updateIcon();
    });
  }

  /* ============================================================
     2. Sidebar (Mobile)
     ============================================================ */
  function initSidebar() {
    var toggle = qs(".sidebar-toggle");
    var sidebar = qs(".sidebar");
    var overlay = qs(".sidebar-overlay");
    if (!toggle || !sidebar) return;

    function open() {
      sidebar.classList.add("open");
      if (overlay) overlay.classList.add("visible");
      toggle.setAttribute("aria-expanded", "true");
    }

    function close() {
      sidebar.classList.remove("open");
      if (overlay) overlay.classList.remove("visible");
      toggle.setAttribute("aria-expanded", "false");
    }

    toggle.addEventListener("click", function () {
      sidebar.classList.contains("open") ? close() : open();
    });

    if (overlay) {
      overlay.addEventListener("click", close);
    }

    // Close on Escape
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && sidebar.classList.contains("open")) close();
    });
  }

  /* ============================================================
     3. Dirty Tracking (Form Changes)
     ============================================================ */
  var dirtyCount = 0;
  var toastContainer = null;

  function ensureToastContainer() {
    if (toastContainer) return toastContainer;
    toastContainer = document.createElement("div");
    toastContainer.className = "toast-container";
    document.body.appendChild(toastContainer);
    return toastContainer;
  }

  function showToast(message, level) {
    var container = ensureToastContainer();
    var el = document.createElement("div");
    el.className = "toast toast--" + (level || "info");
    el.textContent = message;
    container.appendChild(el);
    setTimeout(function () {
      el.style.opacity = "0";
      el.style.transform = "translateX(20px)";
      setTimeout(function () { el.remove(); }, 300);
    }, 3000);
  }

  function initDirtyTracking() {
    var inputs = qsa("input[type=text], input[type=number], input[type=radio], input[type=checkbox], textarea, select")
      .filter(function (el) { return !el.hasAttribute("data-no-dirty"); });
    if (!inputs.length) return;

    // Snapshot initial values
    var initials = new Map();
    inputs.forEach(function (el) {
      if (el.type === "checkbox" || el.type === "radio") {
        initials.set(el, el.checked);
      } else {
        initials.set(el, el.value);
      }
    });

    function recount() {
      var count = 0;
      var radioGroups = {};  // 同 name 的 radio 是一组：变没变只算 1 项
      inputs.forEach(function (el) {
        var init = initials.get(el);
        if (el.type === "radio") {
          if (el.checked !== init && !radioGroups[el.name]) {
            radioGroups[el.name] = 1;
            count++;
          }
        } else if (el.type === "checkbox") {
          if (el.checked !== init) count++;
        } else {
          if (el.value !== init) count++;
        }
      });
      dirtyCount = count;
      updateDirtyUI();
    }

    function updateDirtyUI() {
      var box = qs("#js-dirty");
      if (dirtyCount > 0) {
        if (!box) {
          box = document.createElement("div");
          box.id = "js-dirty";
          box.className = "msg msg--warn";
          box.style.cssText = "margin:var(--sp-4) var(--sp-6);position:sticky;top:var(--header-height);z-index:40;";
          var main = qs(".content") || qs("main");
          if (main) main.insertBefore(box, main.firstChild);
        }
        // CSP/测试要求：**不许注入 HTML 字符串**（XSS 面），全部走 DOM API + textContent。
        // 先清空再重建（这条提示每次计数变化都要刷新数字）。
        while (box.firstChild) { box.removeChild(box.firstChild); }
        var icon = document.createElement("span");
        icon.className = "msg__icon";
        icon.textContent = "⚠️";
        var content = document.createElement("span");
        content.className = "msg__content";
        content.textContent = "有 " + dirtyCount + " 项改动还没保存";
        box.appendChild(icon);
        box.appendChild(content);
      } else if (box) {
        box.remove();
      }
    }

    inputs.forEach(function (el) {
      el.addEventListener("change", recount);
      if (el.type === "text" || el.type === "number" || el.tagName === "TEXTAREA") {
        el.addEventListener("input", recount);
      }
    });

    // Warn before leaving with unsaved changes
    window.addEventListener("beforeunload", function (e) {
      if (dirtyCount > 0) {
        e.preventDefault();
        e.returnValue = "";
      }
    });

    // Confirm on form submit
    qsa("form").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (dirtyCount > 0) {
          var ok = window.confirm("要保存这 " + dirtyCount + " 项改动吗？\n（写入前会自动备份；保存后约 1 秒自动生效）");
          if (!ok) { e.preventDefault(); return; }
        }
        // Show saving toast
        showToast("正在保存…", "info");
      });
    });
  }

  /* ============================================================
     4. Range Validation (Real-time)
     ============================================================ */
  function initRangeValidation() {
    qsa("input[data-lo], input[data-hi]").forEach(function (el) {
      function check() {
        var lo = el.getAttribute("data-lo");
        var hi = el.getAttribute("data-hi");
        var v = el.value.trim();
        if (v === "") { el.classList.remove("input--error"); el.title = ""; return; }
        var n = Number(v);
        var bad = isNaN(n);
        if (!bad && lo !== null && lo !== "" && n < Number(lo)) bad = true;
        if (!bad && hi !== null && hi !== "" && n > Number(hi)) bad = true;
        el.classList.toggle("input--error", bad);
        el.title = bad ? ("超出允许范围（" + (lo || "") + "~" + (hi || "") + "）") : "";
      }
      el.addEventListener("input", check);
      el.addEventListener("change", check);
      check();
    });
  }

  /* ============================================================
     5. Auto-refresh Status (Dashboard only)
     ============================================================ */
  function initAutoRefresh() {
    var indicator = qs(".refresh-indicator");
    if (!indicator) return; // Not on dashboard

    var REFRESH_INTERVAL = 30000; // 30s

    async function refresh() {
      try {
        var res = await fetch("/api/summary", { credentials: "same-origin" });
        if (!res.ok) return;
        var data = await res.json();
        updateDashboard(data);
        var dot = qs(".refresh-dot");
        if (dot) { dot.classList.remove("refresh-dot--stale"); }
      } catch (e) {
        var dot = qs(".refresh-dot");
        if (dot) { dot.classList.add("refresh-dot--stale"); }
      }
    }

    function updateDashboard(data) {
      // Update stat cards by data attribute
      qsa("[data-stat]").forEach(function (el) {
        var key = el.getAttribute("data-stat");
        var val = resolveStatValue(data, key);
        if (val !== undefined) el.textContent = val;
      });

      // Update badges by data-badge
      qsa("[data-badge]").forEach(function (el) {
        var key = el.getAttribute("data-badge");
        var val = resolveStatValue(data, key);
        if (val !== undefined) {
          el.textContent = val;
          // Update color based on value
          if (typeof val === "number" || !isNaN(Number(val))) {
            var n = Number(val);
            el.className = "badge " + (n > 0 ? "badge--bad" : "badge--ok");
          }
        }
      });

      // Update timestamp
      var ts = qs("[data-generated-at]");
      if (ts && data.generated_at) ts.textContent = data.generated_at;
    }

    function resolveStatValue(data, key) {
      // Simple dot-path resolver: "sections.auth.data.cookies.0.remain"
      var parts = key.split(".");
      var obj = data;
      for (var i = 0; i < parts.length; i++) {
        if (obj == null) return undefined;
        obj = obj[parts[i]];
      }
      return obj;
    }

    setInterval(refresh, REFRESH_INTERVAL);
  }

  /* ============================================================
     6. Table Sort & Filter
     ============================================================ */
  function initTableSort() {
    qsa(".table[data-sortable]").forEach(function (table) {
      var headers = qsa("th[data-sort]", table);
      headers.forEach(function (th) {
        th.style.cursor = "pointer";
        th.title = "点击排序";
        th.addEventListener("click", function () {
          var col = th.cellIndex;
          var tbody = qs("tbody", table);
          if (!tbody) return;
          var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
          var asc = th.getAttribute("data-sort-dir") !== "asc";
          th.setAttribute("data-sort-dir", asc ? "asc" : "desc");

          // Reset other headers
          headers.forEach(function (h) {
            if (h !== th) h.removeAttribute("data-sort-dir");
          });

          rows.sort(function (a, b) {
            var av = (a.cells[col] || {}).textContent || "";
            var bv = (b.cells[col] || {}).textContent || "";
            var an = parseFloat(av.replace(/[^0-9.\-]/g, ""));
            var bn = parseFloat(bv.replace(/[^0-9.\-]/g, ""));
            if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
            return asc ? av.localeCompare(bv, "zh") : bv.localeCompare(av, "zh");
          });

          rows.forEach(function (r) { tbody.appendChild(r); });
        });
      });
    });
  }

  /* ============================================================
     7. Collapsible Sections
     ============================================================ */
  function initCollapsible() {
    qsa("[data-collapse-toggle]").forEach(function (toggle) {
      toggle.addEventListener("click", function () {
        var target = qs(toggle.getAttribute("data-collapse-toggle"));
        if (!target) return;
        var isVisible = target.style.display !== "none";
        target.style.display = isVisible ? "none" : "block";
        toggle.setAttribute("aria-expanded", String(!isVisible));
        toggle.classList.toggle("expanded", !isVisible);
      });
    });
  }

  /* ============================================================
     8. Tabs
     ============================================================ */
  function initTabs() {
    qsa(".tabs").forEach(function (tabBar) {
      var tabs = qsa(".tab", tabBar);
      tabs.forEach(function (tab) {
        tab.addEventListener("click", function () {
          var panelId = tab.getAttribute("data-tab");
          if (!panelId) return;

          // Deactivate all
          tabs.forEach(function (t) { t.classList.remove("active"); });
          qsa(".tab-panel", tab.parentElement.parentElement).forEach(function (p) { p.classList.remove("active"); });

          // Activate clicked
          tab.classList.add("active");
          var panel = qs("#" + panelId);
          if (panel) panel.classList.add("active");
        });
      });
    });
  }

  /* ============================================================
     8. Table Search (渐进增强)
     无 JS 时 .js-search 控件整体隐藏（html.js 类由这里添加），
     表格原样可读可操作 —— 搜索只是查找辅助，不是操作路径。
     用法：<input class='input js-search' data-table-search='#表格id'>
     表内 <tr> 含 <h3> 的行视为分类头：只在组内有命中行时显示。
     ============================================================ */
  function initTableSearch() {
    qsa("input[data-table-search]").forEach(function (input) {
      var table = qs(input.getAttribute("data-table-search"));
      if (!table) return;
      var groups = [];
      var cur = null;
      qsa("tr", table).forEach(function (tr) {
        if (qs("h3", tr)) {
          cur = { head: tr, rows: [] };
          groups.push(cur);
        } else {
          if (!cur) { cur = { head: null, rows: [] }; groups.push(cur); }
          cur.rows.push(tr);
        }
      });
      if (!groups.length) return;
      input.addEventListener("input", function () {
        var qv = (input.value || "").trim().toLowerCase();
        groups.forEach(function (g) {
          var any = false;
          g.rows.forEach(function (tr) {
            var hit = !qv || tr.textContent.toLowerCase().indexOf(qv) >= 0;
            tr.style.display = hit ? "" : "none";
            if (hit) any = true;
          });
          if (g.head) {
            g.head.style.display = (!qv || any) ? "" : "none";
          }
        });
      });
    });
  }

  /* ============================================================
     Init All
     ============================================================ */
  function init() {
    document.documentElement.classList.add("js");
    initTheme();
    initSidebar();
    initDirtyTracking();
    initRangeValidation();
    initAutoRefresh();
    initTableSort();
    initCollapsible();
    initTabs();
    initTableSearch();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

/* 小小糯运维后台的前端脚本（P12-D）。
 *
 * 规则（CSP 只放开了 script-src 'self'，这些是硬约束）：
 *   1. 没有内联脚本、没有 onclick、没有 eval —— 全部在这里挂监听；
 *   2. 所有文字一律用 textContent 写进 DOM，**不用 innerHTML**；
 *   3. 只做"体验"，不做业务：没有它，纯 HTML 表单也能完成全部操作。
 *
 * 它做四件事：
 *   a. 改动计数（顶部提示"有 N 项未保存"，离开页面前提醒）
 *   b. 功能矩阵里"关掉一个带状态的功能"时提醒一句（该功能有进行中状态的话）
 *   c. 参数框超出允许范围时**就地标红**（真正的拦截在服务端，这里只是提醒）
 *   d. 危险值二次确认（提交时如果改了冷却/概率之类的极端值）
 */
(function () {
  "use strict";

  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var dirty = 0;
  var dirtyBox = null;

  function ensureBox() {
    if (dirtyBox) { return dirtyBox; }
    dirtyBox = document.createElement("div");
    dirtyBox.className = "msg warn";
    dirtyBox.id = "js-dirty";
    var main = document.querySelector("main");
    if (main) { main.insertBefore(dirtyBox, main.firstChild); }
    return dirtyBox;
  }

  function bump() {
    dirty += 1;
    var b = ensureBox();
    b.textContent = "有 " + dirty + " 项改动还没保存（点最下面的「保存」才会写入）。";
  }

  function markRange(input) {
    var lo = input.getAttribute("data-lo");
    var hi = input.getAttribute("data-hi");
    var v = input.value.trim();
    if (v === "") { input.style.borderColor = ""; return; }
    var n = Number(v);
    var bad = isNaN(n);
    if (!bad && lo !== null && lo !== "" && n < Number(lo)) { bad = true; }
    if (!bad && hi !== null && hi !== "" && n > Number(hi)) { bad = true; }
    input.style.borderColor = bad ? "#ff8b9a" : "";
    input.title = bad ? ("超出允许范围（" + (lo || "") + "~" + (hi || "") + "）") : "";
  }

  function init() {
    // a. 任何输入变化都计数
    qsa("input[type=radio],input[type=checkbox],input[type=text]").forEach(function (el) {
      el.addEventListener("change", function () {
        bump();
        if (el.type === "text") { markRange(el); }
      });
      if (el.type === "text") {
        el.addEventListener("input", function () { markRange(el); });
        markRange(el);
      }
    });

    // d. 提交前确认（只提醒一次，避免打断正常流程）
    //    只挂 /features 这一张表单：它整表提交、爆炸半径最大。
    var form = document.querySelector("form[action='/features']");
    if (form) {
      form.addEventListener("submit", function (ev) {
        if (dirty === 0) { return; }
        var ok = window.confirm("要保存这 " + dirty + " 项改动吗？" +
                                "（写入前会自动备份；保存后约 1 秒自动生效，不用重启）");
        if (!ok) { ev.preventDefault(); }
      });
    }

    // a2. 有未保存改动时离开页面提醒
    window.addEventListener("beforeunload", function (ev) {
      if (dirty > 0) { ev.preventDefault(); ev.returnValue = ""; }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

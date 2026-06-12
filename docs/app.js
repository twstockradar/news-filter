/* 每日選聞 — 前端篩選邏輯(純 vanilla,資料來自 data.js 的 window.NEWS_DATA) */
(function () {
  "use strict";

  var DATA = window.NEWS_DATA || { items: [], cat_labels: {}, src_label: "公司高層" };
  var CAT_LABELS = DATA.cat_labels || {};
  var ORIGIN_LABELS = DATA.origin_labels || {};
  var CAT_KEYS = Object.keys(CAT_LABELS);
  var ITEMS = (DATA.items || []).slice().sort(function (a, b) { return b.ts - a.ts; });
  var DOW = ["一", "二", "三", "四", "五", "六", "日"];

  // ---------- 週索引 ----------
  // weeks: 由新到舊排序的 [{week, range, monday(Date)}]
  var weekMap = {};
  ITEMS.forEach(function (it) {
    if (!weekMap[it.week]) weekMap[it.week] = { week: it.week, range: it.week_range, dates: {} };
    weekMap[it.week].dates[it.date] = true;
  });
  var weeks = Object.keys(weekMap).sort().reverse().map(function (w) { return weekMap[w]; });

  function mondayOf(dateStr) {
    var p = dateStr.split("-");
    var d = new Date(+p[0], +p[1] - 1, +p[2]);
    var wd = (d.getDay() + 6) % 7; // 0=Mon
    d.setDate(d.getDate() - wd);
    return d;
  }
  function fmtDate(d) { // 本地欄位組字串,不用 toISOString(會在 +8 倒退一天)
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  weeks.forEach(function (w) {
    w.monday = mondayOf(Object.keys(w.dates)[0]);
  });

  // ---------- 狀態 ----------
  var state = {
    cats: {},          // {price:true,...} 多選 OR
    exec: false,       // 公司高層(命中六詞;僅靠標題澄清者濾掉)
    q: "",
    weekIdx: 0,        // weeks[] 索引,0 = 最新週
    day: null          // "YYYY-MM-DD" 或 null(整週)
  };

  // ---------- 工具 ----------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function anyCatSelected() {
    return CAT_KEYS.some(function (k) { return state.cats[k]; });
  }
  function searchBlob(it) {
    if (!it._blob) {
      var hitWords = [];
      CAT_KEYS.forEach(function (k) { if (it.hits[k]) hitWords = hitWords.concat(it.hits[k]); });
      it._blob = (it.title + " " + (it.tags || []).join(" ") + " " + (it.stocks || []).join(" ") +
        " " + (it.summary || "") + " " + hitWords.join(" ") +
        " " + (ORIGIN_LABELS[it.origin] || "")).toLowerCase();
    }
    return it._blob;
  }

  // ---------- 篩選管線 ----------
  function passCats(it) {
    if (!anyCatSelected()) return true;
    return it.cats.some(function (k) { return state.cats[k]; });
  }
  function passExec(it) { return !state.exec || (it.src && it.src.length > 0); }
  function passQ(it) {
    if (!state.q) return true;
    var terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
    var blob = searchBlob(it);
    return terms.every(function (t) { return blob.indexOf(t) !== -1; });
  }
  function passWeek(it) { return weeks.length === 0 || it.week === weeks[state.weekIdx].week; }
  function passDay(it) { return !state.day || it.date === state.day; }

  // ---------- 渲染:分類籤 ----------
  var $catFilters = document.getElementById("cat-filters");
  function renderChips() {
    // 數字 = 本週範圍 + 搜尋條件下,各分類的則數(不受分類選取影響)
    var scope = ITEMS.filter(function (it) { return passWeek(it) && passDay(it) && passQ(it); });
    var html = CAT_KEYS.map(function (k) {
      var n = scope.filter(function (it) { return it.cats.indexOf(k) !== -1 && passExec(it); }).length;
      return '<button class="filter-chip' + (state.cats[k] ? " on" : "") + '" style="--c:var(--c-' + k + ')" data-cat="' + k + '">' +
        esc(CAT_LABELS[k]) + ' <span class="n">' + n + "</span></button>";
    });
    var nExec = scope.filter(function (it) { return it.src && it.src.length > 0 && passCats(it); }).length;
    html.push('<button class="filter-chip exec' + (state.exec ? " on" : "") + '" style="--c:var(--c-exec)" data-exec="1">' +
      esc(DATA.src_label) + ' <span class="n">' + nExec + "</span></button>");
    $catFilters.innerHTML = html.join("");
  }
  $catFilters.addEventListener("click", function (e) {
    var btn = e.target.closest(".filter-chip");
    if (!btn) return;
    if (btn.dataset.exec) state.exec = !state.exec;
    else state.cats[btn.dataset.cat] = !state.cats[btn.dataset.cat];
    render();
  });

  // ---------- 渲染:週導覽 ----------
  var $weekLabel = document.getElementById("week-label");
  var $dayStrip = document.getElementById("day-strip");
  var $prev = document.getElementById("week-prev");
  var $next = document.getElementById("week-next");

  function renderWeek() {
    if (weeks.length === 0) { $weekLabel.textContent = "—"; return; }
    var w = weeks[state.weekIdx];
    $weekLabel.textContent = w.week + "(" + w.range + ")";
    $prev.disabled = state.weekIdx >= weeks.length - 1; // 更舊
    $next.disabled = state.weekIdx <= 0;                // 更新

    var counts = {};
    ITEMS.forEach(function (it) {
      if (it.week === w.week && passCats(it) && passExec(it) && passQ(it)) {
        counts[it.date] = (counts[it.date] || 0) + 1;
      }
    });
    var todayStr = fmtDate(new Date());
    var html = "";
    for (var i = 0; i < 7; i++) {
      var d = new Date(w.monday);
      d.setDate(d.getDate() + i);
      var ds = fmtDate(d);
      var n = counts[ds] || 0;
      html += '<button class="day-cell' + (n === 0 ? " zero" : "") +
        (state.day === ds ? " sel" : "") + (ds === todayStr ? " today" : "") +
        '" data-date="' + ds + '">' +
        '<span class="dow">' + DOW[i] + "</span>" +
        '<span class="dnum">' + (d.getMonth() + 1) + "/" + d.getDate() + "</span>" +
        '<span class="cnt">' + (n || "") + "</span></button>";
    }
    $dayStrip.innerHTML = html;
  }
  $prev.addEventListener("click", function () {
    if (state.weekIdx < weeks.length - 1) { state.weekIdx++; state.day = null; render(); }
  });
  $next.addEventListener("click", function () {
    if (state.weekIdx > 0) { state.weekIdx--; state.day = null; render(); }
  });
  $dayStrip.addEventListener("click", function (e) {
    var cell = e.target.closest(".day-cell");
    if (!cell) return;
    state.day = (state.day === cell.dataset.date) ? null : cell.dataset.date;
    render();
  });

  // ---------- 渲染:新聞列表 ----------
  var $list = document.getElementById("news-list");
  var $count = document.getElementById("result-count");

  function cardHTML(it) {
    var mainCat = it.cats[0];
    var hitChips = it.cats.map(function (k) {
      return '<span class="hit" style="--c:var(--c-' + k + ')"><b>' + esc(CAT_LABELS[k]) + "</b>:" +
        esc(it.hits[k].slice(0, 3).join("、")) + "</span>";
    }).join("");
    var srcWords = (it.src || []).slice();
    (it.src_title || []).forEach(function (w) { srcWords.push("標題" + w); });
    var srcChips = srcWords.length
      ? '<span class="hit exec">' + esc(DATA.src_label) + ":" + esc(srcWords.join("、")) + "</span>" : "";
    var stocks = (it.stocks || []).length
      ? '<span class="stock">' + esc(it.stocks.join(" ")) + "</span>" : "";
    var origin = (it.origin && it.origin !== "cnyes" && ORIGIN_LABELS[it.origin])
      ? '<span class="origin">' + esc(ORIGIN_LABELS[it.origin]) + "</span>" : "";
    var thumb = it.cover
      ? '<a class="thumb" href="' + esc(it.url) + '" target="_blank" rel="noopener">' +
        '<img src="' + esc(it.cover) + '" alt="" loading="lazy" onerror="this.parentNode.style.display=\'none\'"></a>'
      : "";
    var dupes = "";
    if (it.dupes && it.dupes.length) {
      dupes = '<details class="dupes"><summary>另有 ' + it.dupes.length + " 則相似報導</summary><ul>" +
        it.dupes.map(function (d) {
          return '<li><span class="dtime">' + esc(d.date.slice(5)) + " " + esc(d.time) + "</span>" +
            '<a href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.title) + "</a></li>";
        }).join("") + "</ul></details>";
    }
    return '<article class="news-card" style="--cat:var(--c-' + mainCat + ')">' +
      '<div class="card-body">' +
      '<h3 class="card-title"><a href="' + esc(it.url) + '" target="_blank" rel="noopener">' + esc(it.title) + "</a></h3>" +
      '<div class="card-meta"><span>' + esc(it.time) + "</span>" + origin + stocks + "</div>" +
      '<div class="hit-row">' + hitChips + srcChips + "</div>" +
      (it.summary ? '<p class="card-summary">' + esc(it.summary) + "</p>" : "") +
      dupes +
      "</div>" + thumb + "</article>";
  }

  function renderList() {
    var shown = ITEMS.filter(function (it) {
      return passWeek(it) && passDay(it) && passCats(it) && passExec(it) && passQ(it);
    });
    var nDupes = shown.reduce(function (s, it) { return s + (it.dupes ? it.dupes.length : 0); }, 0);
    $count.textContent = "共 " + shown.length + " 則" + (nDupes ? "(另含 " + nDupes + " 則相似報導)" : "");

    if (shown.length === 0) {
      $list.innerHTML = '<div class="empty"><div class="big">本範圍沒有符合的新聞</div>換個週次或放寬篩選試試</div>';
      return;
    }
    // 依日期分組(新到舊)
    var groups = [];
    var byDate = {};
    shown.forEach(function (it) {
      if (!byDate[it.date]) { byDate[it.date] = []; groups.push(it.date); }
      byDate[it.date].push(it);
    });
    $list.innerHTML = groups.map(function (date) {
      var p = date.split("-");
      var d = new Date(+p[0], +p[1] - 1, +p[2]);
      var head = (+p[1]) + "月" + (+p[2]) + "日 週" + DOW[(d.getDay() + 6) % 7];
      return '<section class="day-group"><div class="day-head">' + head +
        '<span class="sub">' + byDate[date].length + " 則</span></div>" +
        byDate[date].map(cardHTML).join("") + "</section>";
    }).join("");
  }

  // ---------- 搜尋 / 清除 ----------
  var $search = document.getElementById("search");
  var $clear = document.getElementById("clear-filters");
  var debounce = null;
  $search.addEventListener("input", function () {
    clearTimeout(debounce);
    debounce = setTimeout(function () {
      state.q = $search.value.trim();
      render();
    }, 150);
  });
  $clear.addEventListener("click", function () {
    state.cats = {}; state.exec = false; state.q = ""; state.day = null;
    $search.value = "";
    render();
  });

  // ---------- 總渲染 ----------
  function render() {
    renderChips();
    renderWeek();
    renderList();
    $clear.hidden = !(anyCatSelected() || state.exec || state.q || state.day);
  }

  // 頁尾更新時間 = 最新一則的時間
  if (ITEMS.length) {
    document.getElementById("updated-at").textContent =
      "更新至 " + ITEMS[0].date + " " + ITEMS[0].time;
  }
  render();
})();

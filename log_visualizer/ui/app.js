/* log_visualizer UI（オフライン・依存なし。CallGraph 描画のみ同梱の viz.js を使用） */
(function () {
  'use strict';

  // ---- 列定義（renderer/payload_writer.py の EVENT_COLUMNS と一致させる） ----
  const C = {
    SEQ: 0, LINE_NO: 1, TS: 2, LEVEL: 3, KIND: 4, LANE: 5, FUNC: 6, FILE: 7, LINE: 8,
    FID: 9, PARENT: 10, ARROW: 11, DEPTH: 12, HOPS: 13, MSG: 14, MODULE: 15, THREAD: 16,
    TICK: 17, RAW: 18,
  };
  const ARROW_DASH = { 1: [], 2: [2, 4], 3: [7, 4] };
  const THREAD_COLORS = ['#2563eb', '#db2777', '#059669', '#d97706', '#7c3aed', '#0891b2', '#dc2626', '#65a30d'];
  const HEADER_H = 30;
  const TIME_W = 104;
  const BASE_ROW = 46;
  const MAX_SPACER = 8000000;
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------------------
  // payload 読込み
  // ---------------------------------------------------------------------------
  async function loadPayload() {
    const el = $('lv-payload');
    const b64 = el.textContent.replace(/\s+/g, '');
    el.textContent = '';
    if (typeof DecompressionStream === 'undefined') {
      throw new Error('このブラウザは DecompressionStream に対応していません。Chrome / Edge / Firefox の最新版で開いてください。');
    }
    const parts = [];
    const STEP = 4 * 1024 * 1024;
    for (let i = 0; i < b64.length; i += STEP) {
      const bin = atob(b64.slice(i, i + STEP));
      const arr = new Uint8Array(bin.length);
      for (let j = 0; j < bin.length; j++) arr[j] = bin.charCodeAt(j);
      parts.push(arr);
    }
    const stream = new Blob(parts).stream().pipeThrough(new DecompressionStream('gzip'));
    return JSON.parse(await new Response(stream).text());
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function pad(n, w) { return String(n).padStart(w, '0'); }
  function debounce(fn, ms) {
    let t = 0;
    return function () { clearTimeout(t); const a = arguments; t = setTimeout(() => fn.apply(null, a), ms); };
  }
  function basename(p) { return String(p || '').split('/').pop(); }

  // ---------------------------------------------------------------------------
  // 状態
  // ---------------------------------------------------------------------------
  let P, E, N, META, LANES, FUNCS, FILES, MODS, THREADS, G;
  let parentIdx, childCount, seqIndex, funcLower;
  let vis = new Int32Array(0);
  let visPos;
  let ys = new Float64Array(1);
  let rowH = BASE_ROW;
  let laneCol;
  let laneOrder = [];
  let colors = {};
  let contentH = 0;
  let contentY = 0;
  const state = {
    laneOn: null, levelOn: null, kindOn: null, threadOn: null,
    funcText: '', tsFrom: null, tsTo: null,
    searchText: '', searchMode: 'all', searchOnly: false, matches: null, matchCount: 0, matchCursor: -1,
    collapsed: new Set(), zoom: 1, laneW: 200, timeGap: false, selected: -1,
  };

  function readColors() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n) => cs.getPropertyValue(n).trim();
    colors = {
      bg: v('--bg'), surface: v('--surface'), surface2: v('--surface-2'), border: v('--border'), text: v('--text'), muted: v('--muted'),
      accent: v('--accent'), laneLine: v('--lane-line'), box: v('--box'), boxBorder: v('--box-border'),
      warnFill: v('--warn-fill'), warnBorder: v('--warn-border'), errorFill: v('--error-fill'), errorBorder: v('--error-border'),
      traceFill: v('--trace-fill'), rawFill: v('--raw-fill'), match: v('--match'), headerBg: v('--header-bg'),
      arrow: { 1: v('--sync'), 2: v('--async'), 3: v('--callback') },
    };
  }

  // ---------------------------------------------------------------------------
  // 時刻
  // ---------------------------------------------------------------------------
  function fmtTs(rel, withDate) {
    if (rel == null || META.base_ts == null) return '';
    const d = new Date(META.base_ts + rel);
    const t = pad(d.getUTCHours(), 2) + ':' + pad(d.getUTCMinutes(), 2) + ':' + pad(d.getUTCSeconds(), 2) + '.' + pad(d.getUTCMilliseconds(), 3);
    if (!withDate) return t;
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1, 2) + '-' + pad(d.getUTCDate(), 2) + ' ' + t;
  }
  function parseTsInput(text) {
    const s = text.trim();
    if (!s) return null;
    const m = s.match(/^(?:(\d{4})-(\d{2})-(\d{2})[ T])?(\d{1,2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,3}))?)?$/);
    if (!m || META.base_ts == null) return undefined;
    const base = new Date(META.base_ts);
    const y = m[1] ? +m[1] : base.getUTCFullYear();
    const mo = m[2] ? +m[2] - 1 : base.getUTCMonth();
    const da = m[3] ? +m[3] : base.getUTCDate();
    const ms = Date.UTC(y, mo, da, +m[4], +m[5], m[6] ? +m[6] : 0, m[7] ? +(m[7] + '00').slice(0, 3) : 0);
    return ms - META.base_ts;
  }

  // ---------------------------------------------------------------------------
  // 初期化
  // ---------------------------------------------------------------------------
  function prepare() {
    E = P.events; N = E.length; META = P.meta; LANES = P.lanes;
    FUNCS = P.strings.func; FILES = P.strings.file; MODS = P.strings.module; THREADS = P.strings.thread;
    let maxSeq = 0;
    for (let i = 0; i < N; i++) if (E[i][C.SEQ] > maxSeq) maxSeq = E[i][C.SEQ];
    seqIndex = new Int32Array(maxSeq + 1).fill(-1);
    for (let i = 0; i < N; i++) seqIndex[E[i][C.SEQ]] = i;
    parentIdx = new Int32Array(N).fill(-1);
    childCount = new Int32Array(N);
    for (let i = 0; i < N; i++) {
      const ps = E[i][C.PARENT];
      if (ps >= 0 && ps < seqIndex.length) {
        const p = seqIndex[ps];
        if (p >= 0 && p !== i) { parentIdx[i] = p; childCount[p]++; }
      }
    }
    mergeLanesIgnoringCase();
    funcLower = FUNCS.map((f) => f.toLowerCase());
    visPos = new Int32Array(N).fill(-1);
    state.laneOn = new Uint8Array(LANES.length).fill(1);
    state.levelOn = new Uint8Array(P.levels.length).fill(1);
    state.kindOn = new Uint8Array(P.kinds.length).fill(1);
    state.threadOn = new Uint8Array(THREADS.length + 1).fill(1);
    laneCol = new Int32Array(LANES.length).fill(-1);
    prepareGraph();
  }

  // "RIM" と "rim" のように大文字小文字だけが違うレーンは、件数の多い方に統合する
  function mergeLanesIgnoringCase() {
    const counts = new Int32Array(LANES.length);
    for (let i = 0; i < N; i++) counts[E[i][C.LANE]]++;
    const best = new Map();
    LANES.forEach((name, l) => {
      const key = name.toLowerCase();
      const cur = best.get(key);
      if (cur === undefined || counts[l] > counts[cur]) best.set(key, l);
    });
    const remap = LANES.map((name) => best.get(name.toLowerCase()));
    for (let i = 0; i < N; i++) E[i][C.LANE] = remap[E[i][C.LANE]];
  }

  function prepareGraph() {
    const g = P.graph;
    const n = g.nodes.length;
    const out = Array.from({ length: n }, () => []);
    const inc = Array.from({ length: n }, () => []);
    g.edges.forEach((e, i) => { out[e[0]].push(i); inc[e[1]].push(i); });
    const logged = new Uint8Array(n);
    const firstLog = new Int32Array(n).fill(-1);
    for (let i = 0; i < N; i++) {
      const f = E[i][C.FID];
      if (f >= 0) { logged[f] = 1; if (firstLog[f] < 0) firstLog[f] = i; }
    }
    const lower = g.nodes.map((s) => s.toLowerCase());
    G = { nodes: g.nodes, info: g.info, files: g.files, modules: g.modules, vias: g.vias, edges: g.edges, out, inc, logged, firstLog, lower };
  }

  // ---------------------------------------------------------------------------
  // フィルタ UI
  // ---------------------------------------------------------------------------
  function countBy(col, size) {
    const c = new Int32Array(size);
    for (let i = 0; i < N; i++) { const v = E[i][col]; c[v >= 0 ? v : size - 1]++; }
    return c;
  }
  function buildCheckList(container, names, counts, onArr, swatches) {
    container.innerHTML = names.map((name, i) => (counts && counts[i] === 0) ? '' :
      '<label title="' + esc(name) + '"><input type="checkbox" data-i="' + i + '"' + (onArr[i] ? ' checked' : '') + '>' +
      (swatches ? '<span class="swatch" style="background:' + swatches[i] + '"></span>' : '') +
      '<span class="name">' + esc(name) + '</span>' + (counts ? '<span class="count">' + counts[i].toLocaleString() + '</span>' : '') + '</label>').join('');
    container.addEventListener('change', (ev) => {
      const t = ev.target;
      if (t && t.dataset && t.dataset.i != null) { onArr[+t.dataset.i] = t.checked ? 1 : 0; refresh(); }
    });
  }

  function initFilters() {
    const laneCounts = countBy(C.LANE, LANES.length);
    buildCheckList($('lane-list'), LANES, laneCounts, state.laneOn);
    $('lane-search').addEventListener('input', (ev) => {
      const q = ev.target.value.toLowerCase();
      $('lane-list').querySelectorAll('label').forEach((l) => { l.hidden = q && !l.title.toLowerCase().includes(q); });
    });
    buildCheckList($('level-list'), P.levels, countBy(C.LEVEL, P.levels.length), state.levelOn);
    const kindNames = P.kinds.map((k) => ({ STRUCTURED: '構造化ログ', TRACE: 'トレース（*****形式）', RAW: 'その他の出力' }[k] || k));
    buildCheckList($('kind-list'), kindNames, countBy(C.KIND, P.kinds.length), state.kindOn);
    if (META.has_thread) {
      $('thread-group').hidden = false;
      const names = THREADS.concat(['(なし)']);
      buildCheckList($('thread-list'), names, countBy(C.THREAD, names.length), state.threadOn,
        names.map((_, i) => THREAD_COLORS[i % THREAD_COLORS.length]));
    }
    document.querySelectorAll('[data-all],[data-none]').forEach((b) => b.addEventListener('click', () => {
      const which = b.dataset.all || b.dataset.none;
      const on = b.dataset.all ? 1 : 0;
      const arr = which === 'lane' ? state.laneOn : state.threadOn;
      const list = $(which + '-list');
      list.querySelectorAll('label').forEach((l) => {
        if (l.hidden) return;
        const cb = l.querySelector('input'); cb.checked = !!on; arr[+cb.dataset.i] = on;
      });
      refresh();
    }));
    $('func-filter').addEventListener('input', debounce((ev) => { state.funcText = ev.target.value.trim().toLowerCase(); refresh(); }, 200));
    const tsHandler = (id, key) => debounce(() => {
      const el = $(id); const v = parseTsInput(el.value);
      el.style.borderColor = v === undefined ? colors.errorBorder : '';
      state[key] = v === undefined ? null : v; refresh();
    }, 300);
    $('ts-from').addEventListener('input', tsHandler('ts-from', 'tsFrom'));
    $('ts-to').addEventListener('input', tsHandler('ts-to', 'tsTo'));
    $('ts-from-sel').addEventListener('click', () => setTsFromSelection('ts-from', 'tsFrom'));
    $('ts-to-sel').addEventListener('click', () => setTsFromSelection('ts-to', 'tsTo'));
    $('ts-clear').addEventListener('click', () => { $('ts-from').value = ''; $('ts-to').value = ''; state.tsFrom = state.tsTo = null; refresh(); });
    $('reset-filters').addEventListener('click', resetFilters);
    $('expand-all').addEventListener('click', () => { state.collapsed.clear(); refresh(); });
  }
  function setTsFromSelection(id, key) {
    if (state.selected < 0) return;
    const ts = E[state.selected][C.TS];
    if (ts == null) return;
    $(id).value = fmtTs(ts, true); state[key] = ts; refresh();
  }
  function resetFilters() {
    [state.laneOn, state.levelOn, state.kindOn, state.threadOn].forEach((a) => a.fill(1));
    document.querySelectorAll('#filters input[type=checkbox]').forEach((cb) => { cb.checked = true; });
    ['func-filter', 'ts-from', 'ts-to', 'lane-search'].forEach((id) => { $(id).value = ''; });
    $('lane-list').querySelectorAll('label').forEach((l) => { l.hidden = false; });
    state.funcText = ''; state.tsFrom = state.tsTo = null;
    refresh();
  }

  // ---------------------------------------------------------------------------
  // 検索
  // ---------------------------------------------------------------------------
  function haystack(ev, mode) {
    const f = ev[C.FUNC] >= 0 ? FUNCS[ev[C.FUNC]] : '';
    if (mode === 'func') return f + ' ' + (ev[C.FID] >= 0 ? G.nodes[ev[C.FID]] : '');
    if (mode === 'msg') return ev[C.MSG];
    return f + ' ' + ev[C.MSG] + ' ' + (ev[C.MODULE] >= 0 ? MODS[ev[C.MODULE]] : '') + ' ' +
      (ev[C.FILE] >= 0 ? FILES[ev[C.FILE]] + ':' + ev[C.LINE] : '') + ' ' + ev[C.RAW];
  }
  function runSearch() {
    const q = state.searchText.toLowerCase();
    if (!q) { state.matches = null; state.matchCount = 0; }
    else {
      const m = new Uint8Array(N); let count = 0;
      for (let i = 0; i < N; i++) if (haystack(E[i], state.searchMode).toLowerCase().includes(q)) { m[i] = 1; count++; }
      state.matches = m; state.matchCount = count;
    }
    state.matchCursor = -1;
    refresh();
  }
  function jumpMatch(dir) {
    if (!state.matches || !vis.length) return;
    const start = state.selected >= 0 && visPos[state.selected] >= 0 ? visPos[state.selected] : (dir > 0 ? -1 : vis.length);
    for (let k = 1; k <= vis.length; k++) {
      const p = (start + dir * k + vis.length * 2) % vis.length;
      if (state.matches[vis[p]]) { select(vis[p], true); updateSearchCount(); return; }
    }
  }
  function updateSearchCount() {
    if (!state.matches) { $('search-count').textContent = ''; return; }
    let pos = 0, total = 0;
    for (let p = 0; p < vis.length; p++) {
      if (state.matches[vis[p]]) { total++; if (vis[p] === state.selected) pos = total; }
    }
    $('search-count').textContent = (pos ? pos + ' / ' : '') + total.toLocaleString() + ' 件' +
      (total !== state.matchCount ? '（全体 ' + state.matchCount.toLocaleString() + '）' : '');
  }
  function initSearch() {
    const run = debounce(() => { state.searchText = $('search-text').value.trim(); runSearch(); }, 250);
    $('search-text').addEventListener('input', run);
    $('search-text').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); jumpMatch(ev.shiftKey ? -1 : 1); } });
    $('search-mode').addEventListener('change', (ev) => { state.searchMode = ev.target.value; runSearch(); });
    $('search-only').addEventListener('change', (ev) => { state.searchOnly = ev.target.checked; refresh(); });
    $('search-next').addEventListener('click', () => jumpMatch(1));
    $('search-prev').addEventListener('click', () => jumpMatch(-1));
  }

  // ---------------------------------------------------------------------------
  // 表示対象の計算とレイアウト
  // ---------------------------------------------------------------------------
  function computeVisible() {
    const out = new Int32Array(N);
    const hidden = new Uint8Array(N);
    let n = 0;
    const s = state;
    const hasThread = META.has_thread;
    for (let i = 0; i < N; i++) {
      const p = parentIdx[i];
      if (p >= 0 && (hidden[p] || s.collapsed.has(p))) { hidden[i] = 1; continue; }
      const ev = E[i];
      if (!s.laneOn[ev[C.LANE]] || !s.levelOn[ev[C.LEVEL]] || !s.kindOn[ev[C.KIND]]) continue;
      if (hasThread) { const t = ev[C.THREAD]; if (!s.threadOn[t >= 0 ? t : THREADS.length]) continue; }
      const ts = ev[C.TS];
      if (ts != null && ((s.tsFrom != null && ts < s.tsFrom) || (s.tsTo != null && ts > s.tsTo))) continue;
      if (s.funcText) {
        const f = ev[C.FUNC] >= 0 ? funcLower[ev[C.FUNC]] : '';
        const fid = ev[C.FID] >= 0 ? G.lower[ev[C.FID]] : '';
        if (!f.includes(s.funcText) && !fid.includes(s.funcText)) continue;
      }
      if (s.searchOnly && s.matches && !s.matches[i]) continue;
      out[n++] = i;
    }
    vis = out.slice(0, n);
    visPos.fill(-1);
    for (let k = 0; k < n; k++) visPos[vis[k]] = k;
  }

  function computeLayout() {
    rowH = Math.max(10, BASE_ROW * state.zoom);
    ys = new Float64Array(vis.length + 1);
    let y = 6;
    let prevTs = null;
    for (let k = 0; k < vis.length; k++) {
      const ts = E[vis[k]][C.TS];
      if (state.timeGap && prevTs != null && ts != null && ts > prevTs) {
        y += Math.min(160, Math.log10(1 + ts - prevTs) * 14) * state.zoom;
      }
      if (ts != null) prevTs = ts;
      ys[k] = y;
      y += rowH;
    }
    ys[vis.length] = y;
    contentH = y + 40;
    // レーン（表示中のもののみ・初出順）
    laneOrder = [];
    laneCol.fill(-1);
    const used = new Uint8Array(LANES.length);
    for (let k = 0; k < vis.length; k++) used[E[vis[k]][C.LANE]] = 1;
    for (let l = 0; l < LANES.length; l++) {
      if (used[l]) { laneCol[l] = laneOrder.length; laneOrder.push(l); }
    }
    const spacer = $('seq-spacer');
    spacer.style.height = Math.min(contentH + HEADER_H, MAX_SPACER) + 'px';
    spacer.style.width = (TIME_W + laneOrder.length * state.laneW + 60) + 'px';
  }

  function refresh() {
    computeVisible();
    computeLayout();
    setContentY(contentY);
    $('visible-count').textContent = '表示 ' + vis.length.toLocaleString() + ' / ' + N.toLocaleString() + ' 件';
    updateSearchCount();
    requestDraw();
  }

  // ---------------------------------------------------------------------------
  // 仮想スクロール（コンテンツ座標 ↔ スクロール位置）
  // ---------------------------------------------------------------------------
  const scroller = () => $('seq-scroll');
  function viewH() { return scroller().clientHeight - HEADER_H; }
  function maxContentY() { return Math.max(0, contentH - viewH()); }
  function spacerRange() { return Math.max(0, $('seq-spacer').offsetHeight + HEADER_H - scroller().clientHeight); }
  let syncingScroll = false;
  function setContentY(y) {
    contentY = Math.max(0, Math.min(maxContentY(), y));
    const range = spacerRange();
    const max = maxContentY();
    syncingScroll = true;
    scroller().scrollTop = max > 0 ? contentY / max * range : 0;
    requestDraw();
  }
  function onScroll() {
    if (syncingScroll) { syncingScroll = false; requestDraw(); return; }
    const range = spacerRange();
    contentY = range > 0 ? scroller().scrollTop / range * maxContentY() : 0;
    requestDraw();
  }
  function rowAt(cy) {
    let lo = 0, hi = vis.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (ys[mid] <= cy) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  // ---------------------------------------------------------------------------
  // 描画
  // ---------------------------------------------------------------------------
  let drawPending = false;
  function requestDraw() {
    if (drawPending) return;
    drawPending = true;
    requestAnimationFrame(() => { drawPending = false; draw(); });
  }
  function boxRect(k) {
    const ev = E[vis[k]];
    const col = laneCol[ev[C.LANE]];
    const indent = Math.min(ev[C.DEPTH], 8) * 5;
    const x = TIME_W + col * state.laneW + 8 + indent - scroller().scrollLeft;
    const y = HEADER_H + ys[k] - contentY + 3;
    return { x: x, y: y, w: state.laneW - 16 - indent, h: rowH - 6 };
  }
  const textCache = new Map();
  function fit(ctx, text, maxW, font) {
    if (maxW <= 8) return '';
    const key = font + '|' + maxW + '|' + text;
    const hit = textCache.get(key);
    if (hit !== undefined) return hit;
    let out = text;
    if (ctx.measureText(text).width > maxW) {
      let lo = 0, hi = text.length;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (ctx.measureText(text.slice(0, mid) + '…').width <= maxW) lo = mid; else hi = mid - 1;
      }
      out = text.slice(0, lo) + '…';
    }
    if (textCache.size > 5000) textCache.clear();
    textCache.set(key, out);
    return out;
  }
  function boxLabel(ev) {
    if (ev[C.FUNC] >= 0) return FUNCS[ev[C.FUNC]] + '()';
    return ev[C.KIND] === P.kinds.indexOf('RAW') ? '(出力)' : '(不明)';
  }

  function draw() {
    const sc = scroller();
    const canvas = $('seq-canvas');
    const W = sc.clientWidth, H = sc.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
      canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = colors.bg;
    ctx.fillRect(0, 0, W, H);
    const x0 = sc.scrollLeft;
    const LW = state.laneW;

    // レーンの縦線
    ctx.strokeStyle = colors.laneLine; ctx.lineWidth = 1; ctx.setLineDash([3, 4]);
    for (let c = 0; c < laneOrder.length; c++) {
      const cx = Math.round(TIME_W + c * LW + LW / 2 - x0) + 0.5;
      if (cx < TIME_W - 4 || cx > W + 4) continue;
      ctx.beginPath(); ctx.moveTo(cx, HEADER_H); ctx.lineTo(cx, H); ctx.stroke();
    }
    ctx.setLineDash([]);

    if (!vis.length) {
      ctx.fillStyle = colors.muted; ctx.font = '13px sans-serif';
      ctx.fillText('表示対象のログがありません（フィルタを確認してください）', TIME_W + 20, HEADER_H + 40);
      drawHeader(ctx, W, x0);
      return;
    }

    const first = Math.max(0, rowAt(contentY) - 1);
    let last = first;
    while (last < vis.length && ys[last] < contentY + H) last++;
    const arrowLast = Math.min(vis.length, last + 300);
    const sel = state.selected;

    // 矢印（ボックスの下に描く）
    ctx.save();
    ctx.beginPath(); ctx.rect(TIME_W, HEADER_H, W - TIME_W, H - HEADER_H); ctx.clip();
    for (let k = first; k < arrowLast; k++) {
      const i = vis[k];
      const p = parentIdx[i];
      if (p < 0) continue;
      const kind = E[i][C.ARROW];
      if (!kind) continue;
      const pk = visPos[p];
      if (pk < 0) continue;
      if (k >= last && pk >= last) continue;
      drawArrow(ctx, pk, k, kind, sel >= 0 && (i === sel || p === sel));
    }
    // スレッド線
    if (META.has_thread) drawThreadLines(ctx, first, last);

    // ボックス
    const fontTitle = '600 12px "Segoe UI", "Meiryo", sans-serif';
    const fontMsg = '12px "Segoe UI", "Meiryo", sans-serif';
    const compact = rowH < 26;
    for (let k = first; k < last; k++) {
      const i = vis[k];
      const ev = E[i];
      const r = boxRect(k);
      if (r.x > W || r.x + r.w < TIME_W) continue;
      const level = P.levels[ev[C.LEVEL]];
      const kindName = P.kinds[ev[C.KIND]];
      let fill = colors.box, stroke = colors.boxBorder;
      if (level === 'WARN') { fill = colors.warnFill; stroke = colors.warnBorder; }
      else if (level === 'ERROR' || level === 'FATAL') { fill = colors.errorFill; stroke = colors.errorBorder; }
      else if (kindName === 'TRACE') fill = colors.traceFill;
      else if (kindName === 'RAW') fill = colors.rawFill;
      ctx.fillStyle = fill;
      roundRect(ctx, r.x, r.y, r.w, r.h, 5); ctx.fill();
      ctx.lineWidth = i === sel ? 2.2 : 1;
      ctx.strokeStyle = i === sel ? colors.accent : stroke;
      if (kindName === 'RAW') ctx.setLineDash([3, 3]);
      ctx.stroke(); ctx.setLineDash([]);
      if (state.matches && state.matches[i]) {
        ctx.fillStyle = colors.match; ctx.fillRect(r.x, r.y + 2, 4, r.h - 4);
      }
      if (META.has_thread && ev[C.THREAD] >= 0) {
        ctx.fillStyle = THREAD_COLORS[ev[C.THREAD] % THREAD_COLORS.length]; ctx.fillRect(r.x + r.w - 4, r.y + 2, 3, r.h - 4);
      }
      let tx = r.x + 8;
      if (childCount[i] > 0) {
        ctx.fillStyle = colors.muted; ctx.font = '10px sans-serif';
        ctx.fillText(state.collapsed.has(i) ? '▸' : '▾', r.x + 5, r.y + (compact ? r.h / 2 + 4 : 15));
        tx = r.x + 17;
      }
      const tw = r.x + r.w - tx - 6;
      ctx.fillStyle = colors.text;
      if (compact) {
        if (r.h >= 10) {
          ctx.font = '600 10px sans-serif';
          ctx.fillText(fit(ctx, boxLabel(ev) + ' ' + ev[C.MSG], tw, '10'), tx, r.y + r.h / 2 + 3.5);
        }
      } else {
        ctx.font = fontTitle;
        let title = boxLabel(ev);
        if (state.collapsed.has(i)) title += '  [+' + childCount[i] + ']';
        ctx.fillText(fit(ctx, title, tw, 't'), tx, r.y + 15);
        ctx.font = fontMsg; ctx.fillStyle = colors.muted;
        ctx.fillText(fit(ctx, ev[C.MSG], tw, 'm'), tx, r.y + 31);
      }
    }
    ctx.restore();

    // 時刻列
    ctx.fillStyle = colors.surface; ctx.fillRect(0, HEADER_H, TIME_W, H - HEADER_H);
    ctx.strokeStyle = colors.border; ctx.beginPath(); ctx.moveTo(TIME_W - 0.5, HEADER_H); ctx.lineTo(TIME_W - 0.5, H); ctx.stroke();
    ctx.font = '11px Consolas, "Cascadia Mono", monospace';
    let prev = null;
    for (let k = first; k < last; k++) {
      const ts = E[vis[k]][C.TS];
      if (rowH < 14 && ts === prev) continue;
      const y = HEADER_H + ys[k] - contentY + Math.min(rowH / 2 + 4, 18);
      if (y < HEADER_H + 8) continue;
      ctx.fillStyle = ts === prev ? colors.laneLine : colors.muted;
      ctx.fillText(fmtTs(ts), 8, y);
      prev = ts;
    }
    drawHeader(ctx, W, x0);
  }

  function drawHeader(ctx, W, x0) {
    ctx.fillStyle = colors.headerBg; ctx.fillRect(0, 0, W, HEADER_H);
    ctx.strokeStyle = colors.border; ctx.beginPath(); ctx.moveTo(0, HEADER_H - 0.5); ctx.lineTo(W, HEADER_H - 0.5); ctx.stroke();
    ctx.font = '600 12px "Segoe UI", "Meiryo", sans-serif';
    ctx.save();
    ctx.beginPath(); ctx.rect(TIME_W, 0, W - TIME_W, HEADER_H); ctx.clip();
    for (let c = 0; c < laneOrder.length; c++) {
      const x = TIME_W + c * state.laneW - x0;
      if (x > W || x + state.laneW < TIME_W) continue;
      const name = LANES[laneOrder[c]];
      ctx.fillStyle = colors.text;
      const label = fit(ctx, name, state.laneW - 14, 'h');
      const w = ctx.measureText(label).width;
      ctx.fillText(label, x + (state.laneW - w) / 2, 19);
      ctx.strokeStyle = colors.border;
      ctx.beginPath(); ctx.moveTo(x + state.laneW - 0.5, 6); ctx.lineTo(x + state.laneW - 0.5, HEADER_H - 6); ctx.stroke();
    }
    ctx.restore();
    ctx.fillStyle = colors.muted; ctx.font = '600 11px sans-serif';
    ctx.fillText('時刻', 10, 19);
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
  }

  function drawArrow(ctx, fromK, toK, kind, emphasize) {
    const a = boxRect(fromK), b = boxRect(toK);
    const ay = a.y + Math.min(a.h / 2, 18), by = b.y + Math.min(b.h / 2, 18);
    const sameLane = Math.abs(a.x - b.x) < state.laneW / 2;
    let sx, ex, c1x, c2x;
    if (sameLane) {
      sx = a.x + a.w; ex = b.x + b.w;
      const bulge = 18 + Math.min(30, (toK - fromK) * 3);
      c1x = sx + bulge; c2x = ex + bulge;
    } else if (b.x > a.x) {
      sx = a.x + a.w; ex = b.x; const d = (ex - sx) / 2; c1x = sx + d; c2x = ex - d;
    } else {
      sx = a.x; ex = b.x + b.w; const d = (sx - ex) / 2; c1x = sx - d; c2x = ex + d;
    }
    // 同じレーン内で離れた行への矢印は薄く描く（選択中のログに関係するものは強調）
    const faint = !emphasize && sameLane && toK - fromK > 3;
    const color = emphasize ? colors.accent : colors.arrow[kind];
    ctx.globalAlpha = faint ? 0.28 : 1;
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.lineWidth = emphasize ? 2.2 : 1.3;
    ctx.setLineDash(ARROW_DASH[kind] || []);
    ctx.beginPath(); ctx.moveTo(sx, ay); ctx.bezierCurveTo(c1x, ay, c2x, by, ex, by); ctx.stroke();
    ctx.setLineDash([]);
    // 矢じり（制御点が水平なので終点の接線も水平）
    const dir = ex >= c2x ? 1 : -1;
    const L = 7;
    ctx.beginPath();
    ctx.moveTo(ex, by);
    ctx.lineTo(ex - dir * L, by - L * 0.45);
    ctx.lineTo(ex - dir * L, by + L * 0.45);
    ctx.closePath(); ctx.fill();
    ctx.globalAlpha = 1;
  }

  function drawThreadLines(ctx, first, last) {
    const lastPos = new Map();
    ctx.lineWidth = 1.5;
    ctx.globalAlpha = 0.55;
    for (let k = first; k < last; k++) {
      const t = E[vis[k]][C.THREAD];
      if (t < 0) continue;
      const r = boxRect(k);
      const prev = lastPos.get(t);
      if (prev) {
        ctx.strokeStyle = THREAD_COLORS[t % THREAD_COLORS.length];
        ctx.beginPath(); ctx.moveTo(prev.x, prev.y); ctx.lineTo(r.x + r.w - 2, r.y); ctx.stroke();
      }
      lastPos.set(t, { x: r.x + r.w - 2, y: r.y + r.h });
    }
    ctx.globalAlpha = 1;
  }

  // ---------------------------------------------------------------------------
  // 操作（ホイール・ドラッグ・クリック・キーボード）
  // ---------------------------------------------------------------------------
  function setZoom(z, anchorY) {
    const old = state.zoom;
    z = Math.max(0.2, Math.min(4, z));
    if (Math.abs(z - old) < 1e-4) return;
    const ay = anchorY == null ? viewH() / 2 : anchorY;
    const cy = contentY + ay;
    const k = Math.max(0, rowAt(cy));
    const frac = vis.length ? (cy - ys[k]) / (ys[k + 1] - ys[k] || 1) : 0;
    state.zoom = z;
    computeLayout();
    if (vis.length) setContentY(ys[k] + frac * (ys[k + 1] - ys[k]) - ay);
    $('zoom-label').textContent = Math.round(z * 100) + '%';
    requestDraw();
  }

  function initInteraction() {
    const sc = scroller();
    sc.addEventListener('scroll', onScroll);
    sc.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      if (ev.shiftKey) {
        const d = ev.deltaY || ev.deltaX;
        setContentY(contentY + d);
        return;
      }
      const rect = sc.getBoundingClientRect();
      setZoom(state.zoom * Math.exp(-ev.deltaY * 0.0015), ev.clientY - rect.top - HEADER_H);
    }, { passive: false });

    let drag = null;
    sc.addEventListener('mousedown', (ev) => {
      if (ev.button !== 0) return;
      const rect = sc.getBoundingClientRect();
      if (ev.clientX - rect.left > sc.clientWidth || ev.clientY - rect.top > sc.clientHeight) return; // スクロールバー
      drag = { x: ev.clientX, y: ev.clientY, left: sc.scrollLeft, cy: contentY, moved: false };
    });
    window.addEventListener('mousemove', (ev) => {
      if (!drag) return;
      const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      drag.moved = true;
      sc.scrollLeft = drag.left - dx;
      setContentY(drag.cy - dy);
    });
    window.addEventListener('mouseup', (ev) => {
      if (!drag) return;
      const wasDrag = drag.moved;
      drag = null;
      if (!wasDrag) handleClick(ev);
    });

    window.addEventListener('keydown', (ev) => {
      if ($('view-seq').hidden) return;
      const tag = (ev.target && ev.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        const cur = state.selected >= 0 ? visPos[state.selected] : -1;
        const next = Math.max(0, Math.min(vis.length - 1, cur + (ev.key === 'ArrowDown' ? 1 : -1)));
        if (vis.length) select(vis[next], true);
      } else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') {
        if (state.selected >= 0 && childCount[state.selected] > 0) {
          if (ev.key === 'ArrowLeft') state.collapsed.add(state.selected); else state.collapsed.delete(state.selected);
          refresh();
        }
      } else if (ev.key === 'PageDown' || ev.key === 'PageUp') {
        ev.preventDefault();
        setContentY(contentY + (ev.key === 'PageDown' ? 1 : -1) * viewH() * 0.9);
      }
    });

    $('zoom-in').addEventListener('click', () => setZoom(state.zoom * 1.25));
    $('zoom-out').addEventListener('click', () => setZoom(state.zoom / 1.25));
    $('zoom-reset').addEventListener('click', () => setZoom(1));
    $('time-gap').addEventListener('change', (ev) => { state.timeGap = ev.target.checked; computeLayout(); setContentY(contentY); });
    $('lane-width').addEventListener('input', (ev) => { state.laneW = +ev.target.value; textCache.clear(); computeLayout(); requestDraw(); });
    window.addEventListener('resize', () => { setContentY(contentY); requestDraw(); });
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onTheme = () => { readColors(); requestDraw(); };
    if (mq.addEventListener) mq.addEventListener('change', onTheme);
  }

  function handleClick(ev) {
    const sc = scroller();
    const rect = sc.getBoundingClientRect();
    const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
    if (x < 0 || y < HEADER_H || x > sc.clientWidth || y > sc.clientHeight) return;
    const k = rowAt(contentY + y - HEADER_H);
    if (k < 0 || k >= vis.length) return;
    const r = boxRect(k);
    if (x < r.x || x > r.x + r.w || y < r.y || y > r.y + r.h) return;
    const i = vis[k];
    if (childCount[i] > 0 && x < r.x + 16) {
      if (state.collapsed.has(i)) state.collapsed.delete(i); else state.collapsed.add(i);
      state.selected = i;
      refresh();
      renderDetail(i);
      return;
    }
    select(i, false);
  }

  function select(i, scrollInto) {
    state.selected = i;
    if (scrollInto) {
      const k = visPos[i];
      if (k >= 0 && (ys[k] < contentY || ys[k] + rowH > contentY + viewH())) {
        setContentY(ys[k] - viewH() / 3);
      }
      const col = laneCol[E[i][C.LANE]];
      const sc = scroller();
      const bx = TIME_W + col * state.laneW;
      if (bx < sc.scrollLeft + TIME_W || bx + state.laneW > sc.scrollLeft + sc.clientWidth) {
        sc.scrollLeft = Math.max(0, bx - TIME_W - 40);
      }
    }
    renderDetail(i);
    updateSearchCount();
    requestDraw();
  }

  function revealEvent(i) {
    // フィルタ・折り畳みで隠れている場合は表示状態にしてから選択する
    let p = parentIdx[i];
    let changed = false;
    while (p >= 0) { if (state.collapsed.delete(p)) changed = true; p = parentIdx[p]; }
    if (changed || visPos[i] < 0) {
      if (visPos[i] < 0 && !changed) resetFilters(); else refresh();
    }
    if (visPos[i] >= 0) select(i, true);
  }

  // ---------------------------------------------------------------------------
  // 詳細ペイン
  // ---------------------------------------------------------------------------
  function fullText(ev) {
    const ts = '[' + fmtTs(ev[C.TS], true) + '] ';
    if (ev[C.RAW]) return ts + ev[C.RAW];
    let s = ts;
    if (ev[C.TICK] != null) s += '[' + ev[C.TICK] + '] ';
    s += P.levels[ev[C.LEVEL]] + ' ';
    if (ev[C.MODULE] >= 0) s += MODS[ev[C.MODULE]] + ' ';
    s += ev[C.MSG];
    if (ev[C.FILE] >= 0) s += ' (' + FILES[ev[C.FILE]] + ':' + ev[C.LINE] + ' ' + (ev[C.FUNC] >= 0 ? FUNCS[ev[C.FUNC]] : '') + ')';
    return s;
  }
  function nodeInfo(n) {
    const row = G.info[n];
    return { fid: G.nodes[n], name: row[0], file: row[1] >= 0 ? G.files[row[1]] : null, start: row[2], end: row[3], module: row[4] >= 0 ? G.modules[row[4]] : '', isStatic: !!row[5] };
  }
  function candList(edgeIdxs, pick) {
    if (!edgeIdxs.length) return '<p class="muted">なし</p>';
    const items = edgeIdxs.slice(0, 200).map((ei) => {
      const e = G.edges[ei];
      const n = pick(e);
      const kind = P.arrows[e[2]];
      const via = e[4] >= 0 ? G.vias[e[4]] : '';
      return '<li><span class="badge ' + kind + '" title="' + esc(via) + '">' + ({ SYNC: '同期', ASYNC: '非同期', CALLBACK: 'CB' }[kind] || kind) + '</span>' +
        '<span class="fid" title="' + esc(G.nodes[n] + (via ? '  via ' + via : '') + '  (L' + e[3] + ')') + '">' + esc(G.nodes[n]) + '</span>' +
        (G.logged[n] ? '<button class="linkish" data-log="' + n + '" title="この関数の最初のログへ">ログ</button>' : '') +
        (G.info[n][1] >= 0 ? '<button class="linkish" data-src="' + n + '" data-line="' + e[3] + '" data-side="' + (pick === callerOf ? 'caller' : 'callee') + '">ソース</button>' : '') +
        '<button class="linkish" data-cg="' + n + '">CG</button></li>';
    });
    return '<ul class="cand-list">' + items.join('') + '</ul>' + (edgeIdxs.length > 200 ? '<p class="muted">ほか ' + (edgeIdxs.length - 200) + ' 件</p>' : '');
  }
  const callerOf = (e) => e[0];
  const calleeOf = (e) => e[1];

  function renderDetail(i) {
    const ev = E[i];
    const fid = ev[C.FID];
    const info = fid >= 0 ? nodeInfo(fid) : null;
    const p = parentIdx[i];
    const arrowName = { 1: '同期呼び出し', 2: '非同期通知', 3: 'コールバック' }[ev[C.ARROW]] || '';
    let h = '<p class="detail-title">' + esc(boxLabel(ev)) + '</p><dl class="kv">';
    const row = (k, v) => { h += '<dt>' + k + '</dt><dd>' + v + '</dd>'; };
    row('時刻', esc(fmtTs(ev[C.TS], true)));
    row('ログ行', '#' + ev[C.LINE_NO] + '（seq ' + ev[C.SEQ] + '）');
    row('レベル', esc(P.levels[ev[C.LEVEL]]));
    row('モジュール', esc(LANES[ev[C.LANE]]) + (ev[C.MODULE] >= 0 && MODS[ev[C.MODULE]] !== LANES[ev[C.LANE]] ? ' <span class="muted">(' + esc(MODS[ev[C.MODULE]]) + ')</span>' : ''));
    if (META.has_thread) row('thread_id', esc(ev[C.THREAD] >= 0 ? THREADS[ev[C.THREAD]] : '-'));
    row('関数', esc(ev[C.FUNC] >= 0 ? FUNCS[ev[C.FUNC]] : '-'));
    const srcPath = sourcePathFor(ev);
    if (ev[C.FILE] >= 0 || ev[C.LINE] >= 0) {
      const label = (ev[C.FILE] >= 0 ? FILES[ev[C.FILE]] : basename(srcPath || '?')) + ':' + ev[C.LINE];
      row('ファイル', srcPath ? '<button class="linkish" id="open-src">' + esc(label) + '</button>' : esc(label) + ' <span class="muted">(ソース未埋込み)</span>');
    }
    row('関数ID', info ? esc(info.fid) : '<span class="muted">ソース上の関数を特定できませんでした</span>');
    if (info) row('定義', esc(info.file) + ':' + info.start + '-' + info.end);
    if (p >= 0) {
      row('呼び出し元', '<button class="linkish" data-goto="' + p + '">' + esc(boxLabel(E[p])) + ' @' + esc(fmtTs(E[p][C.TS])) + '</button> <span class="badge ' + P.arrows[ev[C.ARROW]] + '">' + arrowName + '</span>' + (ev[C.HOPS] > 1 ? ' <span class="muted">(' + ev[C.HOPS] + 'ホップ)</span>' : ''));
    }
    row('深度', String(ev[C.DEPTH]) + (childCount[i] ? '（子ログ ' + childCount[i] + ' 件）' : ''));
    h += '</dl><h4>全文</h4><pre class="fulltext">' + esc(fullText(ev)) + '</pre>';
    if (info) {
      h += '<h4>呼び出し元候補（CallGraph）</h4>' + candList(G.inc[fid], callerOf);
      h += '<h4>呼び出し先候補（CallGraph）</h4>' + candList(G.out[fid], calleeOf);
      h += '<button id="detail-cg">CallGraph タブで表示</button>';
    }
    const body = $('detail-body');
    body.className = '';
    body.innerHTML = h;
    const open = $('open-src');
    if (open) open.addEventListener('click', () => openSource(srcPath, ev[C.LINE], info));
    const cg = $('detail-cg');
    if (cg) cg.addEventListener('click', () => { switchTab('graph'); graphSelect(fid); });
  }

  function onDetailClick(ev) {
    const t = ev.target.closest('button');
    if (!t) return;
    if (t.dataset.goto) revealEvent(+t.dataset.goto);
    else if (t.dataset.log) { const i = G.firstLog[+t.dataset.log]; if (i >= 0) revealEvent(i); }
    else if (t.dataset.src) {
      const n = +t.dataset.src; const info = nodeInfo(n);
      openSource(info.file, t.dataset.side === 'caller' ? +t.dataset.line : info.start, info);
    } else if (t.dataset.cg) { switchTab('graph'); graphSelect(+t.dataset.cg); }
  }

  let sourceByBase = null;
  function sourcePathFor(ev) {
    if (ev[C.FID] >= 0) {
      const info = nodeInfo(ev[C.FID]);
      if (info.file && P.sources[info.file] != null) return info.file;
    }
    if (ev[C.FILE] < 0) return null;
    if (!sourceByBase) {
      sourceByBase = new Map();
      Object.keys(P.sources).forEach((p) => { const b = basename(p).toLowerCase(); if (!sourceByBase.has(b)) sourceByBase.set(b, p); });
    }
    return sourceByBase.get(FILES[ev[C.FILE]].toLowerCase()) || null;
  }

  // ---------------------------------------------------------------------------
  // ソースビューア
  // ---------------------------------------------------------------------------
  function openSource(path, line, info) {
    const viewer = $('source-viewer');
    viewer.hidden = false;
    $('source-title').textContent = path + (line > 0 ? ':' + line : '');
    $('source-title').title = path;
    const body = $('source-body');
    const text = path != null ? P.sources[path] : null;
    if (text == null) {
      body.innerHTML = '<div class="src-missing">このファイルは HTML に埋め込まれていません。<br>--embed-sources all を指定すると全ソースを埋め込めます。</div>';
      return;
    }
    const lines = text.split('\n');
    const rs = info && info.file === path ? info.start : -1;
    const re = info && info.file === path ? info.end : -1;
    const parts = new Array(lines.length);
    for (let n = 0; n < lines.length; n++) {
      const no = n + 1;
      const cls = no === line ? 'src-line hl' : (no >= rs && no <= re ? 'src-line range' : 'src-line');
      parts[n] = '<div class="' + cls + '"' + (no === line ? ' id="src-target"' : '') + '><span class="no">' + no + '</span><span>' + esc(lines[n].replace(/\r$/, '')) + '</span></div>';
    }
    body.innerHTML = parts.join('');
    const target = $('src-target');
    if (target) body.scrollTop = target.offsetTop - body.clientHeight / 2;
    else body.scrollTop = 0;
  }

  // ---------------------------------------------------------------------------
  // CallGraph タブ
  // ---------------------------------------------------------------------------
  let vizPromise = null;
  let graphCurrent = -1;
  const gview = { x: 20, y: 20, k: 1 };
  function getViz() {
    if (!vizPromise) {
      vizPromise = (typeof Viz !== 'undefined' && Viz.instance) ? Viz.instance() : Promise.reject(new Error('viz.js が同梱されていません'));
    }
    return vizPromise;
  }
  function dq(s) { return '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"'; }

  function collectSubgraph(center, dir, hops, maxNodes) {
    const nodes = new Map([[center, 0]]);
    const edges = new Set();
    let frontier = [center];
    for (let h = 0; h < hops && frontier.length; h++) {
      const next = [];
      for (const n of frontier) {
        const lists = [];
        if (dir !== 'callers') lists.push([G.out[n], 1]);
        if (dir !== 'callees') lists.push([G.inc[n], 0]);
        for (const [list, forward] of lists) {
          for (const ei of list) {
            const e = G.edges[ei];
            const m = forward ? e[1] : e[0];
            if (!nodes.has(m)) {
              if (nodes.size >= maxNodes) continue;
              nodes.set(m, h + 1); next.push(m);
            }
            edges.add(ei);
          }
        }
      }
      frontier = next;
    }
    // ノード間の残りの辺も含める
    for (const n of nodes.keys()) for (const ei of G.out[n]) if (nodes.has(G.edges[ei][1])) edges.add(ei);
    return { nodes, edges };
  }

  function buildDot(center, sub, markLogged) {
    const clusters = new Map();
    const loose = [];
    for (const n of sub.nodes.keys()) {
      const info = nodeInfo(n);
      if (info.file == null) loose.push(n);
      else { if (!clusters.has(info.module)) clusters.set(info.module, []); clusters.get(info.module).push(n); }
    }
    const L = ['digraph callgraph {', '  rankdir=LR;', '  graph [fontname="Helvetica", fontsize=10, nodesep=0.25, ranksep=0.6, bgcolor="transparent"];',
      '  node [shape=box, style="rounded,filled", fillcolor="#f8fafc", color="#94a3b8", fontname="Helvetica", fontsize=10];',
      '  edge [fontname="Helvetica", fontsize=8, arrowsize=0.7];'];
    let ci = 0;
    for (const [mod, members] of clusters) {
      L.push('  subgraph cluster_' + (ci++) + ' {');
      L.push('    label=' + dq(mod) + '; style="rounded,dashed"; color="#94a3b8"; fontcolor="#64748b";');
      for (const n of members) {
        const info = nodeInfo(n);
        let attrs = 'id=' + dq('n' + n) + ', label=' + dq(info.name + '\n' + basename(info.file)) + ', tooltip=' + dq(info.fid);
        if (n === center) attrs += ', fillcolor="#fde68a", color="#b45309", penwidth=2';
        else if (markLogged && G.logged[n]) attrs += ', fillcolor="#dbeafe", color="#2563eb"';
        L.push('    ' + dq(G.nodes[n]) + ' [' + attrs + '];');
      }
      L.push('  }');
    }
    for (const n of loose) {
      if (G.nodes[n] === 'UNRESOLVED_FUNCTION_POINTER') {
        L.push('  ' + dq(G.nodes[n]) + ' [id=' + dq('n' + n) + ', label="UNRESOLVED\\nFUNCTION_POINTER", shape=octagon, fillcolor="#fee2e2", color="#dc2626"];');
      } else {
        L.push('  ' + dq(G.nodes[n]) + ' [id=' + dq('n' + n) + '];');
      }
    }
    const style = { SYNC: 'solid', ASYNC: 'dotted', CALLBACK: 'dashed' };
    const color = { SYNC: '#334155', ASYNC: '#0f766e', CALLBACK: '#b45309' };
    for (const ei of sub.edges) {
      const e = G.edges[ei];
      const kind = P.arrows[e[2]];
      const via = e[4] >= 0 ? G.vias[e[4]] : '';
      L.push('  ' + dq(G.nodes[e[0]]) + ' -> ' + dq(G.nodes[e[1]]) + ' [style=' + style[kind] + ', color="' + color[kind] + '"' + (via ? ', tooltip=' + dq(via) : '') + '];');
    }
    L.push('}');
    return L.join('\n');
  }

  function graphSelect(n) {
    if (n == null || n < 0) return;
    graphCurrent = n;
    const dir = $('graph-dir').value;
    const hops = +$('graph-hops').value;
    const maxNodes = Math.max(10, Math.min(2000, +$('graph-max').value || 150));
    const sub = collectSubgraph(n, dir, hops, maxNodes);
    const dot = buildDot(n, sub, $('graph-logged').checked);
    $('dot-text').value = dot;
    $('graph-func').value = G.nodes[n];
    renderGraphInfo(n, sub);
    const canvas = $('graph-canvas');
    canvas.innerHTML = '<div class="detail-empty">描画中…</div>';
    getViz().then((viz) => {
      const svg = viz.renderSVGElement(dot);
      canvas.innerHTML = '';
      canvas.appendChild(svg);
      svg.querySelectorAll('g.node').forEach((g) => {
        g.addEventListener('click', (ev) => {
          ev.stopPropagation();
          const id = g.id || '';
          if (id[0] === 'n') graphSelect(+id.slice(1));
        });
      });
      fitGraph(svg);
    }).catch((err) => {
      canvas.innerHTML = '<div class="detail-empty">Graphviz 描画に失敗しました: ' + esc(err.message || err) + '<br>右の DOT テキストを外部の Graphviz で表示できます。</div>';
    });
  }

  function fitGraph(svg) {
    const canvas = $('graph-canvas');
    const w = svg.width.baseVal.value || 800, h = svg.height.baseVal.value || 600;
    const k = Math.min(1.5, Math.min((canvas.clientWidth - 40) / w, (canvas.clientHeight - 40) / h));
    gview.k = k > 0 ? k : 1;
    gview.x = (canvas.clientWidth - w * gview.k) / 2;
    gview.y = (canvas.clientHeight - h * gview.k) / 2;
    applyGraphTransform();
  }
  function applyGraphTransform() {
    const svg = $('graph-canvas').querySelector('svg');
    if (svg) svg.style.transform = 'translate(' + gview.x + 'px,' + gview.y + 'px) scale(' + gview.k + ')';
  }

  function renderGraphInfo(n, sub) {
    const info = nodeInfo(n);
    let h = '<p class="detail-title">' + esc(info.name) + '</p><dl class="kv">';
    h += '<dt>関数ID</dt><dd>' + esc(info.fid) + '</dd>';
    if (info.file) {
      h += '<dt>ファイル</dt><dd>' + esc(info.file) + ':' + info.start + '-' + info.end + '</dd>';
      h += '<dt>モジュール</dt><dd>' + esc(info.module) + (info.isStatic ? ' <span class="muted">(static)</span>' : '') + '</dd>';
    }
    h += '<dt>呼び出し元</dt><dd>' + G.inc[n].length + ' 件</dd><dt>呼び出し先</dt><dd>' + G.out[n].length + ' 件</dd>';
    h += '<dt>表示</dt><dd>' + sub.nodes.size + ' ノード / ' + sub.edges.size + ' 辺</dd></dl><div class="row-buttons">';
    if (G.firstLog[n] >= 0) h += '<button id="g-to-log">この関数のログへ</button>';
    if (info.file && P.sources[info.file] != null) h += '<button id="g-to-src">ソースを表示</button>';
    h += '</div>';
    $('graph-info').innerHTML = h;
    const toLog = $('g-to-log');
    if (toLog) toLog.addEventListener('click', () => { switchTab('seq'); revealEvent(G.firstLog[n]); });
    const toSrc = $('g-to-src');
    if (toSrc) toSrc.addEventListener('click', () => { switchTab('seq'); openSource(info.file, info.start, info); });
  }

  function initGraph() {
    const unresolved = G.nodes.indexOf('UNRESOLVED_FUNCTION_POINTER');
    const unresolvedCount = unresolved >= 0 ? G.inc[unresolved].length : 0;
    $('graph-stats').textContent = '関数 ' + (G.nodes.length - (unresolved >= 0 ? 1 : 0)).toLocaleString() + ' / 辺 ' + G.edges.length.toLocaleString() +
      ' / 未解決の関数ポインタ呼び出し元 ' + unresolvedCount.toLocaleString();
    const input = $('graph-func');
    const matches = $('graph-func-matches');
    const showMatches = debounce(() => {
      const q = input.value.trim().toLowerCase();
      if (!q) { matches.innerHTML = ''; return; }
      const hits = [];
      for (let n = 0; n < G.nodes.length && hits.length < 400; n++) if (G.lower[n].includes(q)) hits.push(n);
      hits.sort((a, b) => (G.logged[b] - G.logged[a]) || (G.nodes[a].length - G.nodes[b].length));
      matches.innerHTML = hits.slice(0, 60).map((n) => '<button data-n="' + n + '" title="' + esc(G.nodes[n]) + '">' +
        (G.logged[n] ? '<span class="badge logged">ログ</span> ' : '') + esc(G.nodes[n]) + '</button>').join('') ||
        '<p class="muted">該当なし</p>';
    }, 150);
    input.addEventListener('input', showMatches);
    input.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Enter') return;
      const exact = G.nodes.indexOf(input.value.trim());
      if (exact >= 0) graphSelect(exact);
      else { const b = matches.querySelector('button[data-n]'); if (b) graphSelect(+b.dataset.n); }
    });
    matches.addEventListener('click', (ev) => { const b = ev.target.closest('button[data-n]'); if (b) graphSelect(+b.dataset.n); });
    ['graph-dir', 'graph-hops', 'graph-max', 'graph-logged'].forEach((id) => $(id).addEventListener('change', () => graphSelect(graphCurrent)));
    $('dot-copy').addEventListener('click', () => {
      const ta = $('dot-text');
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(ta.value).catch(() => { ta.select(); document.execCommand('copy'); });
      else { ta.select(); document.execCommand('copy'); }
    });
    const canvas = $('graph-canvas');
    canvas.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
      const k = Math.max(0.05, Math.min(6, gview.k * Math.exp(-ev.deltaY * 0.0015)));
      gview.x = mx - (mx - gview.x) * (k / gview.k);
      gview.y = my - (my - gview.y) * (k / gview.k);
      gview.k = k;
      applyGraphTransform();
    }, { passive: false });
    let pan = null;
    canvas.addEventListener('mousedown', (ev) => { pan = { x: ev.clientX, y: ev.clientY, gx: gview.x, gy: gview.y }; });
    window.addEventListener('mousemove', (ev) => {
      if (!pan) return;
      gview.x = pan.gx + ev.clientX - pan.x; gview.y = pan.gy + ev.clientY - pan.y; applyGraphTransform();
    });
    window.addEventListener('mouseup', () => { pan = null; });
    // 初期表示: 最も多くログに出現した関数
    let best = -1, bestCount = 0;
    const counts = new Int32Array(G.nodes.length);
    for (let i = 0; i < N; i++) { const f = E[i][C.FID]; if (f >= 0 && ++counts[f] > bestCount) { bestCount = counts[f]; best = f; } }
    graphCurrent = best;
  }

  function switchTab(name) {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
    $('view-seq').hidden = name !== 'seq';
    $('view-graph').hidden = name !== 'graph';
    if (name === 'seq') { setContentY(contentY); requestDraw(); }
    if (name === 'graph' && graphCurrent >= 0 && !$('graph-canvas').querySelector('svg')) graphSelect(graphCurrent);
  }

  // ---------------------------------------------------------------------------
  // 起動
  // ---------------------------------------------------------------------------
  async function main() {
    try {
      P = await loadPayload();
    } catch (err) {
      $('loading-text').textContent = '読み込みに失敗しました: ' + (err && err.message ? err.message : err);
      document.querySelector('#loading .spinner').hidden = true;
      return;
    }
    readColors();
    prepare();
    const m = META;
    $('lv-summary').textContent = N.toLocaleString() + ' 件 ・ ' + LANES.length + ' モジュール ・ 関数 ' +
      (G.nodes.length - 1).toLocaleString() + ' ・ 解析: ' + (m.backend || '-') + ' ・ ' + (m.log_file || '') + ' ・ 生成 ' + (m.generated_at || '');
    $('lv-summary').title = $('lv-summary').textContent;
    initFilters();
    initSearch();
    initInteraction();
    initGraph();
    $('detail-body').addEventListener('click', onDetailClick);
    $('source-close').addEventListener('click', () => { $('source-viewer').hidden = true; });
    document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => switchTab(t.dataset.tab)));
    $('loading').hidden = true;
    refresh();
    window.__lv = { state: state, select: select, switchTab: switchTab, graphSelect: graphSelect, openSource: openSource, revealEvent: revealEvent };
  }

  main();
})();

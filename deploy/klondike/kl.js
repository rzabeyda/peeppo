
// ---------- Косынка (Klondike solitaire) -- free, purely client-side, no gems ----------
(function(){
  const KL_KEY = "peeppo_klondike_v1";
  const SUITS = ["♠","♥","♦","♣"]; // spades, hearts, diamonds, clubs
  const RANKS = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"];
  const VS = "︎"; // text presentation, so iOS doesn't turn suits into emoji
  const suitOf = c => Math.floor(c / 13);
  const rankOf = c => c % 13;
  const isRed = c => { const s = suitOf(c); return s === 1 || s === 2; };
  const tgw = window.Telegram?.WebApp;

  const board = document.getElementById("kl-board");
  const area = document.getElementById("kl-area");
  const blockEl = document.getElementById("pvp-klondike-block");
  let st = null;          // game state
  let stats = {wins:0, played:0};
  let hist = [];          // undo snapshots (JSON strings)
  let busy = false;       // auto-collect animation running
  let geo = null;         // last layout geometry
  let loc = [];           // card -> {t, i, idx}
  const cardEls = [];
  const slotEls = {};
  let drag = null;
  let saveTimer = null;

  // ---------- state ----------
  function shuffled(){
    const d = []; for(let i = 0; i < 52; i++) d.push(i);
    const rnd = new Uint32Array(52);
    if(window.crypto?.getRandomValues) crypto.getRandomValues(rnd); else for(let i = 0; i < 52; i++) rnd[i] = Math.floor(Math.random() * 4294967296);
    for(let i = 51; i > 0; i--){ const j = rnd[i] % (i + 1); [d[i], d[j]] = [d[j], d[i]]; }
    return d;
  }
  function newDeal(draw){
    stopCascade();
    const d = shuffled();
    const s = {stock:[], waste:[], found:[[],[],[],[]], tab:[[],[],[],[],[],[],[]], up:new Array(52).fill(0), moves:0, score:0, elapsed:0, draw:draw || 1, won:false, counted:false};
    for(let i = 0; i < 7; i++) for(let j = i; j < 7; j++) s.tab[j].push(d.pop());
    for(let j = 0; j < 7; j++) s.up[s.tab[j][s.tab[j].length - 1]] = 1;
    s.stock = d;
    st = s; hist = [];
    document.getElementById("kl-win").classList.remove("active");
    render(); save();
  }
  function validState(s){
    try{
      const all = [].concat(s.stock, s.waste, ...s.found, ...s.tab);
      if(all.length !== 52 || new Set(all).size !== 52) return false;
      return s.tab.length === 7 && s.found.length === 4 && Array.isArray(s.up) && s.up.length === 52;
    }catch(e){ return false; }
  }
  function pile(ref){
    if(ref.t === "tab") return st.tab[ref.i];
    if(ref.t === "found") return st.found[ref.i];
    if(ref.t === "waste") return st.waste;
    return st.stock;
  }
  function snapshot(){ hist.push(JSON.stringify(st)); if(hist.length > 300) hist.shift(); }

  function canFound(c, fi){
    const f = st.found[fi];
    if(!f.length) return rankOf(c) === 0;
    const top = f[f.length - 1];
    return suitOf(top) === suitOf(c) && rankOf(c) === rankOf(top) + 1;
  }
  function canTab(c, ti){
    const col = st.tab[ti];
    if(!col.length) return rankOf(c) === 12;
    const top = col[col.length - 1];
    return st.up[top] && isRed(top) !== isRed(c) && rankOf(top) === rankOf(c) + 1;
  }
  function foundTarget(c){
    for(let fi = 0; fi < 4; fi++) if(st.found[fi].length && canFound(c, fi)) return fi;
    for(let fi = 0; fi < 4; fi++) if(canFound(c, fi)) return fi;
    return -1;
  }
  function movable(ref, idx){
    const p = pile(ref);
    if(idx < 0 || idx >= p.length) return false;
    if(ref.t === "stock") return false;
    if(ref.t === "tab") return !!st.up[p[idx]];
    return idx === p.length - 1;
  }
  // tableau targets for the run starting at (ref, idx); skips pointless king-to-empty moves
  function tabTargets(ref, idx){
    const c = pile(ref)[idx];
    const res = [];
    for(let ti = 0; ti < 7; ti++){
      if(ref.t === "tab" && ref.i === ti) continue;
      if(!canTab(c, ti)) continue;
      if(!st.tab[ti].length && ref.t === "tab" && idx === 0) continue;
      res.push(ti);
    }
    // non-empty columns first
    res.sort((a, b) => (st.tab[a].length ? 0 : 1) - (st.tab[b].length ? 0 : 1));
    return res;
  }

  // Windows-style scoring
  function addScore(p){ st.score = Math.max(0, (st.score || 0) + p); }
  function doMove(src, idx, dst){
    const sp = pile(src);
    const cards = sp.splice(idx);
    pile(dst).push(...cards);
    if(dst.t === "found") addScore(10);
    else if(src.t === "waste") addScore(5);
    else if(src.t === "found") addScore(-15);
    if(src.t === "tab" && sp.length && !st.up[sp[sp.length - 1]]){ st.up[sp[sp.length - 1]] = 1; addScore(5); }
    st.moves++;
    tgw?.HapticFeedback?.selectionChanged?.();
    afterChange();
  }
  function drawStock(){
    if(busy || st.won) return;
    if(!st.stock.length && !st.waste.length) return;
    snapshot();
    if(st.stock.length){
      const n = Math.min(st.draw, st.stock.length);
      for(let k = 0; k < n; k++){ const c = st.stock.pop(); st.up[c] = 1; st.waste.push(c); }
    }else{
      while(st.waste.length){ const c = st.waste.pop(); st.up[c] = 0; st.stock.push(c); }
      addScore(st.draw === 1 ? -100 : -20);
    }
    st.moves++;
    afterChange();
  }
  function afterChange(){
    clearHint();
    if(st.found.every(f => f.length === 13) && !st.won){
      st.won = true;
      stats.wins++;
      if(st.elapsed >= 30) addScore(Math.round(700000 / st.elapsed));
      if((st.score || 0) > (stats.best || 0)) stats.best = st.score;
      render(); save();
      setTimeout(() => winCascade(showWin), 350);
      tgw?.HapticFeedback?.notificationOccurred?.("success");
      return;
    }
    if(!st.counted && st.moves > 0){ st.counted = true; stats.played++; }
    render(); save();
  }
  function tapCard(ref, idx){
    if(ref.t === "stock"){ drawStock(); return; }
    if(!movable(ref, idx)) return;
    const p = pile(ref);
    const c = p[idx];
    if(idx === p.length - 1 && ref.t !== "found"){
      const fi = foundTarget(c);
      if(fi >= 0){ snapshot(); doMove(ref, idx, {t:"found", i:fi}); return; }
    }
    const tt = tabTargets(ref, idx);
    if(tt.length){ snapshot(); doMove(ref, idx, {t:"tab", i:tt[0]}); return; }
    shake(c);
  }
  function shake(c){
    const el = cardEls[c];
    el.classList.remove("kl-shake"); void el.offsetWidth; el.classList.add("kl-shake");
    setTimeout(() => el.classList.remove("kl-shake"), 320);
  }
  function undo(){
    if(busy || !hist.length) return;
    stopCascade();
    const wins = stats.wins;
    st = JSON.parse(hist.pop());
    stats.wins = wins;
    document.getElementById("kl-win").classList.remove("active");
    clearHint(); render(); save();
  }

  // ---------- auto-collect to foundations ----------
  function nextHomeMove(){
    const srcs = [{t:"waste"}];
    for(let i = 0; i < 7; i++) srcs.push({t:"tab", i});
    let best = null;
    for(const ref of srcs){
      const p = pile(ref);
      if(!p.length) continue;
      const c = p[p.length - 1];
      if(!st.up[c]) continue;
      const fi = foundTarget(c);
      if(fi >= 0 && (!best || rankOf(c) < best.rank)) best = {ref, idx:p.length - 1, fi, rank:rankOf(c)};
    }
    return best;
  }
  function collectHome(){
    if(busy || st.won) return;
    let m = nextHomeMove();
    if(!m){ toast("Сейчас нечего отправить в дом"); return; }
    snapshot();
    busy = true;
    const step = () => {
      m = nextHomeMove();
      if(!m || st.won){ busy = false; render(); return; }
      doMove(m.ref, m.idx, {t:"found", i:m.fi});
      setTimeout(step, 110);
    };
    step();
  }
  function allOpen(){
    return !st.stock.length && !st.waste.length && st.tab.every(col => col.every(c => st.up[c]));
  }

  // ---------- hint ----------
  let hintTimer = null;
  function clearHint(){
    clearTimeout(hintTimer);
    area.querySelectorAll(".kl-hint").forEach(e => e.classList.remove("kl-hint"));
  }
  function findHint(){
    // 1) anything to foundation
    const tops = [{t:"waste"}];
    for(let i = 0; i < 7; i++) tops.push({t:"tab", i});
    for(const ref of tops){
      const p = pile(ref);
      if(!p.length || !st.up[p[p.length - 1]]) continue;
      const fi = foundTarget(p[p.length - 1]);
      if(fi >= 0) return {src:[p[p.length - 1]], dst:{t:"found", i:fi}};
    }
    // 2) tableau runs that uncover a card (or free a column)
    for(let i = 0; i < 7; i++){
      const col = st.tab[i];
      const idx = col.findIndex(c => st.up[c]);
      if(idx < 0) continue;
      const tt = tabTargets({t:"tab", i}, idx);
      if(tt.length) return {src:col.slice(idx), dst:{t:"tab", i:tt[0]}};
    }
    // 3) waste to tableau
    if(st.waste.length){
      const tt = tabTargets({t:"waste"}, st.waste.length - 1);
      if(tt.length) return {src:[st.waste[st.waste.length - 1]], dst:{t:"tab", i:tt[0]}};
    }
    // 4) the deck
    if(st.stock.length || st.waste.length) return {stock:true};
    return null;
  }
  function showHint(){
    if(busy || st.won) return;
    clearHint();
    const h = findHint();
    if(!h){ toast("Ходов больше нет — попробуй новую раздачу"); return; }
    if(h.stock){
      if(st.stock.length) cardEls[st.stock[st.stock.length - 1]].classList.add("kl-hint");
      else slotEls.stock.classList.add("kl-hint");
    }else{
      h.src.forEach(c => cardEls[c].classList.add("kl-hint"));
      const dp = pile(h.dst);
      if(dp.length) cardEls[dp[dp.length - 1]].classList.add("kl-hint");
      else slotEls[h.dst.t + h.dst.i].classList.add("kl-hint");
    }
    hintTimer = setTimeout(clearHint, 1800);
  }

  // ---------- layout & render ----------
  function buildDom(){
    const mk = (key, cls, txt) => { const e = document.createElement("div"); e.className = "kl-slot " + cls; e.textContent = txt || ""; area.appendChild(e); slotEls[key] = e; return e; };
    mk("stock", "kl-slot-stock", "");
    for(let i = 0; i < 4; i++) mk("found" + i, "", "A");
    for(let i = 0; i < 7; i++) mk("tab" + i, "", "K");
    for(let c = 0; c < 52; c++){
      const e = document.createElement("div");
      e.className = "kl-card" + (isRed(c) ? " red" : "");
      const s = SUITS[suitOf(c)] + VS;
      e.innerHTML = `<div class="kl-face"><div class="kl-corner"><span class="kl-r">${RANKS[rankOf(c)]}</span><span class="kl-s">${s}</span></div><div class="kl-big">${s}</div></div><div class="kl-back"></div>`;
      e.dataset.c = c;
      area.appendChild(e);
      cardEls[c] = e;
    }
  }
  function layout(){
    const W = area.clientWidth;
    if(!W) return null;
    const gap = Math.max(3, Math.round(W * 0.012));
    const cw = (W - gap * 6) / 7;
    const ch = cw * 1.4;
    const y0 = ch + gap * 3;
    const avail = Math.max(ch * 4.6, window.innerHeight * 0.6) - y0;
    return {W, gap, cw, ch, y0, avail, x:i => i * (cw + gap)};
  }
  function render(){
    const g = layout();
    if(!g) return;
    geo = g;
    area.style.setProperty("--cw", g.cw + "px");
    area.style.setProperty("--ch", g.ch + "px");
    board.style.setProperty("--cw", g.cw + "px");
    board.style.setProperty("--ch", g.ch + "px");
    const pos = (e, x, y) => { e.style.transform = `translate(${x}px,${y}px)`; };
    pos(slotEls.stock, g.x(0), 0);
    slotEls.stock.textContent = st.waste.length ? "↻" : "";
    for(let i = 0; i < 4; i++) pos(slotEls["found" + i], g.x(3 + i), 0);
    for(let i = 0; i < 7; i++) pos(slotEls["tab" + i], g.x(i), g.y0);
    loc = [];
    let z = 1;
    const place = (c, x, y, ref, idx) => {
      const e = cardEls[c];
      e.classList.toggle("up", !!st.up[c]);
      e.classList.remove("kl-drag");
      e.style.zIndex = z++;
      pos(e, x, y);
      e._x = x; e._y = y;
      loc[c] = {t:ref.t, i:ref.i, idx};
    };
    st.stock.forEach((c, idx) => place(c, g.x(0), 0, {t:"stock"}, idx));
    const fanN = st.draw === 3 ? Math.min(3, st.waste.length) : 1;
    const fanStep = g.cw * 0.32;
    st.waste.forEach((c, idx) => {
      const k = idx - (st.waste.length - fanN);
      place(c, g.x(1) + (k > 0 ? k * fanStep : 0), 0, {t:"waste"}, idx);
    });
    st.found.forEach((f, i) => f.forEach((c, idx) => place(c, g.x(3 + i), 0, {t:"found", i}, idx)));
    let maxH = g.y0 + g.ch;
    st.tab.forEach((col, i) => {
      let downStep = g.ch * 0.13, upStep = g.ch * 0.27;
      const downs = col.filter(c => !st.up[c]).length;
      const ups = col.length - downs;
      const need = downs * downStep + Math.max(0, ups - 1) * upStep + g.ch;
      if(need > g.avail && ups > 1){
        upStep = Math.max(g.ch * 0.15, (g.avail - g.ch - downs * downStep) / (ups - 1));
      }
      let y = g.y0;
      col.forEach((c, idx) => {
        place(c, g.x(i), y, {t:"tab", i}, idx);
        y += st.up[c] ? upStep : downStep;
      });
      if(col.length) maxH = Math.max(maxH, y - (st.up[col[col.length - 1]] ? upStep : downStep) + g.ch);
    });
    area.style.height = Math.ceil(Math.max(maxH, g.y0 + g.ch * 3.2)) + "px";
    updateHud();
  }
  function fmtTime(s){ const m = Math.floor(s / 60); return m + ":" + String(s % 60).padStart(2, "0"); }
  function updateHud(){
    document.getElementById("kl-stat").textContent = "Счёт " + (st.score || 0) + " · " + fmtTime(st.elapsed);
    document.getElementById("kl-draw-chip").textContent = "По " + st.draw;
    document.getElementById("kl-undo-btn").disabled = !hist.length;
    document.getElementById("kl-home-btn").classList.toggle("kl-pulse", !st.won && allOpen());
  }
  function showWin(){
    document.getElementById("kl-win-text").innerHTML =
      "Счёт: " + (st.score || 0) + "<br>Ходов: " + st.moves + " · Время: " + fmtTime(st.elapsed) +
      "<br>Лучший счёт: " + (stats.best || 0) + " · Побед: " + stats.wins;
    document.getElementById("kl-win").classList.add("active");
  }

  // ---------- the classic bouncing-cards win animation ----------
  let cascade = null;
  function stopCascade(){
    if(!cascade) return;
    cancelAnimationFrame(cascade.raf);
    cascade.cv.remove();
    cardEls.forEach(e => e.style.visibility = "");
    const cb = cascade.done; cascade = null;
    return cb;
  }
  function drawCardCv(ctx, c, x, y, w, h){
    const r = w * 0.12;
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
    ctx.fillStyle = "#fbfbf7"; ctx.fill();
    ctx.strokeStyle = "#8a8a84"; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = isRed(c) ? "#d1123f" : "#151515";
    ctx.textBaseline = "top"; ctx.textAlign = "left";
    ctx.font = `bold ${w * 0.36}px Georgia,serif`;
    const rk = RANKS[rankOf(c)];
    ctx.fillText(rk, x + w * 0.07, y + w * 0.04);
    const rw = ctx.measureText(rk).width;
    const s = SUITS[suitOf(c)] + VS;
    ctx.font = `${w * 0.30}px Georgia,serif`;
    ctx.fillText(s, x + w * 0.07 + rw + 1, y + w * 0.08);
    ctx.font = `${w * 0.66}px Georgia,serif`;
    ctx.textAlign = "center"; ctx.textBaseline = "bottom";
    ctx.fillText(s, x + w / 2, y + h - h * 0.08);
  }
  function winCascade(done){
    stopCascade();
    const g = geo;
    if(!g){ done(); return; }
    const W = board.clientWidth, H = board.clientHeight;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cv = document.createElement("canvas");
    cv.className = "kl-cascade";
    cv.width = W * dpr; cv.height = H * dpr;
    cv.style.width = W + "px"; cv.style.height = H + "px";
    board.appendChild(cv);
    const ctx = cv.getContext("2d");
    ctx.scale(dpr, dpr);
    const padX = area.offsetLeft, padY = area.offsetTop;
    const order = [];
    for(let rk = 12; rk >= 0; rk--) for(let fi = 0; fi < 4; fi++) if(st.found[fi][rk] !== undefined) order.push({c:st.found[fi][rk], fi});
    cascade = {cv, done, raf:0};
    let i = -1, cur = null;
    const launch = () => {
      i++;
      if(i >= order.length){ const cb = stopCascade(); cb && cb(); return false; }
      const o = order[i];
      cardEls[o.c].style.visibility = "hidden";
      const dir = Math.random() < 0.5 ? -1 : 1;
      cur = {c:o.c, x:padX + g.x(3 + o.fi), y:padY, vx:dir * (2 + Math.random() * 3.5), vy:-(Math.random() * 6)};
      return true;
    };
    launch();
    const frame = () => {
      if(!cascade) return;
      for(let k = 0; k < 2 && cur; k++){
        cur.vy += 0.6; cur.x += cur.vx; cur.y += cur.vy;
        if(cur.y > H - g.ch){ cur.y = H - g.ch; cur.vy = -cur.vy * 0.78; }
        drawCardCv(ctx, cur.c, cur.x, cur.y, g.cw, g.ch);
        if(cur.x < -g.cw || cur.x > W){ if(!launch()) return; }
      }
      cascade.raf = requestAnimationFrame(frame);
    };
    cv.addEventListener("pointerdown", (e) => { e.stopPropagation(); const cb = stopCascade(); cb && cb(); });
    cascade.raf = requestAnimationFrame(frame);
  }

  // ---------- pointer input (tap + drag) ----------
  function cardAt(e){
    const el = e.target.closest(".kl-card");
    if(el) return {c:+el.dataset.c};
    if(e.target.closest(".kl-slot-stock")) return {stockSlot:true};
    return null;
  }
  board.addEventListener("pointerdown", (e) => {
    if(!st || busy || st.won || drag) return;
    const hit = cardAt(e);
    if(!hit) return;
    e.preventDefault();
    if(hit.stockSlot){ drag = {stockOnly:true, id:e.pointerId, sx:e.clientX, sy:e.clientY, moved:false}; return; }
    const L = loc[hit.c];
    if(!L) return;
    const ref = {t:L.t, i:L.i};
    if(ref.t === "stock"){ drag = {stockOnly:true, id:e.pointerId, sx:e.clientX, sy:e.clientY, moved:false}; return; }
    let idx = L.idx;
    if(ref.t !== "tab") idx = pile(ref).length - 1; // waste/foundation: always the top card
    if(!movable(ref, idx)) return;
    const cards = pile(ref).slice(idx);
    drag = {ref, idx, cards, id:e.pointerId, sx:e.clientX, sy:e.clientY, moved:false};
    try{ board.setPointerCapture(e.pointerId); }catch(_){}
  });
  board.addEventListener("pointermove", (e) => {
    if(!drag || e.pointerId !== drag.id || drag.stockOnly) return;
    e.preventDefault();
    const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
    if(!drag.moved){
      if(Math.hypot(dx, dy) < 7) return;
      drag.moved = true;
      clearHint();
      drag.cards.forEach((c, k) => { const el = cardEls[c]; el.classList.add("kl-drag"); el.style.zIndex = 1000 + k; });
    }
    drag.dx = dx; drag.dy = dy;
    drag.cards.forEach(c => { const el = cardEls[c]; el.style.transform = `translate(${el._x + dx}px,${el._y + dy}px)`; });
  });
  function endDrag(e, cancelled){
    if(!drag || e.pointerId !== drag.id) return;
    const d = drag; drag = null;
    if(d.stockOnly){ if(!cancelled) drawStock(); return; }
    if(cancelled){ render(); return; }
    if(!d.moved){ tapCard(d.ref, d.idx); return; }
    const c0 = d.cards[0];
    const x = cardEls[c0]._x + d.dx, y = cardEls[c0]._y + d.dy;
    const g = geo;
    const overlap = (tx, ty) => Math.max(0, Math.min(x + g.cw, tx + g.cw) - Math.max(x, tx)) * Math.max(0, Math.min(y + g.ch, ty + g.ch) - Math.max(y, ty));
    let best = null, bestA = 0;
    for(let ti = 0; ti < 7; ti++){
      if(d.ref.t === "tab" && d.ref.i === ti) continue;
      if(!canTab(c0, ti)) continue;
      const col = st.tab[ti];
      const ty = col.length ? cardEls[col[col.length - 1]]._y : g.y0;
      const a = overlap(g.x(ti), ty);
      if(a > bestA){ bestA = a; best = {t:"tab", i:ti}; }
    }
    if(d.cards.length === 1 && d.ref.t !== "found"){
      for(let fi = 0; fi < 4; fi++){
        if(!canFound(c0, fi)) continue;
        const a = overlap(g.x(3 + fi), 0);
        if(a > bestA){ bestA = a; best = {t:"found", i:fi}; }
      }
    }
    if(best){ snapshot(); doMove(d.ref, d.idx, best); }
    else render();
  }
  board.addEventListener("pointerup", (e) => endDrag(e, false));
  board.addEventListener("pointercancel", (e) => endDrag(e, true));
  board.addEventListener("contextmenu", (e) => e.preventDefault());

  // ---------- persistence ----------
  function save(){
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const data = JSON.stringify({s:st, stats});
      try{ localStorage.setItem(KL_KEY, data); }catch(e){}
      try{ tgw?.CloudStorage?.setItem?.(KL_KEY, data, () => {}); }catch(e){}
    }, 600);
  }
  function applyLoaded(raw){
    try{
      const o = JSON.parse(raw);
      if(o && o.s && validState(o.s)){ st = o.s; if(o.stats) stats = o.stats; hist = []; render(); return true; }
    }catch(e){}
    return false;
  }

  // ---------- controls ----------
  function confirmNew(msg, cb){
    if(!st || st.won || st.moves === 0){ cb(); return; }
    if(tgw?.showConfirm){ try{ tgw.showConfirm(msg, ok => { if(ok) cb(); }); return; }catch(e){} }
    if(confirm(msg)) cb();
  }
  document.getElementById("kl-new-btn").addEventListener("click", () => { if(!busy) confirmNew("Начать новую раздачу?", () => newDeal(st.draw)); });
  document.getElementById("kl-win-new").addEventListener("click", () => newDeal(st.draw));
  document.getElementById("kl-undo-btn").addEventListener("click", undo);
  document.getElementById("kl-hint-btn").addEventListener("click", showHint);
  document.getElementById("kl-home-btn").addEventListener("click", collectHome);
  document.getElementById("kl-draw-chip").addEventListener("click", () => {
    if(busy) return;
    const next = st.draw === 1 ? 3 : 1;
    confirmNew("Открывать по " + next + " и начать новую раздачу?", () => newDeal(next));
  });
  const openRules = () => document.getElementById("kl-rules-overlay").classList.add("active");
  document.getElementById("kl-info-btn").addEventListener("click", openRules);
  document.getElementById("kl-rules-close").addEventListener("click", () => document.getElementById("kl-rules-overlay").classList.remove("active"));
  document.getElementById("kl-rules-overlay").addEventListener("click", (e) => { if(e.target.id === "kl-rules-overlay") e.currentTarget.classList.remove("active"); });

  // ---------- visibility / timer ----------
  function isVisible(){ return blockEl.style.display !== "none" && document.getElementById("pvp-screen").classList.contains("active"); }
  let swipesOff = false;
  window.klOnVisibility = function(){
    const v = isVisible();
    if(v !== swipesOff){
      swipesOff = v;
      try{ v ? tgw?.disableVerticalSwipes?.() : tgw?.enableVerticalSwipes?.(); }catch(e){}
    }
    if(v && st) render();
  };
  setInterval(() => {
    if(!st || st.won || st.moves === 0 || document.hidden || !isVisible()) return;
    st.elapsed++;
    updateHud();
    if(st.elapsed % 10 === 0) save();
  }, 1000);
  window.addEventListener("resize", () => { if(st && isVisible()) render(); });

  // ---------- init ----------
  buildDom();
  let local = null;
  try{ local = localStorage.getItem(KL_KEY); }catch(e){}
  if(!(local && applyLoaded(local))){
    st = null;
    const fallback = () => { if(!st) newDeal(1); };
    if(tgw?.CloudStorage?.getItem){
      try{
        tgw.CloudStorage.getItem(KL_KEY, (err, val) => { if(!st && !(val && applyLoaded(val))) fallback(); });
        setTimeout(fallback, 2500);
      }catch(e){ fallback(); }
    }else fallback();
  }
})();

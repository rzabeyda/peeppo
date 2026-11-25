// ---------- Косынка (Klondike) -- webapp/games/klondike.js ----------
// Two modes: "free" (fully client-side, undo, Windows scoring) and "gems" (Vegas rules, the server
// deals and validates every move, faces of hidden cards are only revealed by the server).
// Cards are physical ids 0..51; st.face[id] is the card value (suit*13+rank) once known.
(function(){
  const block = document.getElementById("pvp-klondike-block");
  if(!block) return;
  const FREE_KEY = "peeppo_klondike_v2";
  const MODE_KEY = "peeppo_klondike_mode";
  const SUITS = ["♠","♥","♦","♣"]; // same symbols as the poker cards
  const RANKS = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"];
  const GEM = '<img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">';
  const tgw = window.Telegram?.WebApp;
  const g_api = (p, b) => api(p, b);               // index.html globals
  const g_toast = (m) => { try{ toast(m); }catch(e){} };
  const isAdmin = () => { try{ return !!isAdminUser; }catch(e){ return false; } };
  const gemsNow = () => { try{ return gemsBalance; }catch(e){ return 0; } };
  const setGems = (n) => { try{ if(!isAdmin() && typeof n === "number") setGemsDisplay(n); }catch(e){} };

  block.innerHTML = `
    <div class="market-subrow game-actions-row kl-actions">
      <button type="button" id="kl-new-btn" class="market-filter-btn">Новая</button>
      <button type="button" id="kl-undo-btn" class="market-filter-btn">↶ Отмена</button>
      <button type="button" id="kl-hint-btn" class="market-filter-btn">Подсказка</button>
      <button type="button" id="kl-home-btn" class="market-filter-btn">В дом</button>
    </div>
    <div class="kl-gem-panel" id="kl-gem-panel" style="display:none">
      <div id="kl-bet-box">
        <div class="rb-bet-row">
          <span class="rb-bet-label">Ставка:</span>
          <span class="rb-bet-value" id="kl-bet-value">25</span>
          <span class="rb-bet-unit">${GEM}</span>
          <button class="rb-bet-edit" id="kl-bet-edit">Изменить</button>
        </div>
        <div class="rb-presets" id="kl-presets" style="margin-top:8px">
          <button data-bet="25" class="active">25</button><button data-bet="50">50</button><button data-bet="100">100</button><button data-bet="250">250</button><button data-bet="500">500</button>
        </div>
        <button class="kl-deal-btn" id="kl-deal-btn" style="margin-top:8px">Раздать за <span id="kl-deal-bet">25</span> ${GEM}</button>
        <div class="kl-gem-info" id="kl-gem-info" style="margin-top:6px"></div>
      </div>
      <div class="kl-gem-live" id="kl-gem-live" style="display:none">
        <span class="kl-live-txt" id="kl-live-txt"></span>
        <button class="kl-cash-btn" id="kl-cash-btn">Забрать</button>
      </div>
    </div>
    <div class="kl-wrap">
      <div class="kl-head">
        <div class="rb-title">Косынка</div>
        <span class="kl-stat" id="kl-stat"></span>
        <button type="button" class="kl-draw-chip kl-mode-chip" id="kl-mode-chip"></button>
        <button type="button" class="kl-draw-chip" id="kl-draw-chip">По 1</button>
        <button type="button" id="kl-info-btn" class="game-info-btn">?</button>
      </div>
      <div class="kl-board" id="kl-board"><div class="kl-area" id="kl-area"></div></div>
    </div>`;
  const winEl = document.createElement("div");
  winEl.className = "kl-win"; winEl.id = "kl-win";
  winEl.innerHTML = `<div class="kl-win-card">
      <div class="kl-win-title" id="kl-win-title">🎉 Победа!</div>
      <div class="kl-win-text" id="kl-win-text"></div>
      <button type="button" class="kl-win-btn" id="kl-win-new">Новая раздача</button>
    </div>`;
  (document.getElementById("app") || document.body).appendChild(winEl);
  const rules = document.createElement("div");
  rules.id = "kl-rules-overlay";
  rules.innerHTML = `<div id="kl-rules-card">
      <button class="btn-close-inline" id="kl-rules-close">✕</button>
      <div id="kl-rules-title">Правила Косынки</div>
      <div id="kl-rules-text">
        <p>Цель — собрать все карты в четыре «дома» (справа вверху): по мастям, от туза до короля.</p>
        <p>В столбцах карты кладутся по убыванию с чередованием цвета: на чёрную 8 — красную 7 и так далее. В пустой столбец можно положить только короля.</p>
        <p>Нажми на колоду слева вверху, чтобы открыть карту. Когда колода закончится — нажми ещё раз, и она перевернётся заново.</p>
        <p><b>Управление:</b> нажми на карту — она сама встанет на подходящее место. Или перетащи её пальцем. «Подсказка» показывает возможный ход, «В дом» отправляет в дом все карты, какие можно.</p>
        <p><b>Бесплатно:</b> можно открывать по 1 или по 3 карты, колоду крутить сколько угодно, есть «Отмена». Очки как в Windows.</p>
        <p><b>На гемы (Вегас):</b> ставишь гемы и получаешь раздачу. Открываются по 3 карты, колоду можно пройти только 3 раза, отмены нет. Каждые 13 карт в доме возвращают одну ставку: собрал весь пасьянс — получаешь ставку ×4. Нажми «Забрать», когда ходов больше нет, — получишь за карты, которые уже в доме.</p>
      </div>
    </div>`;
  (document.getElementById("app") || document.body).appendChild(rules);

  const $ = (id) => document.getElementById(id);
  const suitOf = v => Math.floor(v / 13);
  const rankOf = v => v % 13;
  const isRedV = v => { const s = suitOf(v); return s === 1 || s === 2; };
  const board = $("kl-board"), area = $("kl-area");

  let mode = "free";
  let freeSt = null, gemSt = null;   // gemSt: active server round (or finished one still on screen)
  let st = null;                      // the state on screen
  let stats = {wins:0, played:0, best:0};
  let hist = [];
  let busy = false;
  let geo = null, loc = [];
  const cardEls = [], slotEls = {};
  let drag = null, saveTimer = null;
  let gemCfg = {min_bet:25, cards_per_bet:13, max_passes:3};
  let gemBet = 25;
  let gemLoaded = false;
  let sendQ = Promise.resolve();

  const val = (c) => st.face[c];
  const known = (c) => st.face[c] !== undefined && st.face[c] !== null;

  // ---------- state ----------
  function shuffled(){
    const d = []; for(let i = 0; i < 52; i++) d.push(i);
    const rnd = new Uint32Array(52);
    if(window.crypto?.getRandomValues) crypto.getRandomValues(rnd); else for(let i = 0; i < 52; i++) rnd[i] = Math.floor(Math.random() * 4294967296);
    for(let i = 51; i > 0; i--){ const j = rnd[i] % (i + 1); [d[i], d[j]] = [d[j], d[i]]; }
    return d;
  }
  function newFreeDeal(draw){
    stopCascade();
    const face = shuffled();
    const s = {mode:"free", face, stock:[], waste:[], found:[[],[],[],[]], tab:[[],[],[],[],[],[],[]], up:new Array(52).fill(0),
               moves:0, score:0, elapsed:0, draw:draw || 1, won:false, counted:false, passes:1, maxPasses:0};
    let k = 0;
    for(let i = 0; i < 7; i++) for(let j = i; j < 7; j++) s.tab[j].push(k++);
    for(let j = 0; j < 7; j++) s.up[s.tab[j][s.tab[j].length - 1]] = 1;
    while(k < 52) s.stock.push(k++);
    freeSt = s; hist = [];
    if(mode === "free"){ st = s; $("kl-win").classList.remove("active"); refreshFaces(); render(); }
    save();
  }
  function fromServer(r){
    const face = new Array(52).fill(null);
    Object.entries(r.faces || {}).forEach(([k, v]) => face[+k] = v);
    return {mode:"gems", face, stock:r.stock, waste:r.waste, found:r.found, tab:r.tab, up:r.up, moves:r.moves || 0,
            score:0, elapsed:0, draw:r.draw || 3, won:false, counted:true, passes:r.passes || 1, maxPasses:r.max_passes || 3,
            round_id:r.round_id, bet:r.bet, home:r.home || 0, payoutNow:r.payout_now || 0, finished:false};
  }
  function validFree(s){
    try{
      const all = [].concat(s.stock, s.waste, ...s.found, ...s.tab);
      return all.length === 52 && new Set(all).size === 52 && s.tab.length === 7 && s.found.length === 4 &&
             Array.isArray(s.up) && s.up.length === 52 && Array.isArray(s.face) && s.face.length === 52;
    }catch(e){ return false; }
  }
  function pile(ref){
    if(ref.t === "tab") return st.tab[ref.i];
    if(ref.t === "found") return st.found[ref.i];
    if(ref.t === "waste") return st.waste;
    return st.stock;
  }
  const isGems = () => st && st.mode === "gems";
  const playable = () => st && !st.won && !st.finished && !busy;
  function snapshot(){ if(isGems()) return; hist.push(JSON.stringify(st)); if(hist.length > 300) hist.shift(); }

  function canFound(c, fi){
    if(!known(c)) return false;
    const v = val(c), f = st.found[fi];
    if(!f.length) return rankOf(v) === 0;
    const t = val(f[f.length - 1]);
    return suitOf(t) === suitOf(v) && rankOf(v) === rankOf(t) + 1;
  }
  function canTab(c, ti){
    if(!known(c)) return false;
    const v = val(c), col = st.tab[ti];
    if(!col.length) return rankOf(v) === 12;
    const top = col[col.length - 1];
    if(!st.up[top] || !known(top)) return false;
    const t = val(top);
    return isRedV(t) !== isRedV(v) && rankOf(t) === rankOf(v) + 1;
  }
  function foundTarget(c){
    for(let fi = 0; fi < 4; fi++) if(st.found[fi].length && canFound(c, fi)) return fi;
    for(let fi = 0; fi < 4; fi++) if(canFound(c, fi)) return fi;
    return -1;
  }
  function movable(ref, idx){
    const p = pile(ref);
    if(idx < 0 || idx >= p.length || ref.t === "stock") return false;
    if(!st.up[p[idx]] || !known(p[idx])) return false;
    if(ref.t === "tab") return p.slice(idx).every(known);
    return idx === p.length - 1;
  }
  function tabTargets(ref, idx){
    const c = pile(ref)[idx];
    const res = [];
    for(let ti = 0; ti < 7; ti++){
      if(ref.t === "tab" && ref.i === ti) continue;
      if(!canTab(c, ti)) continue;
      if(!st.tab[ti].length && ref.t === "tab" && idx === 0) continue;
      res.push(ti);
    }
    res.sort((a, b) => (st.tab[a].length ? 0 : 1) - (st.tab[b].length ? 0 : 1));
    return res;
  }
  function addScore(p){ if(!isGems()) st.score = Math.max(0, (st.score || 0) + p); }

  // ---------- server sync (gems) ----------
  function send(body){
    const s = st;
    const p = sendQ.then(() => g_api("/klondike/move", Object.assign({round_id:s.round_id}, body)));
    sendQ = p.catch(() => {});
    p.then(res => {
      if(s !== gemSt) return;
      Object.entries(res.reveal || {}).forEach(([k, v]) => { s.face[+k] = v; });
      s.home = res.home; s.payoutNow = res.payout_now; s.passes = res.passes;
      refreshFaces();
      if(res.finished) onGemFinished(res);
      if(st === s){ render(); }
    }).catch(err => {
      g_toast("Ошибка хода: " + (err && err.message || err));
      loadGemState(true);
    });
  }
  function onGemFinished(res){
    const s = gemSt;
    s.finished = true;
    if(Array.isArray(res.deck)) res.deck.forEach((v, k) => s.face[k] = v);
    if(typeof res.gems === "number") setGems(res.gems);
    const all = res.home === 52;
    s.won = all;
    const show = () => {
      $("kl-win-title").textContent = all ? "🎉 Победа!" : "Игра окончена";
      $("kl-win-text").innerHTML = "В доме карт: " + res.home + " из 52<br>Ставка: " + res.bet + " " + GEM +
        "<br><b>Получено: " + res.payout + " " + GEM + "</b>";
      $("kl-win-new").textContent = "Ок";
      $("kl-win").classList.add("active");
    };
    if(all){ tgw?.HapticFeedback?.notificationOccurred?.("success"); setTimeout(() => winCascade(show), 350); }
    else show();
    updateGemPanel();
  }

  // ---------- moves ----------
  function doMove(src, idx, dst){
    const sp = pile(src);
    const cards = sp.splice(idx);
    pile(dst).push(...cards);
    if(dst.t === "found") addScore(10);
    else if(src.t === "waste") addScore(5);
    else if(src.t === "found") addScore(-15);
    if(src.t === "tab" && sp.length && !st.up[sp[sp.length - 1]]){ st.up[sp[sp.length - 1]] = 1; addScore(5); }
    st.moves++;
    if(isGems()){
      st.home = st.found.reduce((a, f) => a + f.length, 0);
      send({kind:"move", src, idx, dst});
    }
    tgw?.HapticFeedback?.selectionChanged?.();
    afterChange();
  }
  function drawStock(){
    if(!playable()) return;
    if(!st.stock.length && !st.waste.length) return;
    if(!st.stock.length && isGems() && st.passes >= st.maxPasses){ g_toast("Колоду можно пройти только " + st.maxPasses + " раза"); return; }
    snapshot();
    if(st.stock.length){
      const n = Math.min(st.draw, st.stock.length);
      for(let k = 0; k < n; k++){ const c = st.stock.pop(); st.up[c] = 1; st.waste.push(c); }
    }else{
      while(st.waste.length){ const c = st.waste.pop(); st.up[c] = 0; st.stock.push(c); }
      st.passes = (st.passes || 1) + 1;
      addScore(st.draw === 1 ? -100 : -20);
    }
    st.moves++;
    if(isGems()) send({kind:"draw"});
    afterChange();
  }
  function afterChange(){
    clearHint();
    if(!isGems()){
      if(st.found.every(f => f.length === 13) && !st.won){
        st.won = true;
        stats.wins++;
        if(st.elapsed >= 30) addScore(Math.round(700000 / st.elapsed));
        if((st.score || 0) > (stats.best || 0)) stats.best = st.score;
        render(); save();
        tgw?.HapticFeedback?.notificationOccurred?.("success");
        setTimeout(() => winCascade(showFreeWin), 350);
        return;
      }
      if(!st.counted && st.moves > 0){ st.counted = true; stats.played++; }
    }
    render(); save();
  }
  function tapCard(ref, idx){
    if(ref.t === "stock"){ drawStock(); return; }
    if(!movable(ref, idx)) return;
    const p = pile(ref), c = p[idx];
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
    if(busy || isGems() || !hist.length) return;
    stopCascade();
    const wins = stats.wins;
    st = freeSt = JSON.parse(hist.pop());
    stats.wins = wins;
    $("kl-win").classList.remove("active");
    clearHint(); refreshFaces(); render(); save();
  }

  // ---------- auto-collect ----------
  function nextHomeMove(){
    const srcs = [{t:"waste"}];
    for(let i = 0; i < 7; i++) srcs.push({t:"tab", i});
    let best = null;
    for(const ref of srcs){
      const p = pile(ref);
      if(!p.length) continue;
      const c = p[p.length - 1];
      if(!st.up[c] || !known(c)) continue;
      const fi = foundTarget(c);
      if(fi >= 0 && (!best || rankOf(val(c)) < best.rank)) best = {ref, idx:p.length - 1, fi, rank:rankOf(val(c))};
    }
    return best;
  }
  function collectHome(){
    if(!playable()) return;
    let m = nextHomeMove();
    if(!m){ g_toast("Сейчас нечего отправить в дом"); return; }
    snapshot();
    busy = true;
    const s0 = st;
    const step = () => {
      if(st !== s0){ busy = false; return; }
      m = nextHomeMove();
      if(!m || st.won || st.finished){ busy = false; render(); return; }
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
    const tops = [{t:"waste"}];
    for(let i = 0; i < 7; i++) tops.push({t:"tab", i});
    for(const ref of tops){
      const p = pile(ref);
      if(!p.length || !st.up[p[p.length - 1]]) continue;
      const fi = foundTarget(p[p.length - 1]);
      if(fi >= 0) return {src:[p[p.length - 1]], dst:{t:"found", i:fi}};
    }
    for(let i = 0; i < 7; i++){
      const col = st.tab[i];
      const idx = col.findIndex(c => st.up[c]);
      if(idx < 0) continue;
      const tt = tabTargets({t:"tab", i}, idx);
      if(tt.length) return {src:col.slice(idx), dst:{t:"tab", i:tt[0]}};
    }
    if(st.waste.length){
      const tt = tabTargets({t:"waste"}, st.waste.length - 1);
      if(tt.length) return {src:[st.waste[st.waste.length - 1]], dst:{t:"tab", i:tt[0]}};
    }
    if(st.stock.length) return {stock:true};
    if(st.waste.length && !(isGems() && st.passes >= st.maxPasses)) return {stock:true};
    return null;
  }
  function showHint(){
    if(!playable()) return;
    clearHint();
    const h = findHint();
    if(!h){ g_toast(isGems() ? "Ходов больше нет — нажми «Забрать»" : "Ходов больше нет — попробуй новую раздачу"); return; }
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
      e.className = "kl-card";
      e.innerHTML = `<div class="kl-face"></div><div class="kl-back"></div>`;
      e.dataset.c = c;
      e._v = undefined;
      area.appendChild(e);
      cardEls[c] = e;
    }
  }
  function refreshFaces(){
    for(let c = 0; c < 52; c++){
      const e = cardEls[c];
      const v = st ? st.face[c] : null;
      const k = v !== undefined && v !== null;
      if(e._v === (k ? v : null)) continue;
      e._v = k ? v : null;
      e.classList.toggle("known", k);
      e.classList.toggle("red", k && isRedV(v));
      e.firstChild.innerHTML = k
        ? `<div class="kl-corner"><span class="kl-r">${RANKS[rankOf(v)]}</span><span class="kl-s">${SUITS[suitOf(v)]}</span></div><div class="kl-big">${SUITS[suitOf(v)]}</div>`
        : "";
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
    updateHud();
    if(!st) return;
    const g = layout();
    if(!g) return;
    geo = g;
    area.style.setProperty("--cw", g.cw + "px");
    area.style.setProperty("--ch", g.ch + "px");
    const pos = (e, x, y) => { e.style.transform = `translate(${x}px,${y}px)`; };
    pos(slotEls.stock, g.x(0), 0);
    slotEls.stock.textContent = st.waste.length ? (isGems() && st.passes >= st.maxPasses ? "✕" : "↻") : "";
    for(let i = 0; i < 4; i++) pos(slotEls["found" + i], g.x(3 + i), 0);
    for(let i = 0; i < 7; i++) pos(slotEls["tab" + i], g.x(i), g.y0);
    loc = [];
    let z = 1;
    const place = (c, x, y, ref, idx) => {
      const e = cardEls[c];
      e.style.display = "";
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
      const downStep = g.ch * 0.13;
      let upStep = g.ch * 0.27;
      const downs = col.filter(c => !st.up[c]).length;
      const ups = col.length - downs;
      const need = downs * downStep + Math.max(0, ups - 1) * upStep + g.ch;
      if(need > g.avail && ups > 1) upStep = Math.max(g.ch * 0.15, (g.avail - g.ch - downs * downStep) / (ups - 1));
      let y = g.y0;
      col.forEach((c, idx) => {
        place(c, g.x(i), y, {t:"tab", i}, idx);
        y += st.up[c] ? upStep : downStep;
      });
      if(col.length) maxH = Math.max(maxH, y - (st.up[col[col.length - 1]] ? upStep : downStep) + g.ch);
    });
    area.style.height = Math.ceil(Math.max(maxH, g.y0 + g.ch * 3.2)) + "px";
  }
  function hideAllCards(){
    cardEls.forEach(e => e.style.display = "none");
    const g = layout();
    if(!g) return;
    area.style.setProperty("--cw", g.cw + "px");
    area.style.setProperty("--ch", g.ch + "px");
    const pos = (e, x, y) => { e.style.transform = `translate(${x}px,${y}px)`; };
    pos(slotEls.stock, g.x(0), 0); slotEls.stock.textContent = "";
    for(let i = 0; i < 4; i++) pos(slotEls["found" + i], g.x(3 + i), 0);
    for(let i = 0; i < 7; i++) pos(slotEls["tab" + i], g.x(i), g.y0);
    area.style.height = Math.ceil(g.y0 + g.ch * 3.2) + "px";
  }
  function fmtTime(s){ const m = Math.floor(s / 60); return m + ":" + String(s % 60).padStart(2, "0"); }
  function updateHud(){
    $("kl-mode-chip").innerHTML = mode === "gems" ? "На гемы " + GEM : "Бесплатно";
    $("kl-draw-chip").style.display = mode === "gems" ? "none" : "";
    $("kl-gem-panel").style.display = mode === "gems" ? "" : "none";
    $("kl-undo-btn").style.display = mode === "gems" ? "none" : "";
    if(!st){ $("kl-stat").textContent = ""; $("kl-draw-chip").textContent = "По 3"; $("kl-draw-chip").disabled = true; return; }
    $("kl-stat").textContent = isGems()
      ? "Дом " + st.found.reduce((a, f) => a + f.length, 0) + "/52 · " + Math.min(st.passes, st.maxPasses) + "/" + st.maxPasses
      : "Счёт " + (st.score || 0) + " · " + fmtTime(st.elapsed);
    $("kl-draw-chip").textContent = "По " + st.draw;
    $("kl-draw-chip").disabled = isGems();
    $("kl-undo-btn").disabled = !hist.length;
    $("kl-home-btn").classList.toggle("kl-pulse", playable() && allOpen());
    if(mode === "gems") updateGemPanel();
  }
  function showFreeWin(){
    $("kl-win-title").textContent = "🎉 Победа!";
    $("kl-win-new").textContent = "Новая раздача";
    $("kl-win-text").innerHTML =
      "Счёт: " + (st.score || 0) + "<br>Ходов: " + st.moves + " · Время: " + fmtTime(st.elapsed) +
      "<br>Лучший счёт: " + (stats.best || 0) + " · Побед: " + stats.wins;
    $("kl-win").classList.add("active");
  }

  // ---------- gem panel ----------
  function updateGemPanel(){
    const active = gemSt && !gemSt.finished;
    $("kl-bet-box").style.display = active ? "none" : "";
    $("kl-gem-live").style.display = active ? "" : "none";
    $("kl-bet-value").textContent = gemBet;
    $("kl-deal-bet").textContent = gemBet;
    $("kl-gem-info").textContent = "Каждые " + gemCfg.cards_per_bet + " карт в доме = одна ставка. Весь пасьянс = ставка ×" + (52 / gemCfg.cards_per_bet).toFixed(0) + ".";
    if(active){
      const home = gemSt.found.reduce((a, f) => a + f.length, 0);
      const now = Math.floor(gemSt.bet * home / gemCfg.cards_per_bet);
      $("kl-live-txt").innerHTML = "Ставка " + gemSt.bet + " " + GEM + " · в доме " + home;
      $("kl-cash-btn").innerHTML = "Забрать " + now + " " + GEM;
    }
  }
  async function loadGemState(force){
    if(gemLoaded && !force) return;
    try{
      const r = await g_api("/klondike/state");
      gemLoaded = true;
      gemCfg = {min_bet:r.min_bet, cards_per_bet:r.cards_per_bet, max_passes:r.max_passes};
      if(typeof r.gems === "number") setGems(r.gems);
      gemSt = r.round ? fromServer(r.round) : (gemSt && gemSt.finished ? gemSt : null);
      if(mode === "gems"){ st = gemSt; showMode(); }
    }catch(e){
      g_toast("Не удалось загрузить игру: " + (e && e.message || e));
    }
  }
  async function startGem(){
    if(busy) return;
    if(gemBet < gemCfg.min_bet){ g_toast("Минимальная ставка " + gemCfg.min_bet + " гемов"); return; }
    if(!isAdmin() && gemsNow() < gemBet){ g_toast("Не хватает гемов"); return; }
    busy = true;
    try{
      const r = await g_api("/klondike/start", {bet:gemBet});
      setGems(r.gems);
      stopCascade();
      $("kl-win").classList.remove("active");
      gemSt = fromServer(r.round);
      st = gemSt;
      refreshFaces(); render();
    }catch(e){
      const m = (e && e.message) || String(e);
      g_toast(m.includes("not enough") ? "Не хватает гемов" : m.includes("finish") ? "Сначала закончи текущую раздачу" : "Ошибка: " + m);
      if(m.includes("finish")) loadGemState(true);
    }finally{ busy = false; updateHud(); }
  }
  async function cashGem(){
    if(!gemSt || gemSt.finished || busy) return;
    busy = true;
    const s = gemSt;
    try{
      await sendQ;
      const r = await g_api("/klondike/cashout", {round_id:s.round_id});
      if(s === gemSt) onGemFinished(r);
    }catch(e){
      g_toast("Ошибка: " + ((e && e.message) || e));
      loadGemState(true);
    }finally{ busy = false; refreshFaces(); render(); }
  }

  // ---------- pointer input ----------
  function cardAt(e){
    const el = e.target.closest(".kl-card");
    if(el) return {c:+el.dataset.c};
    if(e.target.closest(".kl-slot-stock")) return {stockSlot:true};
    return null;
  }
  board.addEventListener("pointerdown", (e) => {
    if(!playable() || drag) return;
    const hit = cardAt(e);
    if(!hit) return;
    e.preventDefault();
    if(hit.stockSlot){ drag = {stockOnly:true, id:e.pointerId}; return; }
    const L = loc[hit.c];
    if(!L) return;
    const ref = {t:L.t, i:L.i};
    if(ref.t === "stock"){ drag = {stockOnly:true, id:e.pointerId}; return; }
    let idx = L.idx;
    if(ref.t !== "tab") idx = pile(ref).length - 1;
    if(!movable(ref, idx)) return;
    drag = {ref, idx, cards:pile(ref).slice(idx), id:e.pointerId, sx:e.clientX, sy:e.clientY, moved:false};
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
    if(!playable()){ render(); return; }
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

  // ---------- win animation (classic bouncing cards) ----------
  let cascade = null;
  function stopCascade(){
    if(!cascade) return null;
    cancelAnimationFrame(cascade.raf);
    cascade.cv.remove();
    cardEls.forEach(e => e.style.visibility = "");
    const cb = cascade.done; cascade = null;
    return cb;
  }
  function rr(ctx, x, y, w, h, r){
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }
  function drawCardCv(ctx, v, x, y, w, h){
    rr(ctx, x, y, w, h, w * 0.17);
    ctx.fillStyle = "#f7f8f9"; ctx.fill();
    ctx.strokeStyle = "rgba(0,0,0,.25)"; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = isRedV(v) ? "#dc2626" : "#000";
    ctx.textBaseline = "top"; ctx.textAlign = "left";
    ctx.font = `900 ${w * 0.34}px -apple-system,'Segoe UI',Arial,sans-serif`;
    const rk = RANKS[rankOf(v)];
    ctx.fillText(rk, x + w * 0.08, y + w * 0.06);
    const rw = ctx.measureText(rk).width;
    const s = SUITS[suitOf(v)];
    ctx.font = `${w * 0.28}px -apple-system,'Segoe UI',Arial,sans-serif`;
    ctx.fillText(s, x + w * 0.08 + rw + 1, y + w * 0.09);
    ctx.font = `${w * 0.62}px -apple-system,'Segoe UI',Arial,sans-serif`;
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(s, x + w / 2, y + h * 0.58);
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
      cur = {v:st.face[o.c], x:padX + g.x(3 + o.fi), y:padY, vx:dir * (2 + Math.random() * 3.5), vy:-(Math.random() * 6)};
      return true;
    };
    launch();
    const frame = () => {
      if(!cascade) return;
      for(let k = 0; k < 2 && cur; k++){
        cur.vy += 0.6; cur.x += cur.vx; cur.y += cur.vy;
        if(cur.y > H - g.ch){ cur.y = H - g.ch; cur.vy = -cur.vy * 0.78; }
        drawCardCv(ctx, cur.v, cur.x, cur.y, g.cw, g.ch);
        if(cur.x < -g.cw || cur.x > W){ if(!launch()) return; }
      }
      cascade.raf = requestAnimationFrame(frame);
    };
    cv.addEventListener("pointerdown", (e) => { e.stopPropagation(); const cb = stopCascade(); cb && cb(); });
    cascade.raf = requestAnimationFrame(frame);
  }

  // ---------- persistence (free mode only; gem games live on the server) ----------
  function save(){
    if(!freeSt) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const data = JSON.stringify({s:freeSt, stats});
      try{ localStorage.setItem(FREE_KEY, data); }catch(e){}
      try{ tgw?.CloudStorage?.setItem?.(FREE_KEY, data, () => {}); }catch(e){}
    }, 600);
  }
  function applyLoaded(raw){
    try{
      const o = JSON.parse(raw);
      if(o && o.s && validFree(o.s)){ freeSt = o.s; freeSt.mode = "free"; if(o.stats) stats = o.stats; hist = []; return true; }
    }catch(e){}
    return false;
  }

  // ---------- controls ----------
  function confirmAsk(msg, cb){
    if(tgw?.showConfirm){ try{ tgw.showConfirm(msg, ok => { if(ok) cb(); }); return; }catch(e){} }
    if(confirm(msg)) cb();
  }
  function showMode(){
    stopCascade();
    $("kl-win").classList.remove("active");
    clearHint();
    if(st){ refreshFaces(); render(); }
    else { hideAllCards(); updateHud(); }
  }
  function setMode(m){
    if(busy) return;
    mode = m;
    try{ localStorage.setItem(MODE_KEY, m); }catch(e){}
    if(m === "free"){
      if(!freeSt) newFreeDeal(1);
      st = freeSt;
      showMode();
    }else{
      st = gemSt;
      showMode();
      loadGemState(true);
    }
  }
  $("kl-mode-chip").addEventListener("click", () => setMode(mode === "gems" ? "free" : "gems"));
  $("kl-new-btn").addEventListener("click", () => {
    if(busy) return;
    if(mode === "free"){
      if(!st || st.won || st.moves === 0) newFreeDeal(st ? st.draw : 1);
      else confirmAsk("Начать новую раздачу?", () => newFreeDeal(st.draw));
    }else{
      if(gemSt && !gemSt.finished){
        const home = gemSt.found.reduce((a, f) => a + f.length, 0);
        confirmAsk("Закончить раздачу и забрать " + Math.floor(gemSt.bet * home / gemCfg.cards_per_bet) + " гемов?", cashGem);
      }else{
        $("kl-win").classList.remove("active");
        g_toast("Выбери ставку и нажми «Раздать»");
      }
    }
  });
  $("kl-win-new").addEventListener("click", () => {
    $("kl-win").classList.remove("active");
    stopCascade();
    if(mode === "free") newFreeDeal(st.draw);
    else try{ block.scrollIntoView({block:"start", behavior:"smooth"}); }catch(e){}
  });
  $("kl-undo-btn").addEventListener("click", undo);
  $("kl-hint-btn").addEventListener("click", showHint);
  $("kl-home-btn").addEventListener("click", collectHome);
  $("kl-cash-btn").addEventListener("click", () => {
    if(!gemSt || gemSt.finished) return;
    const home = gemSt.found.reduce((a, f) => a + f.length, 0);
    confirmAsk("Закончить раздачу и забрать " + Math.floor(gemSt.bet * home / gemCfg.cards_per_bet) + " гемов?", cashGem);
  });
  $("kl-deal-btn").addEventListener("click", startGem);
  $("kl-presets").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-bet]");
    if(!b) return;
    gemBet = +b.dataset.bet;
    $("kl-presets").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
    updateGemPanel();
  });
  $("kl-bet-edit").addEventListener("click", async () => {
    let v = null;
    try{ v = await promptNumber("Ставка (гемов, минимум " + gemCfg.min_bet + ")"); }catch(e){}
    if(v && v >= gemCfg.min_bet){
      gemBet = Math.floor(v);
      $("kl-presets").querySelectorAll("button").forEach(x => x.classList.toggle("active", +x.dataset.bet === gemBet));
      updateGemPanel();
    }else if(v){ g_toast("Минимальная ставка " + gemCfg.min_bet + " гемов"); }
  });
  $("kl-draw-chip").addEventListener("click", () => {
    if(busy || isGems() || !st) return;
    const next = st.draw === 1 ? 3 : 1;
    if(st.moves === 0 || st.won) newFreeDeal(next);
    else confirmAsk("Открывать по " + next + " и начать новую раздачу?", () => newFreeDeal(next));
  });
  $("kl-info-btn").addEventListener("click", () => rules.classList.add("active"));
  $("kl-rules-close").addEventListener("click", () => rules.classList.remove("active"));
  rules.addEventListener("click", (e) => { if(e.target === rules) rules.classList.remove("active"); });

  // ---------- visibility / timer ----------
  function isVisible(){ return block.style.display !== "none" && !!document.getElementById("pvp-screen")?.classList.contains("active"); }
  let swipesOff = false, everShown = false;
  window.klOnVisibility = function(){
    const v = isVisible();
    if(v !== swipesOff){
      swipesOff = v;
      try{ v ? tgw?.disableVerticalSwipes?.() : tgw?.enableVerticalSwipes?.(); }catch(e){}
    }
    if(!v) return;
    if(!everShown){ everShown = true; if(mode === "gems") loadGemState(true); }
    try{ block.scrollIntoView({block:"start", behavior:"smooth"}); }catch(e){}
    if(st){ render(); } else { hideAllCards(); updateHud(); }
  };
  setInterval(() => {
    if(!st || isGems() || st.won || st.moves === 0 || document.hidden || !isVisible()) return;
    st.elapsed++;
    updateHud();
    if(st.elapsed % 10 === 0) save();
  }, 1000);
  window.addEventListener("resize", () => { if(isVisible()) (st ? render() : hideAllCards()); });

  // ---------- init ----------
  buildDom();
  try{ mode = localStorage.getItem(MODE_KEY) === "gems" ? "gems" : "free"; }catch(e){}
  let local = null;
  try{ local = localStorage.getItem(FREE_KEY); }catch(e){}
  const startFree = () => { if(!freeSt){ newFreeDeal(1); } if(mode === "free"){ st = freeSt; refreshFaces(); render(); } };
  if(local && applyLoaded(local)){
    if(mode === "free"){ st = freeSt; refreshFaces(); }
  }else if(tgw?.CloudStorage?.getItem){
    let done = false;
    const fin = () => { if(done) return; done = true; startFree(); };
    try{
      tgw.CloudStorage.getItem(FREE_KEY, (err, val) => { if(!done && val) applyLoaded(val); fin(); });
      setTimeout(fin, 2500);
    }catch(e){ fin(); }
  }else{
    startFree();
  }
  if(mode === "free" && !freeSt) { /* waiting for CloudStorage */ }
  updateHud();
})();

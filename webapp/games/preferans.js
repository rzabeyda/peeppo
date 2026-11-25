/* «Преферанс» (Сочи, короткая пуля) против 2 ботов -- webapp/games/preferans.js
   Логика на сервере (/api/pref/*). Использует api(), toast(), setGemsDisplay(), gemsBalance из index.html. */
(function(){
  "use strict";
  const SYM = {S:"♠", C:"♣", D:"♦", H:"♥", N:"БК"};
  const RED = new Set(["H","D"]);
  const lbl = r => r === "T" ? "10" : r;
  const GEM = '<img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">';
  const ORDER = ["6S","6C","6D","6H","6N","7S","7C","7D","7H","7N","8S","8C","8D","8H","8N","MIS","9S","9C","9D","9H","9N","10S","10C","10D","10H","10N"];

  let view = null, busy = false, bet = 25, sel = [], selContract = null, lastTrickKey = "";

  const cLevel = c => c.startsWith("10") ? 10 : +c[0];
  const cSuit = c => c[c.length - 1];
  function cn(c){
    if(!c) return "";
    if(c === "MIS") return "Мизер";
    if(c === "RASP") return "Распасы";
    if(c === "pass") return "Пас";
    const s = cSuit(c);
    return cLevel(c) + (s === "N" ? " БК" : SYM[s]);
  }

  function cardHTML(code, cls, style){
    const st = style ? ` style="${style}"` : "";
    if(!code) return `<div class="poker-playing-card back ${cls||""}"${st}></div>`;
    const r = code[0], s = code[1];
    return `<div class="poker-playing-card ${RED.has(s) ? "red" : ""} ${cls||""}" data-c="${code}"${st}>
      <div class="poker-corner tl"><div class="pc-rank">${lbl(r)}</div><div class="pf-cs">${SYM[s]}</div></div>
      <div class="pc-center-suit">${SYM[s]}</div>
      <div class="poker-corner br"><div class="pc-rank">${lbl(r)}</div><div class="pf-cs">${SYM[s]}</div></div>
    </div>`;
  }

  function build(){
    const anchor = document.getElementById("pvp-durak-block") || document.getElementById("pvp-poker-block");
    if(!anchor) return false;
    const el = document.createElement("div");
    el.id = "pvp-pref-block";
    el.style.display = "none";
    el.innerHTML = `
      <div class="pf-card">
        <div class="game-title-row">
          <div class="pf-title">Преферанс</div>
          <span class="game-turnover" data-game="pref"></span>
          <button type="button" class="game-info-btn" id="pf-info">?</button>
        </div>
        <div class="pf-setup" id="pf-setup">
          <div class="poker-bet-row">
            <span class="poker-bet-label">Ставка:</span>
            <span class="poker-bet-value" id="pf-bet">25</span>
            <span class="poker-bet-unit">${GEM}</span>
            <button class="poker-bet-edit" id="pf-bet-edit">Изменить</button>
          </div>
          <div class="poker-presets" id="pf-presets">
            <button data-bet="5">5</button><button data-bet="25" class="active">25</button>
            <button data-bet="50">50</button><button data-bet="100">100</button><button data-bet="250">250</button>
          </div>
          <button class="pf-start-btn" id="pf-start">Начать пулю</button>
          <div class="pf-sub">Сочи, ты против двух ботов, короткая пуля из 6 раздач. Если в конце ты в плюсе по вистам, получаешь ×1.9 ставки, при нуле ставка возвращается.</div>
        </div>
        <div class="pf-felt" id="pf-felt" style="display:none">
          <div class="pf-top"><div class="pf-bot" id="pf-p1"></div><div class="pf-bot" id="pf-p2"></div></div>
          <div class="pf-center" id="pf-center"></div>
          <div class="pf-info"><span id="pf-info-l"></span><span id="pf-info-r"></span></div>
          <div class="pf-note" id="pf-note"></div>
          <div class="pf-actions" id="pf-actions"></div>
          <div class="pf-hand" id="pf-hand"></div>
          <div class="pf-foot"><span id="pf-me"></span><span><button id="pf-score-btn">Запись</button> <button id="pf-surrender">Сдаться</button></span></div>
        </div>
        <div class="pf-over" id="pf-over"><div class="pf-over-box" id="pf-over-box"></div></div>
      </div>`;
    anchor.parentNode.insertBefore(el, anchor.nextSibling);

    el.querySelector("#pf-presets").addEventListener("click", e=>{
      const b = e.target.closest("button"); if(b) setBet(parseInt(b.dataset.bet, 10));
    });
    el.querySelector("#pf-bet-edit").addEventListener("click", ()=>{
      const v = parseInt(prompt("Ставка в гемах:", String(bet)) || "", 10);
      if(v >= 1) setBet(v);
    });
    el.querySelector("#pf-start").addEventListener("click", startGame);
    el.querySelector("#pf-info").addEventListener("click", showRules);
    el.querySelector("#pf-score-btn").addEventListener("click", ()=>{ if(view) showOver(`<div class="pf-over-title">Запись</div>${scoreTable(view)}<button class="pf-start-btn" id="pf-close">Закрыть</button>`); });
    el.querySelector("#pf-surrender").addEventListener("click", ()=>{
      if(view && !view.result && confirm("Сдаться? Ставка сгорит.")) act({action:"surrender"});
    });
    el.querySelector("#pf-over").addEventListener("click", e=>{
      if(e.target.id === "pf-close" || (e.target.id === "pf-over" && view && !view.result)) hideOver();
    });
    el.querySelector("#pf-hand").addEventListener("click", e=>{
      const c = e.target.closest(".poker-playing-card"); if(!c || !view || busy) return;
      const code = c.dataset.c;
      if(view.phase === "discard" && view.turn === 0){
        if(sel.includes(code)) sel = sel.filter(x=>x !== code);
        else if(sel.length < 2) sel.push(code);
        else { sel.shift(); sel.push(code); }
        render(view, false);
      }else if(view.phase === "play" && c.classList.contains("pf-ok")){
        act({action:"play", card: code});
      }
    });
    el.querySelector("#pf-actions").addEventListener("click", e=>{
      const b = e.target.closest("button"); if(!b || b.disabled || busy || !view) return;
      const a = b.dataset.a;
      if(a === "bid") act({action:"bid", bid: b.dataset.v});
      else if(a === "pick"){ selContract = b.dataset.v; render(view, false); }
      else if(a === "declare") act({action:"declare", cards: sel, contract: selContract});
      else if(a === "vist" || a === "pass") act({action: a});
      else if(a === "next") act({action:"next"});
    });
    window.addEventListener("resize", ()=>{ if(view) renderHand(); });
    return true;
  }

  function setBet(v){
    bet = v;
    document.getElementById("pf-bet").textContent = v;
    document.querySelectorAll("#pf-presets button").forEach(b=>b.classList.toggle("active", parseInt(b.dataset.bet,10) === v));
  }
  function showOver(html){ document.getElementById("pf-over-box").innerHTML = html; document.getElementById("pf-over").classList.add("active"); }
  function hideOver(){ document.getElementById("pf-over").classList.remove("active"); }

  function showRules(){
    showOver(`<div class="pf-over-title">Правила</div>
      <div class="pf-rules">
        <b>32 карты</b> (от 7 до туза), каждому по 10, 2 карты уходят в <b>прикуп</b>.<br>
        <b>Торговля.</b> Заявки идут по старшинству: 6♠ 6♣ 6♦ 6♥ 6БК 7♠ … 10БК. Мизер стоит между 8БК и 9♠, и заявить его можно только первым словом. Кто спасовал, дальше не торгуется.<br>
        <b>Игра.</b> Победитель торговли берёт прикуп, сносит 2 карты и заказывает игру не ниже своей заявки. Надо взять столько взяток, сколько заказал.<br>
        <b>Вист.</b> Остальные решают, вистовать или пасовать. Если оба спасовали, игра засчитана без розыгрыша.<br>
        <b>Мизер</b>: надо не взять ни одной взятки.<br>
        <b>Распасы</b>, если все спасовали: каждый играет за себя, взятка идёт в гору, а кто не взял ни одной, пишет 1 в пулю.<br>
        Ходить нужно в масть. Если масти нет, надо бить козырем, если козыря нет, сбрасывай любую.<br>
        <b>Цена игры:</b> 6 = 2, 7 = 4, 8 = 6, 9 = 8, 10 = 10, мизер = 10. Сыграл — пишешь в пулю, не добрал — в гору. Вистующие пишут висты за свои взятки.<br><br>
        <b>Пуля — 6 раздач.</b> Итог в вистах: (пуля − гора) × 10 + висты, считается относительно среднего. В плюсе получаешь <b>×1.9</b> ставки, при нуле ставка возвращается, в минусе сгорает.
      </div>
      <button class="pf-start-btn" id="pf-close">Понятно</button>`);
  }

  function scoreTable(v){
    const cls = x => x > 0 ? "pos" : (x < 0 ? "neg" : "");
    const rows = [0,1,2].map(i=>`<tr><td>${v.names[i]}</td><td>${v.pool[i]}</td><td>${v.gora[i]}</td><td>${v.vists_net[i] > 0 ? "+" : ""}${v.vists_net[i]}</td><td class="${cls(v.totals[i])}">${v.totals[i] > 0 ? "+" : ""}${v.totals[i]}</td></tr>`).join("");
    return `<table class="pf-score"><tr><th></th><th>Пуля</th><th>Гора</th><th>Висты</th><th>Итог</th></tr>${rows}</table>`;
  }

  function botPanel(v, i){
    const said = v.phase === "vist" || (v.phase === "play" && v.contract && v.contract !== "RASP" && v.contract !== "MIS" && v.declarer !== i)
      ? (v.vist[i] ? (v.vist[i] === "vist" ? "Вист" : "Пас") : "")
      : (v.said[i] ? cn(v.said[i]) : "");
    const pcls = said === "Пас" ? "pf-said pass" : "pf-said";
    const role = v.declarer === i && v.contract ? ` · играет ${cn(v.contract)}` : "";
    return `<div class="pf-bot-name">🤖 ${v.names[i]}${v.dealer === i ? ' <span class="pf-dealer">С</span>' : ""}</div>
      <div class="pf-bot-line"><span>🂠 ${v.counts[i]}</span><span>взяток ${v.tricks[i]}</span>${said ? `<span class="${pcls}">${said}</span>` : ""}</div>
      ${role ? `<div class="pf-bot-line">${role.slice(3)}</div>` : ""}`;
  }

  function renderCenter(v, animate){
    const c = document.getElementById("pf-center");
    if(v.phase === "discard" && v.prikup){
      const mine = v.declarer === 0;
      c.innerHTML = `<div class="pf-prikup"><div class="lbl">Прикуп${mine ? " (уже у тебя в руке)" : ""}</div><div class="row">${v.prikup.map(x=>cardHTML(x,"pf-s")).join("")}</div></div>`;
      return;
    }
    let trick = v.trick && v.trick.length ? v.trick : null;
    if(!trick && v.last_trick && v.last_trick.length && v.phase === "play" && busy) trick = v.last_trick;
    const key = JSON.stringify(trick || []);
    const prev = lastTrickKey; lastTrickKey = key;
    if(!trick){ c.innerHTML = v.phase === "summary" ? "" : ""; return; }
    c.innerHTML = trick.map(([p, card])=>{
      const nw = animate && !prev.includes(`"${card}"`) ? " pf-c-new" : "";
      return cardHTML(card, `pf-s pf-slot-${p}${nw}`);
    }).join("");
  }

  function renderActions(v){
    const a = document.getElementById("pf-actions");
    const mine = v.turn === 0 && !busy && !v.result;
    if(v.phase === "summary"){
      a.innerHTML = `<div class="pf-sum">${(v.summary && v.summary.text) || ""}</div>${scoreTable(v)}
        <div class="pf-row"><button class="pf-go" data-a="next" ${busy ? "disabled" : ""}>${v.deal_no >= v.max_deals ? "Подвести итог" : "Следующая раздача"}</button></div>`;
      return;
    }
    if(!mine){ a.innerHTML = ""; return; }
    if(v.phase === "bidding"){
      const allowed = new Set(v.allowed_bids);
      const grid = ORDER.filter(c=>c !== "MIS").map(c=>{
        const s = cSuit(c);
        return `<button data-a="bid" data-v="${c}" class="${RED.has(s) ? "red" : ""}" ${allowed.has(c) ? "" : "disabled"}>${cn(c).replace(" ", "")}</button>`;
      }).join("");
      a.innerHTML = `<div class="pf-grid">${grid}</div>
        <div class="pf-row"><button class="pf-pass" data-a="bid" data-v="pass">Пас</button><button data-a="bid" data-v="MIS" ${allowed.has("MIS") ? "" : "disabled"}>Мизер</button></div>`;
      return;
    }
    if(v.phase === "discard"){
      const allowed = v.allowed_contracts;
      if(!allowed.includes(selContract)) selContract = allowed[0];
      let picks;
      if(allowed.length === 1 && allowed[0] === "MIS") picks = "";
      else picks = `<div class="pf-grid">${ORDER.filter(c=>c !== "MIS").map(c=>`<button data-a="pick" data-v="${c}" class="${RED.has(cSuit(c)) ? "red" : ""} ${c === selContract ? "sel" : ""}" ${allowed.includes(c) ? "" : "disabled"}>${cn(c).replace(" ", "")}</button>`).join("")}</div>`;
      a.innerHTML = `${picks}<div class="pf-row"><button class="pf-go" data-a="declare" ${sel.length === 2 ? "" : "disabled"}>Снести ${sel.length}/2 и играть ${cn(selContract)}</button></div>`;
      return;
    }
    if(v.phase === "vist"){
      a.innerHTML = `<div class="pf-row"><button class="pf-vist" data-a="vist">Вист</button><button class="pf-pass" data-a="pass">Пас</button></div>`;
      return;
    }
    a.innerHTML = "";
  }

  function renderHand(){
    const v = view; if(!v) return;
    const wrap = document.getElementById("pf-hand");
    const hand = v.hand || [];
    const legal = new Set(v.legal || []);
    const W = wrap.clientWidth || 320, cw = 54, n = hand.length;
    const step = n > 1 ? Math.min(cw + 4, (W - cw) / (n - 1)) : 0;
    const left0 = Math.max(0, (W - (n ? cw + step * (n - 1) : 0)) / 2);
    const myPlay = v.phase === "play" && v.turn === 0 && !busy;
    wrap.innerHTML = hand.map((c, i)=>{
      let cls = "";
      if(myPlay) cls = legal.has(c) ? "pf-ok" : "pf-dim";
      if(v.phase === "discard" && v.turn === 0 && sel.includes(c)) cls = "pf-sel";
      return cardHTML(c, cls, `left:${left0 + i*step}px;z-index:${i+1}`);
    }).join("");
  }

  function render(v, animate){
    view = v;
    document.getElementById("pf-setup").style.display = v ? "none" : "";
    document.getElementById("pf-felt").style.display = v ? "" : "none";
    if(!v) return;
    if(v.phase !== "discard") sel = [];
    document.getElementById("pf-p1").innerHTML = botPanel(v, 1);
    document.getElementById("pf-p2").innerHTML = botPanel(v, 2);
    document.getElementById("pf-p1").classList.toggle("turn", v.turn === 1 && !v.result && v.phase !== "summary");
    document.getElementById("pf-p2").classList.toggle("turn", v.turn === 2 && !v.result && v.phase !== "summary");
    renderCenter(v, animate);
    const ct = v.contract ? cn(v.contract) : (v.high ? "торг: " + cn(v.high) : "торговля");
    document.getElementById("pf-info-l").textContent = `Раздача ${v.deal_no}/${v.max_deals}`;
    document.getElementById("pf-info-r").textContent = v.declarer !== null && v.contract && v.contract !== "RASP" ? `${v.names[v.declarer]}: ${ct}` : ct;
    let note = v.note || "";
    if(v.turn === 0 && !busy && !v.result){
      if(v.phase === "bidding") note = (note ? note + "\n" : "") + "Твоё слово";
      else if(v.phase === "discard") note = "Выбери 2 карты в снос и игру";
      else if(v.phase === "vist") note = `${v.names[v.declarer]} играет ${cn(v.contract)}. Вистуешь?`;
      else if(v.phase === "play") note = (note ? note + " · " : "") + "Твой ход";
    }
    document.getElementById("pf-note").textContent = note;
    const mySaid = (v.phase === "vist" || v.phase === "play") && v.vist[0] ? (v.vist[0] === "vist" ? "вист" : "пас") : (v.said[0] ? cn(v.said[0]) : "");
    document.getElementById("pf-me").innerHTML = `Ты${v.dealer === 0 ? " (сдаёшь)" : ""} · взяток ${v.tricks[0]}${mySaid ? " · " + mySaid : ""} · ставка ${v.bet} ${GEM}`;
    document.getElementById("pf-surrender").style.display = v.result ? "none" : "";
    renderActions(v);
    renderHand();
    if(v.result) showResult(v);
  }

  function showResult(v){
    const t = {win:"🏆 Ты в плюсе!", lose:"📉 Ты в минусе", draw:"🤝 Ровно"}[v.result];
    const txt = v.result === "win" ? `Выигрыш <b>${v.payout}</b> ${GEM}` : v.result === "draw" ? `Ставка ${v.bet} ${GEM} возвращена` : `Ставка ${v.bet} ${GEM} проиграна`;
    setTimeout(()=>{
      showOver(`<div class="pf-over-title">${t}</div><div class="pf-over-text">${txt}</div>${scoreTable(v)}
        <button class="pf-start-btn" id="pf-again">Ещё пуля</button>`);
      document.getElementById("pf-again").onclick = ()=>{ hideOver(); render(null); };
    }, 500);
  }

  async function playFrames(res){
    const frames = res.frames && res.frames.length ? res.frames : [res];
    for(let i = 0; i < frames.length; i++){
      const last = i === frames.length - 1;
      busy = !last;
      render(frames[i], true);
      if(!last){
        const f = frames[i];
        const full = f.trick && f.trick.length === 3;
        await new Promise(r=>setTimeout(r, full ? 1000 : (f.phase === "discard" ? 1400 : 650)));
      }
    }
    busy = false;
    if(typeof res.gems === "number" && typeof setGemsDisplay === "function") setGemsDisplay(res.gems);
  }

  async function startGame(){
    if(busy) return;
    if(typeof gemsBalance === "number" && gemsBalance < bet && typeof isAdminUser !== "undefined" && !isAdminUser){ toast("Не хватает гемов"); return; }
    busy = true; document.getElementById("pf-start").disabled = true;
    try{
      const res = await api("/pref/start", {bet});
      hideOver(); busy = false; lastTrickKey = "";
      await playFrames(res);
    }catch(e){
      toast(e.message === "not enough gems" ? "Не хватает гемов" : "Ошибка: " + e.message);
      if(/уже есть/.test(e.message)) resume();
    }finally{ busy = false; document.getElementById("pf-start").disabled = false; }
  }

  async function act(body){
    if(busy || !view || view.result) return;
    busy = true; render(view, false);
    try{
      const res = await api("/pref/act", Object.assign({game_id: view.game_id}, body));
      busy = false; sel = [];
      await playFrames(res);
    }catch(e){ busy = false; toast(e.message); resume(); }
  }

  async function resume(){
    try{
      const r = await api("/pref/active");
      if(r.game) render(r.game, false);
      else if(!view || !view.result) render(null);
    }catch(e){}
  }

  function init(){
    if(!build()) return;
    const sw = document.getElementById("pvp-mode-switch");
    if(sw) sw.addEventListener("click", e=>{
      const btn = e.target.closest(".mode-switch-btn"); if(!btn) return;
      const on = btn.dataset.mode === "pref";
      document.getElementById("pvp-pref-block").style.display = on ? "" : "none";
      if(on) resume();
    });
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

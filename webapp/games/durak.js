/* «Дурак» подкидной 1 на 1 против бота -- webapp/games/durak.js
   Вся логика на сервере (/api/durak/*). Использует глобальные api(), toast(),
   setGemsDisplay(), gemsBalance из index.html и стили карт покера. */
(function(){
  "use strict";
  const SYM = {S:"♠", H:"♥", D:"♦", C:"♣"};
  const RED = new Set(["H","D"]);
  const lbl = r => r === "T" ? "10" : r;
  const GEM = '<img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">';

  let view = null, busy = false, bet = 25, inited = false, lastTableKey = "";

  function cardHTML(code, cls){
    if(!code) return `<div class="poker-playing-card back ${cls||""}"></div>`;
    const r = code[0], s = code[1];
    return `<div class="poker-playing-card ${RED.has(s) ? "red" : ""} ${cls||""}" data-c="${code}">
      <div class="poker-corner tl"><div class="pc-rank">${lbl(r)}</div><div class="dk-cs">${SYM[s]}</div></div>
      <div class="pc-center-suit">${SYM[s]}</div>
      <div class="poker-corner br"><div class="pc-rank">${lbl(r)}</div><div class="dk-cs">${SYM[s]}</div></div>
    </div>`;
  }

  function build(){
    const anchor = document.getElementById("pvp-poker-block");
    if(!anchor) return false;
    const el = document.createElement("div");
    el.id = "pvp-durak-block";
    el.style.display = "none";
    el.innerHTML = `
      <div class="dk-card">
        <div class="game-title-row">
          <div class="dk-title">Дурак</div>
          <span class="game-turnover" data-game="durak"></span>
          <button type="button" class="game-info-btn" id="dk-info">?</button>
        </div>
        <div class="dk-setup" id="dk-setup">
          <div class="poker-bet-row">
            <span class="poker-bet-label">Ставка:</span>
            <span class="poker-bet-value" id="dk-bet">25</span>
            <span class="poker-bet-unit">${GEM}</span>
            <button class="poker-bet-edit" id="dk-bet-edit">Изменить</button>
          </div>
          <div class="poker-presets" id="dk-presets">
            <button data-bet="5">5</button><button data-bet="25" class="active">25</button>
            <button data-bet="50">50</button><button data-bet="100">100</button><button data-bet="250">250</button>
          </div>
          <button class="dk-start-btn" id="dk-start">Сдать карты</button>
          <div class="dk-sub">Подкидной, 36 карт, 1 на 1 с ботом. Победа — ×1.9 ставки, ничья — ставка возвращается.</div>
        </div>
        <div class="dk-felt" id="dk-felt" style="display:none">
          <div class="dk-opp"><span class="dk-opp-name" id="dk-opp-name">🤖 Бот</span><div class="dk-opp-cards" id="dk-opp"></div></div>
          <div class="dk-mid"><div class="dk-deck" id="dk-deck"></div><div class="dk-table" id="dk-table"></div></div>
          <div class="dk-note" id="dk-note"></div>
          <div class="dk-actions">
            <button class="dk-btn-take" id="dk-take">Беру</button>
            <button class="dk-btn-done" id="dk-done">Бито</button>
          </div>
          <div class="dk-hand" id="dk-hand"></div>
          <div class="dk-foot"><span id="dk-info-line"></span><button id="dk-surrender">Сдаться</button></div>
        </div>
        <div class="dk-over" id="dk-over"><div class="dk-over-box" id="dk-over-box"></div></div>
      </div>`;
    anchor.parentNode.insertBefore(el, anchor.nextSibling);

    el.querySelector("#dk-presets").addEventListener("click", e=>{
      const b = e.target.closest("button"); if(!b) return;
      setBet(parseInt(b.dataset.bet, 10));
    });
    el.querySelector("#dk-bet-edit").addEventListener("click", ()=>{
      const v = parseInt(prompt("Ставка в гемах:", String(bet)) || "", 10);
      if(v >= 1) setBet(v);
    });
    el.querySelector("#dk-start").addEventListener("click", startGame);
    el.querySelector("#dk-take").addEventListener("click", ()=>act("take"));
    el.querySelector("#dk-done").addEventListener("click", ()=>act("done"));
    el.querySelector("#dk-surrender").addEventListener("click", ()=>{
      if(!view || view.result) return;
      if(confirm("Сдаться? Ставка сгорит.")) act("surrender");
    });
    el.querySelector("#dk-hand").addEventListener("click", e=>{
      const c = e.target.closest(".poker-playing-card"); if(!c) return;
      if(!c.classList.contains("dk-ok")) return;
      act("play", c.dataset.c);
    });
    el.querySelector("#dk-info").addEventListener("click", showRules);
    el.querySelector("#dk-over").addEventListener("click", e=>{
      if(e.target.id === "dk-over" && (!view || !view.result)) hideOver();
    });
    window.addEventListener("resize", ()=>{ if(view) renderHand(); });
    return true;
  }

  function setBet(v){
    bet = v;
    document.getElementById("dk-bet").textContent = v;
    document.querySelectorAll("#dk-presets button").forEach(b=>b.classList.toggle("active", parseInt(b.dataset.bet,10) === v));
  }

  function showOver(html){
    document.getElementById("dk-over-box").innerHTML = html;
    document.getElementById("dk-over").classList.add("active");
  }
  function hideOver(){ document.getElementById("dk-over").classList.remove("active"); }

  function showRules(){
    showOver(`<div class="dk-over-title">Правила</div>
      <div class="dk-rules">
        <b>Колода 36 карт</b>, у каждого по 6. Нижняя карта колоды — <b>козырь</b>.<br>
        Первым ходит тот, у кого младший козырь.<br>
        <b>Атакующий</b> кладёт карту, <b>защитник</b> бьёт её старшей картой той же масти или козырем.<br>
        Подкидывать можно карты того же достоинства, что уже лежат на столе (не больше 6, в первом отбое — 5, и не больше, чем карт у защитника).<br>
        Не можешь или не хочешь отбиваться — жми <b>«Беру»</b>. Всё отбито — атакующий жмёт <b>«Бито»</b>, ход переходит.<br>
        После отбоя все добирают до 6 карт из колоды.<br>
        Кто остался с картами, когда колода кончилась, — <b>дурак</b>.<br><br>
        Победа — <b>×1.9</b> ставки, ничья — возврат ставки, «Сдаться» — ставка сгорает.<br>
        Тапни по подсвеченной карте, чтобы сходить ей.
      </div>
      <button class="dk-start-btn" onclick="document.getElementById('dk-over').classList.remove('active')">Понятно</button>`);
  }

  function renderHand(){
    const wrap = document.getElementById("dk-hand");
    const hand = view.hand || [];
    const ok = new Set(view.playable || []);
    const W = wrap.clientWidth || 320, cw = 54;
    const n = hand.length;
    const step = n > 1 ? Math.min(cw + 4, (W - cw) / (n - 1)) : 0;
    const total = n ? cw + step * (n - 1) : 0;
    const left0 = Math.max(0, (W - total) / 2);
    const myTurn = view.turn === "you" && !busy;
    wrap.innerHTML = hand.map((c, i)=>{
      const cls = myTurn ? (ok.has(c) ? "dk-ok" : "dk-dim") : "";
      return cardHTML(c, cls).replace('class="poker-playing-card', `style="left:${left0 + i*step}px;z-index:${i+1}" class="poker-playing-card`);
    }).join("");
  }

  function render(v, animate){
    view = v;
    const playing = !!v && !v.result;
    document.getElementById("dk-setup").style.display = v && (playing || v.result) ? "none" : "";
    document.getElementById("dk-felt").style.display = v ? "" : "none";
    if(!v) return;
    document.getElementById("dk-opp").innerHTML = (v.result && v.opp_hand)
      ? v.opp_hand.map(c=>cardHTML(c, "dk-s")).join("")
      : Array(v.opp_count).fill(0).map(()=>cardHTML(null)).join("");
    document.getElementById("dk-opp-name").textContent = `🤖 Бот · ${v.result ? (v.opp_hand||[]).length : v.opp_count}`;
    const deck = document.getElementById("dk-deck");
    const ts = v.trump[1];
    if(v.deck_count > 0){
      deck.innerHTML = cardHTML(v.trump, "dk-s dk-trump") + (v.deck_count > 1 ? cardHTML(null, "dk-s dk-deckback") + `<div class="dk-deck-count">${v.deck_count}</div>` : "");
    }else{
      deck.innerHTML = `<div class="dk-deck-empty ${RED.has(ts) ? "" : ""}" style="color:${RED.has(ts) ? "#ef4444" : "#fff"}">${SYM[ts]}</div>`;
    }
    const tbl = document.getElementById("dk-table");
    const prevKey = lastTableKey;
    lastTableKey = JSON.stringify(v.table);
    tbl.innerHTML = v.table.map(([a, d])=>{
      const na = animate && !prevKey.includes(`"${a}"`) ? " dk-new" : "";
      const nd = d && animate && !prevKey.includes(`"${d}"`) ? " dk-new" : "";
      return `<div class="dk-pair">${cardHTML(a, "dk-s" + na)}${d ? cardHTML(d, "dk-s dk-def" + nd) : ""}</div>`;
    }).join("");
    let note = v.note || "";
    if(playing && v.turn === "you" && !busy){
      if(v.you_attack && !v.table.length) note = note ? note + " · твой ход" : "Твой ход";
      else if(v.you_attack && v.taking) note = "Бот берёт — подкинь или жми «Отдать»";
      else if(v.you_attack) note = note || "Подкинь или жми «Бито»";
      else if(!v.playable.length) note = "Нечем бить — жми «Беру»";
      else note = note || "Отбивайся";
    }
    document.getElementById("dk-note").textContent = note;
    const take = document.getElementById("dk-take"), done = document.getElementById("dk-done");
    take.disabled = busy || !v.can_take;
    done.disabled = busy || !v.can_done;
    done.textContent = v.taking && v.you_attack ? "Отдать" : "Бито";
    take.style.display = v.you_attack ? "none" : "";
    done.style.display = v.you_attack ? "" : "none";
    document.getElementById("dk-info-line").innerHTML = `Ставка ${v.bet} ${GEM} · козырь ${SYM[ts]} · бито ${v.discard}`;
    document.getElementById("dk-surrender").style.display = playing ? "" : "none";
    renderHand();
    if(v.result) showResult(v);
  }

  function showResult(v){
    const t = {win:"🏆 Победа!", lose:"🃏 Ты дурак", draw:"🤝 Ничья"}[v.result];
    const txt = v.result === "win" ? `Выигрыш <b>${v.payout}</b> ${GEM}`
      : v.result === "draw" ? `Ставка ${v.bet} ${GEM} возвращена` : `Ставка ${v.bet} ${GEM} проиграна`;
    const opp = (v.opp_hand && v.opp_hand.length) ? `<div class="dk-over-text">Карты бота:</div><div class="dk-over-opp">${v.opp_hand.map(c=>cardHTML(c,"dk-s")).join("")}</div>` : "";
    setTimeout(()=>{
      showOver(`<div class="dk-over-title">${t}</div><div class="dk-over-text">${txt}</div>${opp}
        <button class="dk-start-btn" id="dk-again">Ещё партия</button>`);
      document.getElementById("dk-again").onclick = ()=>{ hideOver(); view = null; lastTableKey = ""; render(null); document.getElementById("dk-setup").style.display = ""; };
    }, 500);
  }

  async function playFrames(res){
    const frames = res.frames && res.frames.length ? res.frames : [res];
    for(let i = 0; i < frames.length; i++){
      const last = i === frames.length - 1;
      busy = !last;
      render(frames[i], true);
      if(!last) await new Promise(r=>setTimeout(r, 650));
    }
    busy = false;
    if(typeof res.gems === "number" && typeof setGemsDisplay === "function") setGemsDisplay(res.gems);
  }

  async function startGame(){
    if(busy) return;
    if(typeof gemsBalance === "number" && gemsBalance < bet && typeof isAdminUser !== "undefined" && !isAdminUser){
      toast("Не хватает гемов"); return;
    }
    busy = true; document.getElementById("dk-start").disabled = true;
    try{
      const res = await api("/durak/start", {bet});
      lastTableKey = "";
      hideOver();
      busy = false;
      await playFrames(res);
    }catch(e){
      toast(e.message === "not enough gems" ? "Не хватает гемов" : "Ошибка: " + e.message);
      if(/уже есть/.test(e.message)) resume();
    }finally{
      busy = false; document.getElementById("dk-start").disabled = false;
    }
  }

  async function act(action, card){
    if(busy || !view || view.result) return;
    busy = true;
    if(view) render(view, false);
    try{
      const res = await api("/durak/act", {game_id: view.game_id, action, card: card || null});
      busy = false;
      await playFrames(res);
    }catch(e){
      busy = false;
      toast(e.message);
      resume();
    }
  }

  async function resume(){
    try{
      const r = await api("/durak/active");
      if(r.game){ lastTableKey = ""; render(r.game, false); }
      else if(!view || !view.result){ render(null); }
    }catch(e){}
  }

  function onModeClick(e){
    const btn = e.target.closest(".mode-switch-btn"); if(!btn) return;
    const on = btn.dataset.mode === "durak";
    const blk = document.getElementById("pvp-durak-block");
    if(blk) blk.style.display = on ? "" : "none";
    if(on){
      if(!inited){ inited = true; }
      resume();
    }
  }

  function init(){
    if(!build()) return;
    const sw = document.getElementById("pvp-mode-switch");
    if(sw) sw.addEventListener("click", onModeClick);
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

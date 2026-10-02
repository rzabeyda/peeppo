
const tg = window.Telegram?.WebApp;
function applyTgViewportHeight(){
  if(tg && tg.viewportStableHeight){
    document.documentElement.style.setProperty("--tg-vh", tg.viewportStableHeight + "px");
  }
}
if (tg) {
  tg.ready();
  tg.expand();
  applyTgViewportHeight();
  tg.onEvent("viewportChanged", applyTgViewportHeight);
}

// Light haptic tap + a quick visual "vibrate" bounce on every button/card/nav
// press, app-wide — one listener instead of wiring it into every click handler.
document.addEventListener("click", (e)=>{
  const el = e.target.closest("button, .grid-item, .nav-btn, .history-row, .picker-item, .mode-switch-btn, .tops-switch-btn");
  if(el){
    tg?.HapticFeedback?.impactOccurred("light");
    el.classList.remove("tap-vibrate");
    void el.offsetWidth; // restart the animation even on rapid repeated taps
    el.classList.add("tap-vibrate");
    el.addEventListener("animationend", ()=> el.classList.remove("tap-vibrate"), { once: true });
  }
}, true);

const API_BASE = "/api";
// Bumped whenever a card image file is replaced under the same filename (e.g. an
// artwork swap) so browsers/Telegram's WebView don't keep serving a cached, stale
// picture from before the swap -- every /static/cards/ URL below carries this as a
// query string, which is enough to bust the cache without renaming any files.
const CARD_IMG_VERSION = 3;
const FARM_COST_GEMS = 25;
// Mirrors database.py's MIN_LISTING_PRICE_BY_RARITY — client-side copy for the price
// prompt + pre-check (server still enforces it for real via ListingPriceTooLow).
const MIN_LISTING_PRICE_BY_RARITY = { bronze: 25, silver: 50, gold: 100, platinum: 100, diamond: 100 };
function minListingPrice(rarity){
  return MIN_LISTING_PRICE_BY_RARITY[rarity] ?? MIN_LISTING_PRICE_BY_RARITY.bronze;
}
let initData = tg?.initData || "";
let inventory = [];
// The infinity symbol is shown ONLY on the admin's own account (isAdminUser, set from
// /auth's is_admin flag in loadProfile()) — a cosmetic flex for the dev, never for real
// players, who always see their honest balance. gemsBalance tracks the real numeric
// value separately from the displayed text either way, so client-side "can I afford
// this" checks stay correct even while the admin's display shows the infinity symbol.
let isAdminUser = false;
let gemsBalance = 0;
function setGemsDisplay(n){
  gemsBalance = n;
  document.getElementById("gems-amount").textContent = isAdminUser ? "∞" : n;
}
let daysUntilNextIncome = null;
let selectedUserCardId = null;
let selectedItem = null;

function toast(msg){
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(()=>el.classList.remove("show"), 3000);
}
function toastHTML(html){
  const el = document.getElementById("toast");
  el.innerHTML = html;
  el.classList.add("show");
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(()=>el.classList.remove("show"), 3000);
}
function notEnoughGemsToast(){
  toastHTML(`Не хватает гемов — нужно ${FARM_COST_GEMS} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
}
function showAlert(text){
  document.getElementById("alert-text").textContent = text;
  document.getElementById("alert-overlay").classList.add("active");
}
function showAlertHTML(html){
  document.getElementById("alert-text").innerHTML = html;
  document.getElementById("alert-overlay").classList.add("active");
}
document.getElementById("alert-ok").addEventListener("click", ()=>{
  document.getElementById("alert-overlay").classList.remove("active");
});

// Custom in-app confirm modal (replaces tg.showConfirm) — Telegram's native popup button
// order is controlled by the Telegram client, not us, and can't be forced to a fixed
// side. This one is ours, so "OK"/confirm always renders on the left, cancel on the right.
let genericConfirmResolve = null;
function confirmAction(text){
  return new Promise(resolve=>{
    genericConfirmResolve = resolve;
    document.getElementById("confirm-text").textContent = text;
    document.getElementById("confirm-overlay").classList.add("active");
  });
}
function closeConfirmModal(ok){
  document.getElementById("confirm-overlay").classList.remove("active");
  if(genericConfirmResolve) genericConfirmResolve(ok);
  genericConfirmResolve = null;
}
document.getElementById("confirm-ok").addEventListener("click", ()=>closeConfirmModal(true));
document.getElementById("confirm-cancel").addEventListener("click", ()=>closeConfirmModal(false));

// Custom in-app confirm modal for staking — tg.showConfirm() is capped at 256 chars by
// Telegram, and the staking rules text (rates for all 5 rarities + limits) blew past that,
// which made Telegram silently reject the popup call and the WebApp appear to hang for a
// few seconds before bouncing back. A real HTML modal has no such limit.
let stakeConfirmResolve = null;
function showStakeConfirm(fee){
  return new Promise(resolve=>{
    stakeConfirmResolve = resolve;
    document.getElementById("stake-confirm-fee").innerHTML = `${fee} <img src="/static/icons/diamond.png" alt="">`;
    document.getElementById("stake-confirm-overlay").classList.add("active");
  });
}
function closeStakeConfirm(ok){
  document.getElementById("stake-confirm-overlay").classList.remove("active");
  if(stakeConfirmResolve) stakeConfirmResolve(ok);
  stakeConfirmResolve = null;
}
document.getElementById("stake-confirm-cancel").addEventListener("click", ()=>closeStakeConfirm(false));
document.getElementById("stake-confirm-confirm").addEventListener("click", ()=>closeStakeConfirm(true));

// ---------- Generic input modal (buy gems / list price / offer / gift username) ----------
let inputResolve = null;
let inputNumeric = true;
function promptInput(title, {numeric=true, placeholder=""}={}){
  return new Promise(resolve=>{
    inputResolve = resolve;
    inputNumeric = numeric;
    document.getElementById("input-title").textContent = title;
    const field = document.getElementById("input-field");
    field.type = numeric ? "number" : "text";
    field.setAttribute("inputmode", numeric ? "numeric" : "text");
    field.placeholder = placeholder;
    field.value = "";
    document.getElementById("input-overlay").classList.add("active");
    setTimeout(()=>field.focus(), 50);
  });
}
function promptNumber(title){
  return promptInput(title, { numeric: true });
}
function promptText(title, placeholder=""){
  return promptInput(title, { numeric: false, placeholder });
}
function closeInputModal(value){
  document.getElementById("input-overlay").classList.remove("active");
  if(inputResolve) inputResolve(value);
  inputResolve = null;
}
document.getElementById("input-cancel").addEventListener("click", ()=>closeInputModal(null));
document.getElementById("input-confirm").addEventListener("click", ()=>{
  const field = document.getElementById("input-field");
  if(inputNumeric){
    const val = parseInt(field.value, 10);
    closeInputModal(Number.isFinite(val) && val > 0 ? val : null);
  }else{
    const val = field.value.trim().replace(/^@/, "");
    closeInputModal(val ? val : null);
  }
});

async function shareGameResult(game, roundId, btn){
  if(!roundId || !btn) return;
  btn.disabled = true;
  const original = btn.textContent;
  try{
    await api("/games/share", { game, round_id: roundId });
    btn.textContent = "Опубликовано ✓";
    tg?.HapticFeedback?.notificationOccurred("success");
  }catch(e){
    toast("Не удалось поделиться: " + e.message);
    btn.textContent = original;
    btn.disabled = false;
  }
}

async function api(path, body={}){
  const res = await fetch(API_BASE + path, {
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ initData, ...body })
  });
  if(!res.ok){
    let detail = res.statusText;
    try{
      const data = await res.json();
      if(data && data.detail) detail = data.detail;
    }catch(e){ /* body wasn't JSON — keep statusText */ }
    throw new Error(detail);
  }
  return res.json();
}

// ---------- Navigation ----------
function switchScreen(screenId){
  // Leaving the games tab with an uncollected poker win pending (status stays 'won' server-side
  // until /poker/collect is called) used to strand that round out of history/the leaderboard
  // forever -- collectPokerWinnings() no-ops safely if there's nothing open to collect.
  if(screenId !== "pvp-screen" && typeof collectPokerWinnings === "function") collectPokerWinnings();
  document.querySelectorAll(".nav-btn").forEach(b=>b.classList.remove("active"));
  document.querySelectorAll(".screen").forEach(s=>s.classList.remove("active"));
  document.querySelector(`.nav-btn[data-screen="${screenId}"]`)?.classList.add("active");
  document.getElementById(screenId).classList.add("active");
  document.getElementById("app").classList.toggle("bg-farm", screenId === "farm-screen");
  const mainEl = document.querySelector("main");
  mainEl.classList.toggle("no-scroll", screenId === "farm-screen");
  // Switching screens reuses the SAME scrollable <main> for all of them -- if the
  // previous screen (e.g. a long PvP participant list) was scrolled down, that
  // scrollTop carries over as-is. no-scroll only blocks FURTHER scrolling, it doesn't
  // reset the existing position, so farm-screen's content rendered shifted/clipped
  // exactly as if still scrolled. Reset it on every navigation so each screen always
  // starts at the top.
  mainEl.scrollTop = 0;
  if(screenId !== "pvp-screen") stopPvpPolling();
  if(screenId === "farm-screen") loadStats(); // re-sync "Всего" — it drifts stale otherwise, since it only updates from THIS player's own farm/craft/case/burn actions
  if(screenId === "profile-screen") loadProfile();
  if(screenId === "market-screen") refreshMarketScreen();
  if(screenId === "pvp-screen"){ pvpTabJustOpened = true; loadPvpState(); setPvpPulse(false); }
}

// Ambient PvP check — runs regardless of which tab is open, so a round starting
// (the join countdown ticking) shows up as a pulsing dot on the PvP tab even if
// the player is off browsing Farm/Market/Profile.
let pvpAmbientTimer = null;
function setPvpPulse(on){
  document.getElementById("pvp-nav-pulse")?.classList.toggle("visible", !!on);
}
async function checkPvpAmbient(){
  const activeScreen = document.querySelector(".nav-btn.active")?.dataset.screen;
  if(activeScreen === "pvp-screen") return; // already looking at it, no need for a dot
  try{
    const state = await api("/pvp/state");
    const counting = state.seconds_left !== null && state.seconds_left !== undefined;
    setPvpPulse(counting);
  }catch(e){ /* ambient check — stay silent on failure */ }
}
function startPvpAmbientPolling(){
  if(pvpAmbientTimer) return;
  checkPvpAmbient();
  pvpAmbientTimer = setInterval(checkPvpAmbient, 5000);
}
startPvpAmbientPolling();
document.querySelectorAll(".nav-btn").forEach(btn=>{
  btn.addEventListener("click", ()=>{
    // Tapping the bottom-nav "Рынок" button should always land on the Торговля
    // sub-tab with filters visible -- marketMode is a global that goToMarketSwapTab()
    // flips to "swap" after listing a card for barter, and it used to stay "swap"
    // (filter row hidden) on every later visit via this nav button until the page reloaded.
    if(btn.dataset.screen === "market-screen") setMarketMode("trade");
    switchScreen(btn.dataset.screen);
  });
});
document.querySelector('.nav-btn[data-screen="profile-screen"]').classList.add("active");
document.getElementById("profile-screen").classList.add("active");
loadProfile();

// ---------- Farm ----------
async function loadStats(){
  try{
    const res = await fetch(API_BASE + "/stats");
    const data = await res.json();
    document.getElementById("total-farmed").textContent = data.total_farmed;
  }catch(e){ /* non-critical, just skip */ }
}
loadStats();

// ---------- Gems ----------
let currentStreak = { days: 1, bonus: 2 };
async function refreshGems(){
  try{
    const auth = await api("/auth");
    isAdminUser = !!auth.is_admin;
    setGemsDisplay(auth.gems);
    applyPlayerRank(auth.player_rank);
    myTelegramId = auth.telegram_id;
    document.getElementById("bot-day-count").textContent = auth.bot_day || 1;
    if(auth.streak){
      currentStreak = auth.streak;
      document.getElementById("stat-streak").textContent = auth.streak.days;
    }
    if(auth.is_new){
      document.getElementById("welcome-overlay").classList.add("active");
    }else if(auth.daily_bonus > 0){
      document.getElementById("daily-bonus-overlay").classList.add("active");
    }
    if(auth.referral_reward_notice){
      showReferralRewardModal(auth.referral_reward_notice.name, auth.referral_reward_notice.amount);
    }
    if(auth.wheel_available){
      // Small delay so it doesn't visually collide with the welcome/daily-bonus
      // overlays above if one of those is also showing right now.
      setTimeout(maybeOpenFortuneWheel, 1200);
    }
    if(auth.gem_mining) renderMining(auth.gem_mining);
  }catch(e){ /* non-critical */ }
}
refreshGems();

// Read-once in-app popup for "you earned 25 gems for a referral" — replaces the old
// bot-DM notifications, auto-closes on its own after 3s (no button to tap).
function showReferralRewardModal(whoName, amount){
  const el = document.getElementById("referral-reward-overlay");
  document.getElementById("referral-reward-text").innerHTML =
    `Ты получил ${amount} гемов за реферала — <span class="who">${whoName}</span>`;
  el.classList.add("active");
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(()=>el.classList.remove("active"), 3000);
}

document.getElementById("welcome-ok").addEventListener("click", ()=>{
  document.getElementById("welcome-overlay").classList.remove("active");
  // welcome-overlay only ever shows for a brand-new player (auth.is_new) — right after
  // it closes, walk them through every game feature once via the same reference modal
  // the "?" button opens, so onboarding and the reference doc never drift apart.
  document.getElementById("profile-info-overlay").classList.add("active");
});

document.getElementById("daily-bonus-ok").addEventListener("click", ()=>{
  document.getElementById("daily-bonus-overlay").classList.remove("active");
});

function showCardsBreakdown(){
  document.getElementById("chance-title").textContent = "Твоя коллекция";
  document.getElementById("chance-sub").textContent = "Разбивка по редкости — только твои карты";
  document.getElementById("chance-total").textContent = `Всего: ${inventory.length}`;
  document.getElementById("farmed-diamond").textContent = inventory.filter(c=>c.rarity==="diamond").length;
  document.getElementById("farmed-platinum").textContent = inventory.filter(c=>c.rarity==="platinum").length;
  document.getElementById("farmed-gold").textContent = inventory.filter(c=>c.rarity==="gold").length;
  document.getElementById("farmed-silver").textContent = inventory.filter(c=>c.rarity==="silver").length;
  document.getElementById("farmed-bronze").textContent = inventory.filter(c=>c.rarity==="bronze").length;
  document.getElementById("chance-overlay").classList.add("active");
}
async function showGlobalCardsBreakdown(){
  document.getElementById("chance-title").textContent = "Карты всех игроков";
  document.getElementById("chance-sub").textContent = "Разбивка по редкости — все игроки суммарно";
  try{
    const res = await fetch(API_BASE + "/stats/breakdown");
    const data = await res.json();
    document.getElementById("farmed-diamond").textContent = data.diamond ?? 0;
    document.getElementById("farmed-platinum").textContent = data.platinum ?? 0;
    document.getElementById("farmed-gold").textContent = data.gold ?? 0;
    document.getElementById("farmed-silver").textContent = data.silver ?? 0;
    document.getElementById("farmed-bronze").textContent = data.bronze ?? 0;
    const total = (data.diamond ?? 0) + (data.platinum ?? 0) + (data.gold ?? 0) + (data.silver ?? 0) + (data.bronze ?? 0);
    document.getElementById("chance-total").textContent = `Всего: ${total}`;
  }catch(e){ /* non-critical, just leave whatever was there */ }
  document.getElementById("chance-overlay").classList.add("active");
}
document.getElementById("farm-total-pill").addEventListener("click", showGlobalCardsBreakdown);
document.getElementById("stat-cards-tile").addEventListener("click", showCardsBreakdown);
document.getElementById("stat-streak-tile").addEventListener("click", ()=>{
  const days = currentStreak.days || 0;
  const bonus = currentStreak.bonus || 0;
  document.getElementById("streak-info-current").innerHTML =
    `${pluralDays(days)} · +${bonus} <img src="/static/icons/diamond.png" alt="">`;
  document.getElementById("streak-info-overlay").classList.add("active");
});
document.getElementById("streak-info-close").addEventListener("click", ()=>{
  document.getElementById("streak-info-overlay").classList.remove("active");
});
document.getElementById("streak-info-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "streak-info-overlay"){
    document.getElementById("streak-info-overlay").classList.remove("active");
  }
});
document.getElementById("chance-close").addEventListener("click", ()=>{
  document.getElementById("chance-overlay").classList.remove("active");
});
document.getElementById("chance-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "chance-overlay"){
    document.getElementById("chance-overlay").classList.remove("active");
  }
});

document.getElementById("gems-pill").addEventListener("click", ()=>{
  document.getElementById("gems-choice-overlay").classList.add("active");
});
document.getElementById("gems-choice-cancel").addEventListener("click", ()=>{
  document.getElementById("gems-choice-overlay").classList.remove("active");
});
document.getElementById("gems-choice-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "gems-choice-overlay"){
    document.getElementById("gems-choice-overlay").classList.remove("active");
  }
});
document.getElementById("gems-choice-sell").addEventListener("click", async ()=>{
  document.getElementById("gems-choice-overlay").classList.remove("active");
  try{
    const status = await api("/crypto/status");
    if(status.pending){
      showAlert(`У тебя уже есть заявка на вывод ${status.pending.gram_amount} Stars — она на рассмотрении. Дождись решения, прежде чем создавать новую.`);
      return;
    }
  }catch(e){
    toast("Не удалось проверить заявки: " + e.message);
    return;
  }
  openGramPicker();
});

// ---------- GRAM withdrawal picker (10 Diamond cards = 1 GRAM, multiples of 10 only) ----------
const GRAM_CARDS_PER_UNIT = 10;
const STARS_PER_UNIT = 50;
let gramPickerSelected = new Set();

function gramPickerUpdateSub(){
  const n = gramPickerSelected.size;
  const sub = document.getElementById("gram-picker-sub");
  const ready = n > 0 && n % GRAM_CARDS_PER_UNIT === 0;
  sub.textContent = ready
    ? `Выбрано: ${n} → получишь ${(n / GRAM_CARDS_PER_UNIT) * STARS_PER_UNIT} Stars`
    : `Выбрано: ${n} — нужно кратно ${GRAM_CARDS_PER_UNIT} (10, 20, 30…)`;
  sub.classList.toggle("ready", ready);
}

function openGramPicker(){
  gramPickerSelected = new Set();
  gramPickerUpdateSub();
  renderGramPickerGrid();
  document.getElementById("gram-picker-overlay").classList.add("active");
}

function renderGramPickerGrid(){
  const grid = document.getElementById("gram-picker-grid");
  const eligible = inventory.filter(c => c.rarity === "diamond" && !c.pvp_round_id && !c.listed_price && !c.swap_listed && !c.staked_at && !c.in_giveaway);
  if(eligible.length === 0){
    grid.innerHTML = `<div class="empty-state">Нет свободных Diamond карт</div>`;
    return;
  }
  grid.innerHTML = eligible.map(c => `
    <div class="grid-item picker-item rarity-border-${c.rarity} ${gramPickerSelected.has(c.user_card_id) ? 'selected' : ''}" data-uc="${c.user_card_id}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name || ''}">
      <div class="rarity-tag ${c.rarity}">${c.rarity}</div>
      <div class="check">✓</div>
    </div>
  `).join("");
  grid.querySelectorAll(".picker-item").forEach(el=>{
    el.addEventListener("click", ()=>{
      const ucId = parseInt(el.dataset.uc);
      if(gramPickerSelected.has(ucId)){
        gramPickerSelected.delete(ucId);
        el.classList.remove("selected");
      }else{
        gramPickerSelected.add(ucId);
        el.classList.add("selected");
      }
      gramPickerUpdateSub();
    });
  });
}

document.getElementById("gram-picker-cancel").addEventListener("click", ()=>{
  document.getElementById("gram-picker-overlay").classList.remove("active");
});

document.getElementById("gram-picker-confirm").addEventListener("click", async ()=>{
  const n = gramPickerSelected.size;
  if(n === 0 || n % GRAM_CARDS_PER_UNIT !== 0){
    toast(`Выбери карты кратно ${GRAM_CARDS_PER_UNIT} (10, 20, 30…)`);
    return;
  }
  const ids = [...gramPickerSelected];
  document.getElementById("gram-picker-overlay").classList.remove("active");

  try{
    const res = await api("/crypto/withdraw", { user_card_ids: ids, wallet_address: "" });
    try{
      const data = await api("/profile");
      inventory = data.inventory;
      sortInventoryAndRender();
    }catch(e){ /* non-critical — next profile visit will pick it up anyway */ }
    showAlert("Ожидай, заявка обрабатывается ⏳");
  }catch(e){
    toast("Не удалось отправить заявку: " + e.message);
  }
});
document.getElementById("gems-choice-buy").addEventListener("click", async ()=>{
  document.getElementById("gems-choice-overlay").classList.remove("active");
  let gems = await promptNumber("Сколько гемов купить? (1000 гемов = 100 звёзд)");
  if(!gems) return;
  if(gems % 10 !== 0){
    gems = Math.ceil(gems / 10) * 10;
    toast(`Округлено до ${gems} гемов (кратно 10)`);
  }
  try{
    const res = await api("/gems/invoice", { gems });
    if(tg?.openInvoice){
      tg.openInvoice(res.invoice_link, (status)=>{
        if(status === "paid"){
          toast("Оплачено! Зачисляем гемы...");
          setTimeout(refreshGems, 1500);
        }else if(status === "failed"){
          toast("Платёж не прошёл");
        }
      });
    }else{
      window.open(res.invoice_link, "_blank");
      toast("Открой ссылку оплаты и вернись сюда");
    }
  }catch(e){
    toast("Не удалось создать счёт: " + e.message);
  }
});

// ---------- Market ----------
let marketListings = [];

async function loadMarket(){
  try{
    const data = await api("/market/listings");
    setGemsDisplay(data.gems);
    marketListings = data.listings;
    renderMarket();
  }catch(e){
    toast("Не удалось загрузить рынок: " + e.message);
  }
}

let marketSortMode = "price_asc"; // "price_asc" | "price_desc" | "number" | "rarity_bronze" | "rarity_diamond"
document.querySelectorAll("#market-filter-row .market-filter-btn").forEach(btn=>{
  btn.addEventListener("click", ()=>{
    marketSortMode = btn.dataset.sort;
    document.querySelectorAll("#market-filter-row .market-filter-btn").forEach(b=>{
      b.classList.toggle("active", b === btn);
    });
    renderMarket();
  });
});

function renderMarket(){
  const list = document.getElementById("market-list");
  if(marketListings.length === 0){
    list.className = "";
    list.innerHTML = `<div class="empty-state"><span class="emoji">🛒</span>Пока никто ничего не продаёт.<br>Выстави свою картинку в профиле!</div>`;
    return;
  }

  const sortMode = marketSortMode;
  const sorted = [...marketListings].sort((a, b) => {
    switch(sortMode){
      case "price_desc": return b.listed_price - a.listed_price;
      case "number": return a.drop_number - b.drop_number;
      case "rarity_bronze": return (RARITY_ORDER[b.rarity] ?? 4) - (RARITY_ORDER[a.rarity] ?? 4);
      case "rarity_diamond": return (RARITY_ORDER[a.rarity] ?? 4) - (RARITY_ORDER[b.rarity] ?? 4);
      case "price_asc":
      default: return a.listed_price - b.listed_price;
    }
  });

  list.className = "grid";
  list.innerHTML = sorted.map(l => `
      <div class="grid-item rarity-border-${l.rarity}${l.custom_name ? " obsidian-border" : ""}" data-uc="${l.user_card_id}">
        <img src="/static/cards/${l.filename}?v=${CARD_IMG_VERSION}" alt="${l.name||''}">
        <div class="rarity-tag ${l.custom_name ? "obsidian" : l.rarity}">${l.custom_name ? "OBSIDIAN" : l.rarity}</div>
        <div class="market-number-tag">#${l.drop_number}</div>
        <div class="market-price-tag"><img src="/static/icons/diamond.png" alt="">${l.listed_price}</div>
        ${l.is_mine ? `<div class="market-mine-badge">Твой</div>` : ""}
      </div>
    `).join("");

  list.querySelectorAll(".grid-item").forEach(el=>{
    el.addEventListener("click", ()=>openMarketItem(parseInt(el.dataset.uc), "trade"));
  });
}

// ---------- Market: swap / barter mode ----------
let marketMode = "trade"; // "trade" | "swap"
let swapListings = [];

function refreshMarketScreen(){
  if(marketMode === "swap"){ loadSwapListings(); }
  else { loadMarket(); }
}

function setMarketMode(mode){
  marketMode = mode;
  document.querySelectorAll(".mode-switch-btn").forEach(b=>{
    b.classList.toggle("active", b.dataset.mode === mode);
  });
  document.getElementById("market-filter-row").classList.toggle("hidden", mode !== "trade");
}

// Called right after successfully listing a card for swap, so the player lands
// straight on the listing they just created instead of wondering if it worked.
function goToMarketSwapTab(){
  setMarketMode("swap");
  switchScreen("market-screen");
}

// ---------- История (trade/swap/PvP round log — mode-aware, one shared panel) ----------
function pluralCards(n){
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if(abs > 10 && abs < 20) return `${n} карт`;
  if(last === 1) return `${n} карта`;
  if(last >= 2 && last <= 4) return `${n} карты`;
  return `${n} карт`;
}

function pluralDays(n){
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if(abs > 10 && abs < 20) return `${n} дней`;
  if(last === 1) return `${n} день`;
  if(last >= 2 && last <= 4) return `${n} дня`;
  return `${n} дней`;
}

document.getElementById("day-counter-pill").addEventListener("click", ()=>{
  const daysNum = parseInt(document.getElementById("bot-day-count").textContent, 10) || 1;
  document.getElementById("day-info-title").textContent = daysNum <= 1
    ? "Peeppo запущен сегодня!"
    : `Peeppo запущен ${pluralDays(daysNum)} назад`;
  document.getElementById("day-info-overlay").classList.add("active");
});
document.getElementById("day-info-close").addEventListener("click", ()=>{
  document.getElementById("day-info-overlay").classList.remove("active");
});
document.getElementById("day-info-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "day-info-overlay"){
    document.getElementById("day-info-overlay").classList.remove("active");
  }
});

function fmtHistoryDate(iso){
  if(!iso) return "";
  try{
    const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
    return d.toLocaleString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }catch(e){ return ""; }
}

async function openHistory(explicitMode){
  const overlay = document.getElementById("history-overlay");
  const title = document.getElementById("history-title");
  const list = document.getElementById("history-list");
  overlay.classList.add("active");
  list.innerHTML = `<div class="empty-state">Загрузка...</div>`;

  const mode = explicitMode || marketMode;
  if(mode === "swap"){
    title.textContent = "История обменов";
    try{
      const data = await api("/swap/history");
      renderSwapHistory(data.trades || []);
    }catch(e){ list.innerHTML = `<div class="empty-state">Не удалось загрузить историю</div>`; }
  }else if(mode === "pvp"){
    title.textContent = "История PvP";
    try{
      const data = await api("/pvp/history");
      renderPvpHistory(data.rounds || []);
    }catch(e){ list.innerHTML = `<div class="empty-state">Не удалось загрузить историю</div>`; }
  }else if(mode === "redblack"){
    title.textContent = "История Red&Black";
    try{
      const data = await api("/redblack/history");
      renderRedBlackHistoryList(data.rounds || []);
    }catch(e){ list.innerHTML = `<div class="empty-state">Не удалось загрузить историю</div>`; }
  }else if(mode === "aviator"){
    title.textContent = "История Ракетки";
    try{
      const data = await api("/aviator/history");
      renderAviatorHistoryList(data.rounds || []);
    }catch(e){ list.innerHTML = `<div class="empty-state">Не удалось загрузить историю</div>`; }
  }else if(mode === "poker"){
    title.textContent = "История Покера";
    try{
      const data = await api("/poker/history");
      renderPokerHistoryList(data.rounds || []);
    }catch(e){ list.innerHTML = `<div class="empty-state">Не удалось загрузить историю</div>`; }
  }else if(mode === "mines"){
    title.textContent = "История Минных полей";
    try{
      const data = await api("/mines/history");
      renderMinesHistoryList(data.rounds || []);
    }catch(e){ list.innerHTML = `<div class="empty-state">Не удалось загрузить историю</div>`; }
  }else{
    title.textContent = "История продаж";
    try{
      const data = await api("/market/history");
      renderMarketHistory(data.trades || []);
    }catch(e){ list.innerHTML = `<div class="empty-state">Не удалось загрузить историю</div>`; }
  }
}

function renderMarketHistory(trades){
  const list = document.getElementById("history-list");
  if(!trades.length){
    list.innerHTML = `<div class="empty-state"><span class="emoji">🛒</span>Сделок пока не было.</div>`;
    return;
  }
  list.innerHTML = trades.map(t => `
    <div class="history-row market-history-row rarity-border-${t.rarity}">
      <div class="thumbs"><img class="rarity-border-${t.rarity}" src="/static/cards/${t.filename}?v=${CARD_IMG_VERSION}" alt=""></div>
      <div class="info">
        <div class="line1">${capName(t.name) || "Без названия"}</div>
        <div class="line2">${fmtHistoryDate(t.completed_at)}</div>
      </div>
      <div class="price">${t.price_gems} <img src="/static/icons/diamond.png" alt="" style="width:12px;height:12px;vertical-align:-1px;"></div>
    </div>
  `).join("");
}

function renderSwapHistory(trades){
  const list = document.getElementById("history-list");
  if(!trades.length){
    list.innerHTML = `<div class="empty-state"><span class="emoji">🔄</span>Обменов пока не было.</div>`;
    return;
  }
  list.innerHTML = trades.map(t => {
    const firstOffered = t.offered_cards[0];
    const offeredThumb = firstOffered ? `<img class="rarity-border-${firstOffered.rarity}" src="/static/cards/${firstOffered.filename}?v=${CARD_IMG_VERSION}" alt="">` : "";
    const offeredNames = t.offered_cards.map(c=>c.name).filter(Boolean);
    const offeredLabel = offeredNames.length > 1
      ? `${offeredNames[0]} +${offeredNames.length - 1}`
      : (offeredNames[0] || "?");
    return `
      <div class="history-row rarity-border-${t.listed_card.rarity}">
        <div class="thumbs"><img class="rarity-border-${t.listed_card.rarity}" src="/static/cards/${t.listed_card.filename}?v=${CARD_IMG_VERSION}" alt="">${offeredThumb}</div>
        <div class="info">
          <div class="line1">${capName(t.listed_card.name) || "Без названия"} ⇄ ${offeredLabel}</div>
          <div class="line2">${fmtHistoryDate(t.accepted_at)}</div>
        </div>
      </div>
    `;
  }).join("");
}

function renderPvpHistory(rounds){
  const list = document.getElementById("history-list");
  if(!rounds.length){
    list.innerHTML = `<div class="empty-state"><span class="emoji">⚔️</span>Раундов пока не было.</div>`;
    return;
  }
  list.innerHTML = rounds.map(r => {
    // A win with under a 20% chance is a real underdog moment -- call it out with a
    // pink/fuchsia border instead of the usual neutral one.
    const underdog = r.winner_win_pct != null && r.winner_win_pct < 20 ? " underdog-win" : "";
    return `
    <div class="history-row pvp-history-row${underdog}">
      <div class="info">
        <div class="line1">${r.winner_name} забрал ${pluralCards(r.total_cards)} с ${r.winner_win_pct != null ? r.winner_win_pct : "?"}%</div>
        <div class="line2">Игроки: ${r.total_players} | ${fmtHistoryDate(r.resolved_at)}</div>
      </div>
    </div>
  `;
  }).join("");
}

// Anywhere another player's handle is shown publicly (history feeds, leaderboards,
// market/swap listings, PvP), the admin's own real @username is hidden behind a
// generic "Бот" label -- never applied to the player's OWN profile screen, where
// they see their own real username as usual.
function maskedName(u){
  return (u && u.toLowerCase() === "rzabeyda") ? "Бот" : u;
}
// Custom names (the "Имена" bank, and any card wearing one) are stored/matched in
// lowercase canonical form server-side, but always DISPLAYED with a capitalized first
// letter (durov -> Durov, pepe -> Pepe) -- never touches data-name attrs, which must
// stay the exact lowercase value the API expects.
function capName(n){
  return n ? n[0].toUpperCase() + n.slice(1) : n;
}
function renderRedBlackHistoryList(rounds){
  const list = document.getElementById("history-list");
  if(!rounds.length){
    list.innerHTML = `<div class="empty-state"><span class="emoji">🔴⚫</span>Раундов пока не было.</div>`;
    return;
  }
  list.innerHTML = rounds.map(r => {
    const name = r.username ? maskedName(r.username) : (r.first_name || "Игрок");
    const colorEmoji = r.result === "red" ? "🔴" : "⚫";
    const sign = r.won ? `+${r.payout - r.bet}` : `-${r.bet}`;
    return `
      <div class="history-row pvp-history-row">
        <div class="info">
          <div class="line1">${colorEmoji} ${name} — ${r.won ? "выиграл" : "проиграл"} ${sign} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></div>
          <div class="line2">ставка ${r.bet} · ${fmtHistoryDate(r.created_at)}</div>
        </div>
      </div>`;
  }).join("");
}

function renderMinesHistoryList(rounds){
  const list = document.getElementById("history-list");
  if(!rounds.length){
    list.innerHTML = `<div class="empty-state"><span class="emoji">💣</span>Раундов пока не было.</div>`;
    return;
  }
  list.innerHTML = rounds.map(r => {
    const name = r.username ? maskedName(r.username) : (r.first_name || "Игрок");
    const won = r.status === "won";
    const sign = won ? `+${r.payout - r.bet}` : `-${r.bet}`;
    const multLabel = won ? ` (x${r.cashout_multiplier.toFixed(2)})` : "";
    return `
      <div class="history-row pvp-history-row">
        <div class="info">
          <div class="line1">💣 ${name} (${r.mine_count} мин) — ${won ? "забрал" : "подорвался"} ${sign}${multLabel} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></div>
          <div class="line2">ставка ${r.bet} · ${fmtHistoryDate(r.created_at)}</div>
        </div>
      </div>`;
  }).join("");
}

function renderPokerHistoryList(rounds){
  const list = document.getElementById("history-list");
  if(!rounds.length){
    list.innerHTML = `<div class="empty-state"><span class="emoji">🃏</span>Раздач пока не было.</div>`;
    return;
  }
  list.innerHTML = rounds.map(r => {
    const name = r.username ? maskedName(r.username) : (r.first_name || "Игрок");
    const label = POKER_CATEGORY_LABEL[r.category] || r.category || "Пусто";
    const net = r.current_payout - r.bet;
    const sign = net >= 0 ? `+${net}` : `${net}`;
    const won = net >= 0 && r.status !== "busted";
    return `
      <div class="history-row pvp-history-row">
        <div class="info">
          <div class="line1">🃏 ${name} — ${label}${r.status === "busted" ? " (сгорело)" : ""} ${sign} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></div>
          <div class="line2">ставка ${r.bet} · ${fmtHistoryDate(r.created_at)}</div>
        </div>
      </div>`;
  }).join("");
}

function renderAviatorHistoryList(rounds){
  const list = document.getElementById("history-list");
  if(!rounds.length){
    list.innerHTML = `<div class="empty-state"><span class="emoji">🚀</span>Раундов пока не было.</div>`;
    return;
  }
  list.innerHTML = rounds.map(r => {
    const name = r.username ? maskedName(r.username) : (r.first_name || "Игрок");
    const won = r.status === "won";
    const mult = won ? r.cashout_multiplier : r.crash_point;
    const payout = won ? Math.round(r.bet * r.cashout_multiplier) : 0;
    const sign = won ? `+${payout - r.bet}` : `-${r.bet}`;
    return `
      <div class="history-row pvp-history-row">
        <div class="info">
          <div class="line1">🚀 ${name} — ${won ? "забрал" : "улетела"} на ${mult.toFixed(2)}x (${sign} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">)</div>
          <div class="line2">ставка ${r.bet} · ${fmtHistoryDate(r.created_at)}</div>
        </div>
      </div>`;
  }).join("");
}

document.getElementById("market-history-btn").addEventListener("click", ()=>openHistory());
document.getElementById("pvp-history-btn").addEventListener("click", ()=>openHistory("pvp"));
document.getElementById("rb-history-btn").addEventListener("click", ()=>openHistory("redblack"));
document.getElementById("poker-history-btn").addEventListener("click", ()=>openHistory("poker"));
document.getElementById("av-history-btn").addEventListener("click", ()=>openHistory("aviator"));
document.getElementById("history-close").addEventListener("click", ()=>{
  document.getElementById("history-overlay").classList.remove("active");
});

document.getElementById("pvp-rules-btn").addEventListener("click", ()=>{
  document.getElementById("pvp-rules-overlay").classList.add("active");
});
document.getElementById("pvp-rules-close").addEventListener("click", ()=>{
  document.getElementById("pvp-rules-overlay").classList.remove("active");
});
document.getElementById("av-rules-btn").addEventListener("click", ()=>{
  document.getElementById("av-rules-overlay").classList.add("active");
});
document.getElementById("av-info-btn").addEventListener("click", ()=>{
  document.getElementById("av-rules-overlay").classList.add("active");
});
document.getElementById("av-rules-close").addEventListener("click", ()=>{
  document.getElementById("av-rules-overlay").classList.remove("active");
});
document.getElementById("rb-rules-btn").addEventListener("click", ()=>{
  document.getElementById("rb-rules-overlay").classList.add("active");
});
document.getElementById("rb-info-btn").addEventListener("click", ()=>{
  document.getElementById("rb-rules-overlay").classList.add("active");
});
document.getElementById("rb-rules-close").addEventListener("click", ()=>{
  document.getElementById("rb-rules-overlay").classList.remove("active");
});
document.getElementById("mines-rules-btn").addEventListener("click", ()=>{
  document.getElementById("mines-rules-overlay").classList.add("active");
});
document.getElementById("mines-info-btn").addEventListener("click", ()=>{
  document.getElementById("mines-rules-overlay").classList.add("active");
});
document.getElementById("mines-rules-close").addEventListener("click", ()=>{
  document.getElementById("mines-rules-overlay").classList.remove("active");
});
document.getElementById("poker-rules-btn").addEventListener("click", ()=>{
  document.getElementById("poker-rules-card").classList.remove("compact");
  document.getElementById("poker-rules-title").textContent = "Правила Американского покера";
  document.getElementById("poker-rules-overlay").classList.add("active");
});
document.getElementById("poker-info-btn").addEventListener("click", ()=>{
  // Just the combinations/paytable -- none of the double-up-ladder rules text or diagram.
  document.getElementById("poker-rules-card").classList.add("compact");
  document.getElementById("poker-rules-title").textContent = "Комбинации";
  document.getElementById("poker-rules-overlay").classList.add("active");
});
document.getElementById("poker-rules-close").addEventListener("click", ()=>{
  document.getElementById("poker-rules-overlay").classList.remove("active");
});

const GAME_TOP_CONFIG = {
  redblack: { endpoint: "/redblack/leaderboard", empty: "Пока никто не сыграл ни одного раунда.",  empty_emoji: "🔴⚫",
              title: "🏆 Топ 10 Red&Black", sub: "Больше всего выигранных гемов" },
  aviator:  { endpoint: "/aviator/leaderboard",  empty: "Пока никто не сыграл ни одного раунда.",  empty_emoji: "🚀",
              title: "🏆 Топ 10 Ракетки", sub: "Больше всего выигранных гемов" },
  poker:    { endpoint: "/poker/leaderboard",    empty: "Пока никто не сыграл ни одной раздачи.",  empty_emoji: "🃏",
              title: "🏆 Топ 10 Покера", sub: "Больше всего выигранных гемов" },
  mines:    { endpoint: "/mines/leaderboard",    empty: "Пока никто не сыграл ни одного раунда.",  empty_emoji: "💣",
              title: "🏆 Топ 10 Минных полей", sub: "Больше всего выигранных гемов" },
};

const PVP_TOP_SUBMODES = {
  wins:    { endpoint: "/pvp/leaderboard",         valueKey: "wins",              suffix: " 🏆", signed: false,
             title: "🏆 Топ 10 по победам в PvP", sub: "Больше всего выигранных раундов",
             empty: "Пока никто не выиграл ни одного раунда.", empty_emoji: "⚔️" },
  cards:   { endpoint: "/pvp/leaderboard/cards",   valueKey: "cards_won",         suffix: " карт", signed: true,
             title: "🏆 Топ 10 по картам в PvP", sub: "Кто больше всего в плюсе по картам (забрано минус проиграно)",
             empty: "Пока никто не в плюсе по картам.", empty_emoji: "🃏" },
  diamond: { endpoint: "/pvp/leaderboard/diamond", valueKey: "diamond_cards_won", suffix: ' <img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">', signed: true,
             title: "🏆 Топ 10 по Diamond-картам в PvP", sub: "Кто больше всего в плюсе по diamond-картам (забрано минус проиграно)",
             empty: "Пока никто не в плюсе по diamond-картам.", empty_emoji: '<img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">' },
};
let pvpTopSubmode = "wins";

async function renderPvpTopRows(cfg, mode){
  document.getElementById("pvp-top-title").textContent = cfg.title;
  document.getElementById("pvp-top-sub").textContent = cfg.sub;
  const rowsEl = document.getElementById("pvp-top-rows");
  rowsEl.innerHTML = `<div class="empty-state"><span class="emoji">⏳</span>Загрузка...</div>`;
  try{
    const res = await api(cfg.endpoint);
    const rows = res.leaderboard || [];
    if(!rows.length){
      rowsEl.innerHTML = `<div class="empty-state"><span class="emoji">${cfg.empty_emoji}</span>${cfg.empty}</div>`;
      return;
    }
    const medals = ["🥇","🥈","🥉"];
    rowsEl.innerHTML = rows.map((r, i)=>{
      const name = r.username ? maskedName(r.username) : (r.first_name || `Игрок ${r.telegram_id}`);
      const rank = medals[i] || `${i + 1}.`;
      const valueLabel = mode === "pvp"
        ? `${cfg.signed && r[cfg.valueKey] >= 0 ? "+" : ""}${r[cfg.valueKey]}${cfg.suffix}`
        : `${r.net_profit >= 0 ? "+" : ""}${r.net_profit} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">`;
      return `<div class="pvp-top-row"><span class="rank">${rank}</span><span class="name">${name}</span><span class="wins">${valueLabel}</span></div>`;
    }).join("");
  }catch(e){
    rowsEl.innerHTML = `<div class="empty-state"><span class="emoji">⚠️</span>Не удалось загрузить.</div>`;
  }
}

async function openGameTop(mode){
  const submodeSwitch = document.getElementById("pvp-top-submode-switch");
  document.getElementById("pvp-top-overlay").classList.add("active");
  if(mode === "pvp"){
    submodeSwitch.style.display = "flex";
    pvpTopSubmode = "wins";
    submodeSwitch.querySelectorAll(".pvp-top-submode-btn").forEach(b=>b.classList.toggle("active", b.dataset.submode === "wins"));
    await renderPvpTopRows(PVP_TOP_SUBMODES.wins, "pvp");
  }else{
    submodeSwitch.style.display = "none";
    await renderPvpTopRows(GAME_TOP_CONFIG[mode], mode);
  }
}
document.getElementById("pvp-top-submode-switch").addEventListener("click", (e)=>{
  const btn = e.target.closest(".pvp-top-submode-btn");
  if(!btn) return;
  const sm = btn.dataset.submode;
  if(sm === pvpTopSubmode) return;
  pvpTopSubmode = sm;
  document.querySelectorAll("#pvp-top-submode-switch .pvp-top-submode-btn").forEach(b=>b.classList.toggle("active", b.dataset.submode === sm));
  renderPvpTopRows(PVP_TOP_SUBMODES[sm], "pvp");
});
document.getElementById("pvp-top-btn").addEventListener("click", ()=>openGameTop("pvp"));
document.getElementById("rb-top-btn").addEventListener("click", ()=>openGameTop("redblack"));
document.getElementById("av-top-btn").addEventListener("click", ()=>openGameTop("aviator"));
document.getElementById("poker-top-btn").addEventListener("click", ()=>openGameTop("poker"));
document.getElementById("mines-top-btn").addEventListener("click", ()=>openGameTop("mines"));
document.getElementById("pvp-top-close").addEventListener("click", ()=>{
  document.getElementById("pvp-top-overlay").classList.remove("active");
});
document.getElementById("pvp-top-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "pvp-top-overlay"){
    document.getElementById("pvp-top-overlay").classList.remove("active");
  }
});

document.querySelectorAll(".mode-switch-btn").forEach(btn=>{
  btn.addEventListener("click", ()=>{
    if(btn.dataset.mode === marketMode) return;
    setMarketMode(btn.dataset.mode);
    refreshMarketScreen();
  });
});

async function loadSwapListings(){
  try{
    const data = await api("/swap/listings");
    swapListings = data.listings;
    renderSwapListings();
  }catch(e){
    toast("Не удалось загрузить обмен: " + e.message);
  }
}

function renderSwapListings(){
  const list = document.getElementById("market-list");
  if(swapListings.length === 0){
    list.className = "";
    list.innerHTML = `<div class="empty-state"><span class="emoji">🔄</span>Пока никто не выставил карты на обмен.<br>Выстави свою в профиле!</div>`;
    return;
  }

  list.className = "grid";
  list.innerHTML = swapListings.map(l => `
      <div class="grid-item rarity-border-${l.rarity}${l.custom_name ? " obsidian-border" : ""}" data-uc="${l.user_card_id}">
        <img src="/static/cards/${l.filename}?v=${CARD_IMG_VERSION}" alt="${l.name||''}">
        <div class="rarity-tag ${l.custom_name ? "obsidian" : l.rarity}">${l.custom_name ? "OBSIDIAN" : l.rarity}</div>
        <div class="drop-number">#${l.drop_number}</div>
        ${l.is_mine ? `<div class="market-mine-badge">Твой</div>` : ""}
      </div>
    `).join("");

  list.querySelectorAll(".grid-item").forEach(el=>{
    el.addEventListener("click", ()=>openMarketItem(parseInt(el.dataset.uc), "swap"));
  });
}

// ---------- Market item quick-action popup (shared by both Торговля and Обмен grids) ----------
function findListing(ucId, mode){
  const arr = mode === "swap" ? swapListings : marketListings;
  return arr.find(l => l.user_card_id === ucId);
}

function closeMarketItem(){
  document.getElementById("market-item-overlay").classList.remove("active");
}

let marketItemUcId = null;
let marketItemMode = "trade";
function openMarketItem(ucId, mode){
  const item = findListing(ucId, mode);
  if(!item) return;
  marketItemUcId = ucId;
  marketItemMode = mode;
  const marketItemImgEl = document.getElementById("market-item-img");
  marketItemImgEl.src = `/static/cards/${item.filename}?v=${CARD_IMG_VERSION}`;
  setRarityBorder(marketItemImgEl, item.rarity);
  marketItemImgEl.classList.toggle("obsidian-border", !!item.custom_name);
  document.getElementById("market-item-name").textContent = (capName(item.name) || "Без названия") + (item.drop_number != null ? ` #${item.drop_number}` : "");
  const priceRow = document.getElementById("market-item-price-row");
  const actions = document.getElementById("market-item-actions");

  if(mode === "trade"){
    priceRow.style.display = "flex";
    priceRow.innerHTML = `<img src="/static/icons/diamond.png" alt="">${item.listed_price}`;
    if(item.is_mine){
      actions.innerHTML = `<button class="btn-unlist" id="mi-unlist">Снять с продажи</button>`;
      document.getElementById("mi-unlist").addEventListener("click", async ()=>{
        try{
          await api("/market/unlist", { user_card_id: ucId });
          toast("Снято с продажи");
          closeMarketItem();
          loadMarket();
        }catch(e){ toast("Не получилось: " + e.message); }
      });
    }else{
      actions.innerHTML = `<button class="btn-buy" id="mi-buy">Купить</button><button class="btn-offer" id="mi-offer">Оценить</button>`;
      document.getElementById("mi-buy").addEventListener("click", async ()=>{
        try{
          await api("/market/buy", { user_card_id: ucId });
          toast("Куплено! Смотри в профиле.");
          closeMarketItem();
          loadMarket();
          refreshGems();
        }catch(e){ toast("Не получилось купить: " + e.message); }
      });
      document.getElementById("mi-offer").addEventListener("click", async ()=>{
        const minPrice = minListingPrice(item.rarity);
        const price = await promptNumber(`Сколько гемов предложить (минимум ${minPrice})`);
        if(!price) return;
        if(price < minPrice){
          toast(`Минимальная цена предложения ${minPrice} гемов`);
          return;
        }
        try{
          await api("/market/offer", { user_card_id: ucId, price_gems: price });
          toast("Предложение отправлено продавцу");
          closeMarketItem();
        }catch(e){
          if(String(e.message).includes("minimum price")){
            toast(`Минимальная цена предложения ${minPrice} гемов`);
          }else{
            toast("Не получилось: " + e.message);
          }
        }
      });
    }
  }else{
    priceRow.style.display = "none";
    priceRow.innerHTML = "";
    if(item.is_mine){
      actions.innerHTML = `<button class="btn-unlist" id="mi-unlist-swap">Снять с обмена</button>`;
      document.getElementById("mi-unlist-swap").addEventListener("click", async ()=>{
        try{
          await api("/swap/unlist", { user_card_id: ucId });
          toast("Снято с обмена");
          closeMarketItem();
          loadSwapListings();
        }catch(e){ toast("Не получилось: " + e.message); }
      });
    }else{
      actions.innerHTML = `<button class="btn-propose" id="mi-propose">Предложить обмен</button>`;
      document.getElementById("mi-propose").addEventListener("click", ()=>{
        closeMarketItem();
        openSwapPicker(ucId);
      });
    }
  }

  document.getElementById("market-item-overlay").classList.add("active");
}

// Swipe left/right on the card to browse the rest of the current listings —
// same pattern as the profile collection modal.
let marketItemTouchStartX = 0, marketItemTouchStartY = 0;
const marketItemCardEl = document.getElementById("market-item-card");
marketItemCardEl.addEventListener("touchstart", (e)=>{
  marketItemTouchStartX = e.touches[0].clientX;
  marketItemTouchStartY = e.touches[0].clientY;
}, { passive: true });
marketItemCardEl.addEventListener("touchend", (e)=>{
  const arr = marketItemMode === "swap" ? swapListings : marketListings;
  if(arr.length <= 1 || marketItemUcId === null) return;
  const dx = e.changedTouches[0].clientX - marketItemTouchStartX;
  const dy = e.changedTouches[0].clientY - marketItemTouchStartY;
  if(Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)){
    const idx = arr.findIndex(l => l.user_card_id === marketItemUcId);
    if(idx === -1) return;
    const nextIdx = dx < 0 ? (idx + 1) % arr.length : (idx - 1 + arr.length) % arr.length;
    openMarketItem(arr[nextIdx].user_card_id, marketItemMode);
  }
}, { passive: true });

document.getElementById("market-item-close").addEventListener("click", closeMarketItem);
document.getElementById("market-item-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "market-item-overlay") closeMarketItem();
});

// ---------- Swap picker (choose which of your own cards to offer) ----------
let swapPickerTargetId = null;
let swapPickerSelected = new Set();

function openSwapPicker(targetUserCardId){
  swapPickerTargetId = targetUserCardId;
  swapPickerSelected = new Set();
  renderSwapPickerGrid();
  document.getElementById("swap-picker-overlay").classList.add("active");
}

function renderSwapPickerGrid(){
  const grid = document.getElementById("swap-picker-grid");
  if(inventory.length === 0){
    grid.innerHTML = `<div class="empty-state">Нечего предложить — иди фармить 🌱</div>`;
    return;
  }
  const swapAvailable = inventory.filter(c => !c.pvp_round_id && !c.listed_price && !c.swap_listed && !c.staked_at && !c.in_giveaway);
  if(swapAvailable.length === 0){
    grid.innerHTML = `<div class="empty-state">Нет свободных карт — сними с продажи/обмена/PvP-банка или иди фармить 🌱</div>`;
    return;
  }
  grid.innerHTML = swapAvailable.map(c => `
    <div class="grid-item picker-item rarity-border-${c.rarity} ${swapPickerSelected.has(c.user_card_id) ? 'selected' : ''}" data-uc="${c.user_card_id}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}">
      <div class="rarity-tag ${c.rarity}">${c.rarity}</div>
      <div class="check">✓</div>
    </div>
  `).join("");
  grid.querySelectorAll(".picker-item").forEach(el=>{
    el.addEventListener("click", ()=>{
      const ucId = parseInt(el.dataset.uc);
      if(swapPickerSelected.has(ucId)) swapPickerSelected.delete(ucId);
      else swapPickerSelected.add(ucId);
      el.classList.toggle("selected");
    });
  });
}

document.getElementById("swap-picker-cancel").addEventListener("click", ()=>{
  document.getElementById("swap-picker-overlay").classList.remove("active");
});

document.getElementById("swap-picker-confirm").addEventListener("click", async ()=>{
  if(swapPickerSelected.size === 0){ toast("Выбери хотя бы одну карту"); return; }
  try{
    await api("/swap/offer", { user_card_id: swapPickerTargetId, offered_user_card_ids: [...swapPickerSelected] });
    toast("Предложение отправлено владельцу");
    document.getElementById("swap-picker-overlay").classList.remove("active");
  }catch(e){
    toast("Не получилось: " + e.message);
  }
});

// ---------- PvP (shared jackpot — stake any number of cards, one winner takes the pot) ----------
let pvpState = null;
let pvpPollTimer = null;
let myTelegramId = null; // set once we know it, from /auth — used to mark "(ты)" in the list
// NOTE: whether this player has already seen a given round's reveal is tracked
// server-side (pvp_last_seen_round_id on the user) via unseen_result, NOT in
// localStorage — Telegram WebApp has no localStorage, and the client re-runs from
// scratch on every open anyway, so a local JS variable can't remember this either.

const PVP_PALETTE = ["#ff2fb0","#facc15","#4ade80","#f87171","#38bdf8","#fb923c","#f472b6","#a3e635"];

// Turns a participants list (each with chance_pct) into conic-gradient stops plus
// per-participant angle info (start/end/mid, clockwise degrees from 12 o'clock) —
// shared by the live lobby wheel and the spin-to-a-winner reveal wheel.
function pvpBuildWheel(participants){
  if(!participants || !participants.length){
    return { gradient: "#2a2a2a 0deg 360deg", segments: [] };
  }
  let acc = 0;
  const stops = [];
  const segments = [];
  participants.forEach((p, i)=>{
    const color = PVP_PALETTE[i % PVP_PALETTE.length];
    const size = Math.max(0, (p.chance_pct / 100) * 360);
    const start = acc;
    let end = acc + size;
    if(i === participants.length - 1) end = 360; // close any rounding gap
    stops.push(`${color} ${start}deg ${end}deg`);
    segments.push({ ...p, color, start, end, mid: (start + end) / 2 });
    acc = end;
  });
  return { gradient: stops.join(", "), segments };
}

function startPvpPolling(){
  stopPvpPolling();
  pvpPollTimer = setInterval(loadPvpState, 3000);
}
function stopPvpPolling(){
  if(pvpPollTimer){ clearInterval(pvpPollTimer); pvpPollTimer = null; }
}

async function loadPvpState(){
  try{
    pvpState = await api("/pvp/state");
    renderPvp();
    startPvpPolling();
  }catch(e){
    toast("Не удалось загрузить PvP: " + e.message);
  }
}

// ---------- PvP sub-tab switch (cards / Red&Black / [Ракетка later]) ----------
let pvpMode = "cards";
let pendingPvpReveal = null; // an unseen_result that arrived while the player was on a different sub-tab
// True for exactly the FIRST renderPvp() after switching onto the PvP screen -- that
// first /pvp/state fetch can carry an unseen_result left over from a round that
// concluded while the player was elsewhere, and they explicitly don't want a replay of
// something already finished just from walking in. Reset right after that one render,
// so a round that concludes while they're actually sitting here watching (a later poll,
// still on this same screen visit) still gets the live reveal as normal.
let pvpTabJustOpened = false;
document.getElementById("pvp-mode-switch").addEventListener("click", (e)=>{
  const btn = e.target.closest(".mode-switch-btn");
  if(!btn) return;
  if(pvpMode === "poker" && btn.dataset.mode !== "poker") collectPokerWinnings();
  pvpMode = btn.dataset.mode;
  document.querySelectorAll("#pvp-mode-switch .mode-switch-btn").forEach(b=>b.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById("pvp-cards-block").style.display = pvpMode === "cards" ? "" : "none";
  document.getElementById("pvp-aviator-block").style.display = pvpMode === "aviator" ? "" : "none";
  document.getElementById("pvp-redblack-block").style.display = pvpMode === "redblack" ? "" : "none";
  document.getElementById("pvp-mines-block").style.display = pvpMode === "mines" ? "" : "none";
  document.getElementById("pvp-poker-block").style.display = pvpMode === "poker" ? "" : "none";
  if(pvpMode === "mines"){ resumeMinesRound(); syncMinesActiveRound(); }
  if(pvpMode === "cards" && pendingPvpReveal){
    openPvpReveal(pendingPvpReveal);
    pendingPvpReveal = null;
  }
});

// ---------- Red&Black (in-app, fully independent from the chat /redblack game) ----------
const REDBLACK_MIN_BET = 25; // mirrors database.py's REDBLACK_MIN_BET -- client-side pre-check only, server enforces for real
let rbBet = 25;
let rbBusy = false;
let rbHistoryLog = [];

function renderRedBlackBet(){
  document.getElementById("rb-bet-value").textContent = rbBet;
  document.querySelectorAll("#rb-presets button").forEach(b=>{
    b.classList.toggle("active", parseInt(b.dataset.bet, 10) === rbBet);
  });
}

document.getElementById("rb-presets").addEventListener("click", (e)=>{
  const btn = e.target.closest("button[data-bet]");
  if(!btn) return;
  rbBet = parseInt(btn.dataset.bet, 10);
  renderRedBlackBet();
});

document.getElementById("rb-bet-edit").addEventListener("click", async ()=>{
  const val = await promptNumber("Ставка (гемов, минимум " + REDBLACK_MIN_BET + ")");
  if(val && val >= REDBLACK_MIN_BET){
    rbBet = val;
    renderRedBlackBet();
  }else if(val){
    toast("Минимальная ставка " + REDBLACK_MIN_BET + " гемов");
  }
});

function prependRbHistory(res){
  rbHistoryLog.unshift(res);
  rbHistoryLog = rbHistoryLog.slice(0, 12);
  document.getElementById("rb-history").innerHTML = rbHistoryLog.map(r=>{
    const emoji = r.result === "red" ? "🔴" : "⚫";
    const sign = r.won ? ("+" + (r.payout - r.bet)) : ("-" + r.bet);
    return `<div class="rb-history-row ${r.won ? "win" : "lose"}">${emoji} ${sign}</div>`;
  }).join("");
}

async function playRedBlack(choice){
  if(rbBusy) return;
  if(rbBet < REDBLACK_MIN_BET){ toast("Минимальная ставка " + REDBLACK_MIN_BET + " гемов"); return; }
  if(rbBet > gemsBalance && !isAdminUser){ toast("Не хватает гемов"); return; }
  rbBusy = true;
  const btnRed = document.getElementById("rb-pick-red");
  const btnBlack = document.getElementById("rb-pick-black");
  btnRed.disabled = true; btnBlack.disabled = true;
  document.getElementById("rb-share-btn").style.display = "none";
  try{
    const res = await api("/redblack/play", { bet: rbBet, choice });
    setGemsDisplay(res.gems);
    const resultEl = document.getElementById("rb-result");
    const colorName = res.result === "red" ? "красное" : "чёрное";
    const colorEmoji = res.result === "red" ? "🔴" : "⚫";
    if(res.won){
      resultEl.innerHTML = `<span class="rb-win">${colorEmoji} Выпало ${colorName} — выигрыш +${res.payout - res.bet} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></span>`;
      tg?.HapticFeedback?.notificationOccurred("success");
    }else{
      resultEl.innerHTML = `<span class="rb-lose">${colorEmoji} Выпало ${colorName} — проигрыш -${res.bet} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></span>`;
      tg?.HapticFeedback?.notificationOccurred("error");
    }
    resultEl.classList.remove("rb-pulse");
    void resultEl.offsetWidth;
    resultEl.classList.add("rb-pulse");
    prependRbHistory(res);
    const shareBtn = document.getElementById("rb-share-btn");
    shareBtn.style.display = "block";
    shareBtn.textContent = "Поделиться в чат";
    shareBtn.disabled = false;
    shareBtn.onclick = ()=>shareGameResult("redblack", res.round_id, shareBtn);
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    btnRed.disabled = false; btnBlack.disabled = false;
    rbBusy = false;
  }
}
document.getElementById("rb-pick-red").addEventListener("click", ()=>playRedBlack("red"));
document.getElementById("rb-pick-black").addEventListener("click", ()=>playRedBlack("black"));

// ---------- Minefields (Минные поля) ----------
const MINES_MIN_BET = 25; // mirrors database.py's MINES_MIN_BET
const MINES_GRID_TILES = 25; // mirrors database.py's MINES_GRID_TILES
let minesBet = 25;
let minesCount = 3;
let minesRoundId = null;
let minesRevealed = [];
let minesMultiplier = 1.0;
let minesBusy = false;

function renderMinesBetUI(){
  // NOTE: deliberately never sets .disabled on these controls -- a disabled button
  // swallows clicks with zero feedback (no toast, nothing), which read as "broken"
  // when someone taps Изменить mid-round. Each handler below checks minesRoundId
  // itself and explains why with a toast instead.
  document.getElementById("mines-bet-value").textContent = minesBet;
  document.getElementById("mines-start-bet").textContent = minesBet;
  document.querySelectorAll("#mines-bet-presets button").forEach(b=>{
    b.classList.toggle("active", parseInt(b.dataset.bet, 10) === minesBet);
  });
  document.querySelectorAll("#mines-count-row button").forEach(b=>{
    b.classList.toggle("active", parseInt(b.dataset.mines, 10) === minesCount);
  });
}

function renderMinesStats(){
  const stats = document.getElementById("mines-stats");
  if(minesRoundId === null || !minesRevealed.length){
    stats.style.display = "none";
    return;
  }
  stats.style.display = "flex";
  document.getElementById("mines-mult").textContent = "x" + minesMultiplier.toFixed(2);
  document.getElementById("mines-potential").textContent = `${Math.round(minesBet * minesMultiplier)} гемов при выводе сейчас`;
}

function renderMinesGrid(minePositions, hitTile){
  const grid = document.getElementById("mines-grid");
  const resolved = minePositions !== undefined;
  let html = "";
  for(let i = 0; i < MINES_GRID_TILES; i++){
    const isSafe = minesRevealed.includes(i);
    const isMine = resolved && minePositions.includes(i);
    let cls = "mines-tile";
    let label = "";
    if(isSafe){ cls += " safe"; label = "💎"; }
    else if(isMine){ cls += (i === hitTile ? " mine" : " mine-dim"); label = "💣"; }
    if(resolved || isSafe) cls += " disabled";
    html += `<div class="${cls}" data-tile="${i}">${label}</div>`;
  }
  grid.innerHTML = html;
  if(!resolved){
    grid.querySelectorAll(".mines-tile:not(.disabled)").forEach(el=>{
      el.addEventListener("click", ()=>revealMinesTile(parseInt(el.dataset.tile, 10)));
    });
  }
}

async function syncMinesActiveRound(){
  // Recovers a board orphaned by a crash/reload/backgrounded app -- see
  // get_active_mines_round_for_user() on the server. Previously minesRoundId lived
  // ONLY in memory, so losing it client-side left the server thinking a board was
  // still open forever, silently blocking every future "Начать" with
  // "already have an active board" and no way out short of an admin DB fix. Called
  // once every time the Mines tab is opened; a no-op if we're already tracking a
  // round in memory (normal case, keeps this from ever fighting a live round).
  if(minesRoundId !== null || minesBusy) return;
  try{
    const active = await api("/mines/my_active", {});
    if(active){
      minesRoundId = active.round_id;
      minesBet = active.bet;
      minesCount = active.mine_count;
      minesRevealed = active.revealed;
      minesMultiplier = active.multiplier;
      renderMinesGrid();
      resumeMinesRound();
    }
  }catch(e){
    // best-effort background recovery check -- never surface an error toast for it
  }
}

function resumeMinesRound(){
  // Called on every switch INTO the Mines tab -- state lives in memory (kept in sync
  // by every reveal/cashout response), no need to hit the server just to redraw.
  renderMinesBetUI();
  renderMinesStats();
  document.getElementById("mines-start-btn").style.display = minesRoundId === null ? "block" : "none";
  document.getElementById("mines-cashout-btn").style.display = (minesRoundId !== null && minesRevealed.length) ? "block" : "none";
  if(minesRoundId === null && !document.getElementById("mines-grid").children.length){
    renderMinesGrid();
  }
}

document.getElementById("mines-bet-presets").addEventListener("click", (e)=>{
  const btn = e.target.closest("button[data-bet]");
  if(!btn) return;
  if(minesRoundId !== null){ toast("Сначала заверши текущий раунд"); return; }
  minesBet = parseInt(btn.dataset.bet, 10);
  renderMinesBetUI();
});
document.getElementById("mines-bet-edit").addEventListener("click", async ()=>{
  if(minesRoundId !== null){ toast("Сначала заверши текущий раунд"); return; }
  const val = await promptNumber("Ставка (гемов, минимум " + MINES_MIN_BET + ")");
  if(val && val >= MINES_MIN_BET){
    minesBet = val;
    renderMinesBetUI();
  }else if(val){
    toast("Минимальная ставка " + MINES_MIN_BET + " гемов");
  }
});
document.getElementById("mines-count-row").addEventListener("click", (e)=>{
  const btn = e.target.closest("button[data-mines]");
  if(!btn) return;
  if(minesRoundId !== null){ toast("Сначала заверши текущий раунд"); return; }
  minesCount = parseInt(btn.dataset.mines, 10);
  renderMinesBetUI();
});

async function startMinesRound(){
  if(minesBusy || minesRoundId !== null) return;
  if(minesBet < MINES_MIN_BET){ toast("Минимальная ставка " + MINES_MIN_BET + " гемов"); return; }
  if(minesBet > gemsBalance && !isAdminUser){ toast("Не хватает гемов"); return; }
  minesBusy = true;
  document.getElementById("mines-start-btn").disabled = true;
  document.getElementById("mines-share-btn").style.display = "none";
  document.getElementById("mines-result").innerHTML = "";
  try{
    const res = await api("/mines/start", { bet: minesBet, mine_count: minesCount });
    minesRoundId = res.round_id;
    minesRevealed = [];
    minesMultiplier = 1.0;
    setGemsDisplay(gemsBalance - minesBet);
    renderMinesGrid();
    resumeMinesRound();
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    minesBusy = false;
    document.getElementById("mines-start-btn").disabled = false;
  }
}
document.getElementById("mines-start-btn").addEventListener("click", startMinesRound);

function finishMinesRound(res, wasCashout){
  minesRoundId = null;
  const resultEl = document.getElementById("mines-result");
  if(res.status === "won"){
    const profit = res.payout - minesBet;
    resultEl.innerHTML = `<span class="rb-win">💎 ${wasCashout ? "Забрал" : "Поле зачищено"} — выигрыш +${profit} на x${res.multiplier.toFixed(2)} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></span>`;
    tg?.HapticFeedback?.notificationOccurred("success");
    setGemsDisplay(res.gems);
    const shareBtn = document.getElementById("mines-share-btn");
    shareBtn.style.display = "block";
    shareBtn.textContent = "Поделиться в чат";
    shareBtn.disabled = false;
    shareBtn.onclick = ()=>shareGameResult("mines", res.round_id, shareBtn);
  }else{
    resultEl.innerHTML = `<span class="rb-lose">💣 Подорвался — проигрыш -${minesBet} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></span>`;
    tg?.HapticFeedback?.notificationOccurred("error");
  }
  resultEl.classList.remove("rb-pulse");
  void resultEl.offsetWidth;
  resultEl.classList.add("rb-pulse");
  renderMinesGrid(res.mine_positions, res.tile);
  minesRevealed = res.revealed || minesRevealed;
  resumeMinesRound();
}

async function revealMinesTile(tile){
  if(minesBusy || minesRoundId === null) return;
  minesBusy = true;
  try{
    const res = await api("/mines/reveal", { round_id: minesRoundId, tile });
    if(res.status === "lost"){
      finishMinesRound(res, false);
    }else if(res.status === "won"){
      finishMinesRound(res, false);
    }else{
      minesRevealed = res.revealed;
      minesMultiplier = res.multiplier;
      renderMinesGrid();
      resumeMinesRound();
    }
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    minesBusy = false;
  }
}

async function cashoutMinesRound(){
  if(minesBusy || minesRoundId === null || !minesRevealed.length) return;
  minesBusy = true;
  document.getElementById("mines-cashout-btn").disabled = true;
  try{
    const res = await api("/mines/cashout", { round_id: minesRoundId });
    finishMinesRound(res, true);
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    minesBusy = false;
    document.getElementById("mines-cashout-btn").disabled = false;
  }
}
document.getElementById("mines-cashout-btn").addEventListener("click", cashoutMinesRound);
document.getElementById("mines-history-btn").addEventListener("click", ()=>openHistory("mines"));

// ---------- American Poker '90s (in-app, server-authoritative 5-card draw + double-up) ----------
const POKER_MIN_BET = 1; // mirrors database.py's POKER_MIN_BET -- client-side pre-check only, server enforces for real
const POKER_JOKER = "JK"; // mirrors database.py's POKER_JOKER
const POKER_SUIT_SYMBOL = { S: "♠", H: "♥", D: "♦", C: "♣" };
const POKER_RED_SUITS = new Set(["H", "D"]);
const POKER_RANK_LABEL = { T: "10" };
const POKER_RANK_VALUE = { 2:2,3:3,4:4,5:5,6:6,7:7,8:8,9:9,T:10,J:11,Q:12,K:13,A:14 };
const POKER_CATEGORY_LABEL = {
  royal_flush: "ROYAL FLUSH", five_of_a_kind: "5 OF A KIND", straight_flush: "STR FLUSH", four_kind: "4 OF A KIND",
  full_house: "FULL HOUSE", flush: "FLUSH", straight: "STRAIGHT",
  three_kind: "3 OF A KIND", two_pair: "2 PAIRS", jacks_or_better: "JACKS OR BETTER",
  nothing: "MISS",
};

let pokerBet = 25;
let pokerBusy = false;
let pokerRoundId = null;
let pokerCards = [];
let pokerHold = [false, false, false, false, false];
let pokerPhase = "idle"; // idle | dealt | drawn (won, gamble/collect available)
let pokerBaseWin = 0; // the payout at level 0 (right after Draw), before any doubling
let pokerGambleLevel = 0; // how many successful doublings so far (0..POKER_LADDER_LEVELS)
// Mirrors database.py's POKER_DOUBLE_MULTIPLIERS: 6 plain doublings off the base, then a
// bonus final rung that's NOT a fixed multiplier -- it roughly doubles whatever level 6
// paid and rounds UP to the nearest POKER_BONUS_ROUND_TO, see pokerBonusPayout() below.
const POKER_DOUBLE_MULTIPLIERS = [2, 4, 8, 16, 32, 64];
const POKER_BONUS_ROUND_TO = 5000;
const POKER_LADDER_LEVELS = POKER_DOUBLE_MULTIPLIERS.length + 1;
function pokerBonusPayout(level6Payout){
  return Math.ceil((level6Payout * 2) / POKER_BONUS_ROUND_TO) * POKER_BONUS_ROUND_TO;
}
function pokerLadderAmount(lvl){
  return lvl === POKER_LADDER_LEVELS
    ? pokerBonusPayout(pokerBaseWin * POKER_DOUBLE_MULTIPLIERS[POKER_DOUBLE_MULTIPLIERS.length - 1])
    : pokerBaseWin * POKER_DOUBLE_MULTIPLIERS[lvl - 1];
}

function renderPokerBet(){
  document.getElementById("poker-bet-value").textContent = pokerBet;
  document.querySelectorAll("#poker-presets button").forEach(b=>{
    b.classList.toggle("active", parseInt(b.dataset.bet, 10) === pokerBet);
  });
}
document.getElementById("poker-presets").addEventListener("click", (e)=>{
  if(pokerPhase !== "idle") return;
  const btn = e.target.closest("button[data-bet]");
  if(!btn) return;
  pokerBet = parseInt(btn.dataset.bet, 10);
  renderPokerBet();
});
document.getElementById("poker-bet-edit").addEventListener("click", async ()=>{
  if(pokerPhase !== "idle") return;
  const val = await promptNumber("Ставка (гемов, минимум " + POKER_MIN_BET + ")");
  if(val && val >= POKER_MIN_BET){
    pokerBet = val;
    renderPokerBet();
  }else if(val){
    toast("Минимальная ставка " + POKER_MIN_BET + " гемов");
  }
});

// Suggests which of the 5 dealt cards to hold, for convenience -- pre-checks a solid
// (not literally optimal, but close) standard video-poker strategy: any made paying
// hand keeps itself, otherwise chases the best draw (4-flush, 4-straight, 3-to-royal,
// suited/high cards). The player can still untoggle anything -- this only sets the
// starting selection, never locks it.
function pokerSuggestHold(cards){
  // The joker is always the best card in hand -- hold it, then chase whatever it's
  // wild for among the 4 real cards (a matching pair+ towards quads/five-of-a-kind,
  // else 3+ suited towards a flush, else lone high cards) rather than running the
  // full made-hand heuristic below, which assumes 5 real cards.
  const jokerIdx = cards.indexOf(POKER_JOKER);
  if(jokerIdx !== -1){
    const holds = [false, false, false, false, false];
    holds[jokerIdx] = true;
    const others = cards.map((c, i) => ({ i, rank: POKER_RANK_VALUE[c[0]], suit: c[1] })).filter(x => x.i !== jokerIdx);
    const rankCounts = {};
    others.forEach(o => { rankCounts[o.rank] = (rankCounts[o.rank] || 0) + 1; });
    const pairedRank = Object.keys(rankCounts).find(r => rankCounts[r] >= 2);
    if(pairedRank){
      others.forEach(o => { if(String(o.rank) === pairedRank) holds[o.i] = true; });
      return holds;
    }
    const suitCounts = {};
    others.forEach(o => { suitCounts[o.suit] = (suitCounts[o.suit] || 0) + 1; });
    const bestSuit = Object.keys(suitCounts).find(s => suitCounts[s] >= 3);
    if(bestSuit){
      others.forEach(o => { if(o.suit === bestSuit) holds[o.i] = true; });
      return holds;
    }
    // 4 unique ranks + the wild joker already complete a straight iff they fit inside
    // some 5-consecutive-rank window (span <= 4) -- covers a mid-gap fill (3,5,6,7 ->
    // joker=4) and a plain open-ended 4-run (4,5,6,7 -> joker=3 or 8) alike. Also check
    // the low-ace "wheel" (A,2,3,4 -> joker=5) by re-scoring the ace as 1.
    const otherRankSet = new Set(others.map(o => o.rank));
    if(otherRankSet.size === 4){
      const fitsWindow = (ranks) => {
        const sorted = [...ranks].sort((a, b) => a - b);
        return sorted[3] - sorted[0] <= 4;
      };
      const rankList = [...otherRankSet];
      const wheelList = rankList.map(r => r === 14 ? 1 : r);
      if(fitsWindow(rankList) || fitsWindow(wheelList)){
        others.forEach(o => { holds[o.i] = true; });
        return holds;
      }
    }
    // A straight draw doesn't need to use all 4 non-joker cards -- if dropping just
    // ONE of them leaves 3 real ranks the joker can bridge (span <= 4, same threshold
    // as the complete-straight check above), hold the joker + those 3 and discard the
    // odd one out, rather than falling through to a much weaker suited-pair chase.
    // e.g. 10,K,Joker,8,Q -> keep 10,Q,K + joker (fills J), discard the 8.
    let bestStraightDrawCombo = null;
    let bestStraightDrawSpan = Infinity;
    for(let skip = 0; skip < others.length; skip++){
      const combo = others.filter((_, idx) => idx !== skip);
      const comboRanks = [...new Set(combo.map(o => o.rank))];
      if(comboRanks.length !== 3) continue; // the dropped card must be what made a 4th distinct rank, not a duplicate
      const span = (ranks) => Math.max(...ranks) - Math.min(...ranks);
      const wheelRanks = comboRanks.map(r => r === 14 ? 1 : r);
      const s = Math.min(span(comboRanks), span(wheelRanks));
      if(s <= 4 && s < bestStraightDrawSpan){
        bestStraightDrawSpan = s;
        bestStraightDrawCombo = combo;
      }
    }
    if(bestStraightDrawCombo){
      bestStraightDrawCombo.forEach(o => { holds[o.i] = true; });
      return holds;
    }
    // No pair, no straight (made or draw), no 3+ suited -- a lone high card no longer
    // pays anything (see the paytable comment above draw_poker()), so holding one is
    // pointless now. Chase a flush instead if 2 of the 4 already share a suit (joker
    // makes 3, only 2 more draws needed); otherwise nothing here is worth keeping
    // beyond the joker.
    const suitGroups = {};
    others.forEach(o => { (suitGroups[o.suit] = suitGroups[o.suit] || []).push(o); });
    const bestSuitGroup = Object.values(suitGroups).sort((a, b) => b.length - a.length)[0];
    if(bestSuitGroup && bestSuitGroup.length >= 2){
      bestSuitGroup.forEach(o => { holds[o.i] = true; });
    }
    return holds;
  }
  const parsed = cards.map(c => ({ rank: POKER_RANK_VALUE[c[0]], suit: c[1] }));
  const rankCounts = {};
  parsed.forEach(c => { rankCounts[c.rank] = (rankCounts[c.rank] || 0) + 1; });
  const counts = Object.values(rankCounts).sort((a,b) => b - a);
  const uniqRanks = [...new Set(parsed.map(c => c.rank))].sort((a,b) => b - a);
  const suits = parsed.map(c => c.suit);
  const suitCounts = {};
  suits.forEach(s => { suitCounts[s] = (suitCounts[s] || 0) + 1; });
  const isFlush = new Set(suits).size === 1;
  let isStraight = false;
  if(uniqRanks.length === 5){
    if(uniqRanks[0] - uniqRanks[4] === 4) isStraight = true;
    else if(uniqRanks.join(",") === "14,5,4,3,2") isStraight = true;
  }
  if(counts[0] === 4){
    // Hold only the quad, not the kicker -- the deck's one Joker can still be drawn
    // into that discarded slot, upgrading four_kind to five_of_a_kind; keeping the
    // kicker forecloses that for zero benefit (the 5th card never affects the category).
    const quadRank = Number(Object.keys(rankCounts).find(r => rankCounts[r] === 4));
    return parsed.map(c => c.rank === quadRank);
  }
  if((isStraight && isFlush) || (counts[0] === 3 && counts[1] === 2) || isFlush || isStraight){
    return [true, true, true, true, true];
  }
  if(counts[0] === 3){
    const tripRank = Number(Object.keys(rankCounts).find(r => rankCounts[r] === 3));
    return parsed.map(c => c.rank === tripRank);
  }
  if(counts[0] === 2 && counts[1] === 2){
    const pairRanks = Object.keys(rankCounts).filter(r => rankCounts[r] === 2).map(Number);
    return parsed.map(c => pairRanks.includes(c.rank));
  }
  for(const s in suitCounts){
    if(suitCounts[s] === 4) return parsed.map(c => c.suit === s);
  }
  const findStraightDraw = (ranks) => {
    const sorted = [...ranks].sort((a, b) => a - b);
    let best = null;
    for(let i = 0; i + 3 < sorted.length; i++){
      const w = sorted.slice(i, i + 4);
      const span = w[3] - w[0];
      if(span <= 4 && (!best || span < (best[3] - best[0]))) best = w;
    }
    return best;
  };
  let straightDraw = findStraightDraw(uniqRanks);
  if(!straightDraw && uniqRanks.includes(14)){
    const wheelRanks = uniqRanks.map(r => r === 14 ? 1 : r);
    const wheelDraw = findStraightDraw(wheelRanks);
    if(wheelDraw) straightDraw = wheelDraw.map(r => r === 1 ? 14 : r);
  }
  if(straightDraw){
    const holds = [false, false, false, false, false];
    const usedIdx = new Set();
    for(const targetRank of straightDraw){
      const idx = parsed.findIndex((c, i) => c.rank === targetRank && !usedIdx.has(i));
      if(idx !== -1){ holds[idx] = true; usedIdx.add(idx); }
    }
    return holds;
  }
  // Only ever hold: a pair, two pair, trips, a made straight/flush (handled above),
  // a 4-card flush draw, or a 4-card straight draw (all handled above). A bare high
  // card or two suited high cards no longer pay anything (see paytable) and are
  // strictly worse equity than a fresh 5-card redraw, so nothing else gets held.
  if(counts[0] === 2){
    const pairRank = Number(Object.keys(rankCounts).find(r => rankCounts[r] === 2));
    return parsed.map(c => c.rank === pairRank);
  }
  return [false, false, false, false, false];
}

function renderPokerHand(clickable){
  const wrap = document.getElementById("poker-hand");
  if(!pokerCards.length){
    // idle placeholder: 5 face-down backs, so the card field is visible by default
    wrap.innerHTML = Array(5).fill(0).map(()=>`<div class="poker-card-slot"><div class="poker-playing-card back"></div><div class="poker-hold-label"></div></div>`).join("");
    return;
  }
  wrap.innerHTML = pokerCards.map((code, i)=>{
    const held = pokerHold[i];
    const holdLabel = `<div class="poker-hold-label">${held ? "HELD" : ""}</div>`;
    if(code === POKER_JOKER){
      return `<div class="poker-card-slot">
        <div class="poker-playing-card joker ${held ? "held" : ""}" data-idx="${i}">
          <div class="pc-center-suit">🃏</div>
        </div>
        ${holdLabel}
      </div>`;
    }
    const rank = code[0], suit = code[1];
    const label = POKER_RANK_LABEL[rank] || rank;
    const symbol = POKER_SUIT_SYMBOL[suit];
    const isRed = POKER_RED_SUITS.has(suit);
    // true black (not #141414) reads closer to a real printed card, red suits unaffected
    return `<div class="poker-card-slot">
      <div class="poker-playing-card ${isRed ? "red" : ""} ${held ? "held" : ""}" data-idx="${i}">
        <div class="poker-corner tl"><div class="pc-rank">${label}</div></div>
        <div class="pc-center-suit">${symbol}</div>
        <div class="poker-corner br"><div class="pc-rank">${label}</div></div>
      </div>
      ${holdLabel}
    </div>`;
  }).join("");
  if(clickable){
    wrap.querySelectorAll(".poker-playing-card").forEach(el=>{
      el.addEventListener("click", ()=>{
        if(pokerPhase !== "dealt" || pokerBusy) return;
        const i = parseInt(el.dataset.idx, 10);
        pokerHold[i] = !pokerHold[i];
        el.classList.toggle("held", pokerHold[i]);
        const labelEl = el.parentElement.querySelector(".poker-hold-label");
        if(labelEl) labelEl.textContent = pokerHold[i] ? "HELD" : "";
      });
    });
  }
}

function renderPokerDoubleLadder(){
  const ladder = document.getElementById("poker-double-ladder");
  let html = "";
  for(let lvl = POKER_LADDER_LEVELS; lvl >= 1; lvl--){
    const amount = pokerLadderAmount(lvl);
    let cls = "";
    if(lvl <= pokerGambleLevel) cls = "cleared";
    else if(lvl === pokerGambleLevel + 1) cls = "next-level";
    if(lvl === POKER_LADDER_LEVELS) cls += " bonus-level";
    html += `<div class="ladder-row lvl${lvl} ${cls}"><span class="mult">${amount}</span></div>`;
  }
  ladder.innerHTML = html;
  const currentAmount = pokerGambleLevel === 0 ? pokerBaseWin : pokerLadderAmount(pokerGambleLevel);
  document.getElementById("poker-double-current").textContent = currentAmount;
}

// Revealed cards accumulate left-to-right in pd-cards-row; a trailing face-down card
// marks "the next guess" and is only shown while the chain is still open. The row never
// wraps to a second line -- once more than POKER_DOUBLE_VISIBLE_CARDS have been revealed,
// only the most recent ones are kept on screen (older ones are dropped, not scrolled to).
let pokerDoubleCards = [];
const POKER_FLIP_RANKS = ["7","8","9","10","J","Q","K","A"];
const POKER_DOUBLE_VISIBLE_CARDS = 4;
function renderPokerDoubleCardsRow(showNextBack){
  const row = document.getElementById("pd-cards-row");
  const visible = pokerDoubleCards.slice(-POKER_DOUBLE_VISIBLE_CARDS);
  let html = visible.map(c => c.color === "joker"
    ? `<div class="pd-mini-card joker">🃏</div>`
    : `<div class="pd-mini-card ${c.color}"><div class="pd-mini-rank">${c.rank}</div><div class="pd-mini-suit">${c.suit}</div></div>`
  ).join("");
  if(showNextBack) html += `<div class="pd-mini-card back"></div>`;
  row.innerHTML = html;
  row.scrollLeft = row.scrollWidth;
}
function pokerDoubleRevealCard(colorResult){
  if(colorResult === "joker"){
    pokerDoubleCards.push({ color: "joker", suit: "🃏", rank: "" });
    return;
  }
  const isRed = colorResult === "red";
  const suit = isRed ? (Math.random() < 0.5 ? "♥" : "♦") : (Math.random() < 0.5 ? "♠" : "♣");
  const rank = POKER_FLIP_RANKS[Math.floor(Math.random() * POKER_FLIP_RANKS.length)];
  pokerDoubleCards.push({ color: isRed ? "red" : "black", suit, rank });
}

function openPokerDouble(){
  renderPokerDoubleLadder();
  pokerDoubleCards = [];
  renderPokerDoubleCardsRow(true);
  document.getElementById("poker-double-red").disabled = false;
  document.getElementById("poker-double-black").disabled = false;
  document.getElementById("poker-double-share").style.display = "none";
  document.getElementById("poker-double-overlay").classList.add("active");
}
function closePokerDouble(){
  document.getElementById("poker-double-overlay").classList.remove("active");
}
document.getElementById("poker-double-open-btn").addEventListener("click", openPokerDouble);
document.getElementById("poker-double-close").addEventListener("click", closePokerDouble);
document.getElementById("poker-double-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "poker-double-overlay") closePokerDouble();
});

function pokerShowIdle(){
  pokerPhase = "idle";
  pokerRoundId = null;
  pokerCards = [];
  pokerBaseWin = 0;
  pokerGambleLevel = 0;
  pokerDoubleCards = [];
  closePokerDouble();
  renderPokerHand(false);
  document.getElementById("poker-deal-btn").style.display = "";
  document.getElementById("poker-draw-btn").style.display = "none";
  document.getElementById("poker-gamble-wrap").style.display = "none";
}
pokerShowIdle();

async function dealPoker(){
  if(pokerBusy) return;
  if(pokerBet < POKER_MIN_BET){ toast("Минимальная ставка " + POKER_MIN_BET + " гемов"); return; }
  if(pokerBet > gemsBalance && !isAdminUser){ toast("Не хватает гемов"); return; }
  pokerBusy = true;
  document.getElementById("poker-deal-btn").disabled = true;
  document.getElementById("poker-result").innerHTML = "";
  try{
    const res = await api("/poker/deal", { bet: pokerBet });
    setGemsDisplay(res.gems);
    pokerRoundId = res.round_id;
    pokerCards = res.cards;
    pokerHold = pokerSuggestHold(pokerCards);
    pokerPhase = "dealt";
    document.getElementById("poker-deal-btn").style.display = "none";
    document.getElementById("poker-draw-btn").style.display = "";
    document.getElementById("poker-draw-btn").disabled = false;
    document.getElementById("poker-gamble-wrap").style.display = "none";
    renderPokerHand(true);
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    document.getElementById("poker-deal-btn").disabled = false;
    pokerBusy = false;
  }
}
document.getElementById("poker-deal-btn").addEventListener("click", dealPoker);

async function drawPoker(){
  if(pokerBusy || pokerPhase !== "dealt") return;
  pokerBusy = true;
  document.getElementById("poker-draw-btn").disabled = true;
  try{
    const res = await api("/poker/draw", { round_id: pokerRoundId, hold_mask: pokerHold });
    setGemsDisplay(res.gems);
    pokerCards = res.cards;
    renderPokerHand(false);
    const resultEl = document.getElementById("poker-result");
    const label = POKER_CATEGORY_LABEL[res.category] || res.category;
    if(res.payout > 0){
      resultEl.innerHTML = `<span class="poker-win">${label}</span>`;
      tg?.HapticFeedback?.notificationOccurred("success");
      pokerPhase = "drawn";
      pokerBaseWin = res.payout;
      pokerGambleLevel = 0;
      document.getElementById("poker-draw-btn").style.display = "none";
      document.getElementById("poker-gamble-amount").textContent = res.payout;
      document.getElementById("poker-gamble-wrap").style.display = "";
    }else{
      resultEl.innerHTML = `<span class="poker-lose">Мимо</span>`;
      tg?.HapticFeedback?.notificationOccurred("error");
      pokerShowIdle();
    }
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    pokerBusy = false;
  }
}
document.getElementById("poker-draw-btn").addEventListener("click", drawPoker);

async function gamblePokerChoice(choice){
  if(pokerBusy || pokerPhase !== "drawn") return;
  pokerBusy = true;
  document.getElementById("poker-double-red").disabled = true;
  document.getElementById("poker-double-black").disabled = true;
  try{
    const res = await api("/poker/gamble", { round_id: pokerRoundId, choice });
    setGemsDisplay(res.gems);
    pokerDoubleRevealCard(res.result); // the face-down "next" card reveals the actual color (or the Joker) that came up, then stays
    const resultEl = document.getElementById("poker-result");
    const isJokerReveal = res.result === "joker";
    const colorName = res.result === "red" ? "красное" : "чёрное";
    const colorEmoji = res.result === "red" ? "🔴" : "⚫";
    if(res.won){
      resultEl.innerHTML = isJokerReveal
        ? `<span class="poker-win">🃏 ДЖОКЕР — авто-выигрыш!</span>`
        : `<span class="poker-win">${colorEmoji} Выпало ${colorName}</span>`;
      tg?.HapticFeedback?.notificationOccurred("success");
      pokerGambleLevel = res.gamble_count;
      document.getElementById("poker-gamble-amount").textContent = res.payout;
      renderPokerDoubleLadder();
      if(res.can_gamble){
        renderPokerDoubleCardsRow(true); // revealed card stays, a fresh face-down card appears next to it
        document.getElementById("poker-double-red").disabled = false;
        document.getElementById("poker-double-black").disabled = false;
      }else{
        renderPokerDoubleCardsRow(false); // chain is done — no more face-down card
        toast("Лесенка пробита полностью — забери выигрыш!");
        document.getElementById("poker-double-share").style.display = "";
      }
    }else{
      renderPokerDoubleCardsRow(false); // busted — revealed card stays, chain is over
      resultEl.innerHTML = `<span class="poker-lose">${colorEmoji} Выпало ${colorName} — сгорел весь выигрыш</span>`;
      document.getElementById("poker-gamble-amount").textContent = 0;
      tg?.HapticFeedback?.notificationOccurred("error");
      setTimeout(()=>{
        closePokerDouble();
        pokerShowIdle();
      }, 700);
    }
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    pokerBusy = false;
  }
}
document.getElementById("poker-double-red").addEventListener("click", ()=>gamblePokerChoice("red"));
document.getElementById("poker-double-black").addEventListener("click", ()=>gamblePokerChoice("black"));

async function collectPokerWinnings(){
  if(pokerBusy || pokerPhase !== "drawn") return;
  pokerBusy = true;
  try{
    const res = await api("/poker/collect", { round_id: pokerRoundId });
    setGemsDisplay(res.gems);
    toast("Забрал " + res.payout + " гемов");
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    pokerBusy = false;
    closePokerDouble();
    pokerShowIdle();
  }
}
document.getElementById("poker-collect-btn").addEventListener("click", collectPokerWinnings);
document.getElementById("poker-double-collect").addEventListener("click", collectPokerWinnings);
document.getElementById("poker-double-share").addEventListener("click", (e)=>shareGameResult("poker", pokerRoundId, e.currentTarget));

// ---------- Aviator (in-app, fully independent from the chat /go game) ----------
const AVIATOR_MIN_BET = 25; // mirrors database.py's AVIATOR_MIN_BET -- client-side pre-check only, server enforces for real
const AVIATOR_POLL_MS = 600; // faster than the 1.5s tick cadence for a snappier-feeling cashout button, not for accuracy
let avBet = 25;
let avRoundId = null;
let avPollTimer = null;
let avBusy = false;
let avHistoryLog = [];

function renderAviatorBet(){
  document.getElementById("av-bet-value").textContent = avBet;
  document.querySelectorAll("#av-presets button").forEach(b=>{
    b.classList.toggle("active", parseInt(b.dataset.bet, 10) === avBet);
  });
}
document.getElementById("av-presets").addEventListener("click", (e)=>{
  const btn = e.target.closest("button[data-bet]");
  if(!btn) return;
  avBet = parseInt(btn.dataset.bet, 10);
  renderAviatorBet();
});
document.getElementById("av-bet-edit").addEventListener("click", async ()=>{
  const val = await promptNumber("Ставка (гемов, минимум " + AVIATOR_MIN_BET + ")");
  if(val && val >= AVIATOR_MIN_BET){
    avBet = val;
    renderAviatorBet();
  }else if(val){
    toast("Минимальная ставка " + AVIATOR_MIN_BET + " гемов");
  }
});

function showAviatorIdle(){
  document.getElementById("av-idle").style.display = "";
  document.getElementById("av-flying").style.display = "none";
}
function showAviatorFlying(){
  document.getElementById("av-idle").style.display = "none";
  document.getElementById("av-flying").style.display = "";
}

function updateAviatorMultiplier(m, capped){
  const mEl = document.getElementById("av-multiplier");
  mEl.textContent = m.toFixed(2) + "x";
  mEl.classList.toggle("av-capped", !!capped);
  document.getElementById("av-cashout-amount").textContent = Math.round(avBet * m);
}

function prependAvHistory(entry){
  avHistoryLog.unshift(entry);
  avHistoryLog = avHistoryLog.slice(0, 12);
  document.getElementById("av-history").innerHTML = avHistoryLog.map(r=>{
    const label = (r.multiplier || 0).toFixed(2) + "x";
    return `<div class="av-history-row ${r.won ? "win" : "lose"}">${label}</div>`;
  }).join("");
}

async function startAviator(){
  if(avBusy) return;
  if(avBet < AVIATOR_MIN_BET){ toast("Минимальная ставка " + AVIATOR_MIN_BET + " гемов"); return; }
  if(avBet > gemsBalance && !isAdminUser){ toast("Не хватает гемов"); return; }
  avBusy = true;
  document.getElementById("av-start-btn").disabled = true;
  try{
    const res = await api("/aviator/start", { bet: avBet });
    avRoundId = res.round_id;
    if(!isAdminUser) setGemsDisplay(gemsBalance - avBet); // start_aviator already deducted the bet server-side
    document.getElementById("av-result").innerHTML = "";
    document.getElementById("av-share-btn").style.display = "none";
    showAviatorFlying();
    updateAviatorMultiplier(1.00, false);
    stopAviatorPolling();
    avPollTimer = setInterval(pollAviator, AVIATOR_POLL_MS);
  }catch(e){
    toast("Ошибка: " + e.message);
  }finally{
    avBusy = false;
    document.getElementById("av-start-btn").disabled = false;
  }
}
document.getElementById("av-start-btn").addEventListener("click", startAviator);

function stopAviatorPolling(){
  if(avPollTimer){ clearInterval(avPollTimer); avPollTimer = null; }
}

function onAviatorCrashed(st){
  const crash = st.crash_point ?? st.multiplier ?? 0;
  const finishedRoundId = avRoundId;
  document.getElementById("av-result").innerHTML =
    `<span class="av-lose">💥 Улетела на ${crash.toFixed(2)}x — проигрыш -${st.bet} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></span>`;
  tg?.HapticFeedback?.notificationOccurred("error");
  prependAvHistory({ won: false, multiplier: crash });
  avRoundId = null;
  showAviatorIdle();
  const shareBtn = document.getElementById("av-share-btn");
  shareBtn.style.display = "block";
  shareBtn.textContent = "Поделиться в чат";
  shareBtn.disabled = false;
  shareBtn.onclick = ()=>shareGameResult("aviator", finishedRoundId, shareBtn);
}

function onAviatorCashedOut(res){
  const multiplier = res.multiplier ?? (avBet > 0 ? res.payout / avBet : 0);
  const profit = res.payout - res.bet;
  const finishedRoundId = avRoundId;
  document.getElementById("av-result").innerHTML =
    `<span class="av-win">✅ Забрал на ${multiplier.toFixed(2)}x — выигрыш +${profit} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt=""></span>`;
  tg?.HapticFeedback?.notificationOccurred("success");
  prependAvHistory({ won: true, multiplier });
  avRoundId = null;
  if(typeof res.gems === "number") setGemsDisplay(res.gems);
  showAviatorIdle();
  const shareBtn = document.getElementById("av-share-btn");
  shareBtn.style.display = "block";
  shareBtn.textContent = "Поделиться в чат";
  shareBtn.disabled = false;
  shareBtn.onclick = ()=>shareGameResult("aviator", finishedRoundId, shareBtn);
}

async function pollAviator(){
  if(!avRoundId) return;
  try{
    const st = await api("/aviator/state", { round_id: avRoundId });
    if(st.status === "flying"){
      updateAviatorMultiplier(st.multiplier, st.capped);
    }else if(st.status === "lost"){
      stopAviatorPolling();
      onAviatorCrashed(st);
    }else if(st.status === "won"){
      stopAviatorPolling();
      onAviatorCashedOut(st);
    }
  }catch(e){
    // transient network hiccup -- next poll retries, no need to spam a toast every 600ms
  }
}

document.getElementById("av-cashout-btn").addEventListener("click", async ()=>{
  if(avBusy || !avRoundId) return;
  avBusy = true;
  const btn = document.getElementById("av-cashout-btn");
  btn.disabled = true;
  stopAviatorPolling();
  try{
    const res = await api("/aviator/cashout", { round_id: avRoundId });
    onAviatorCashedOut(res);
  }catch(e){
    // most likely: it crashed a split-second before this landed server-side -- re-poll
    // once to learn the real outcome instead of guessing.
    try{
      const st = await api("/aviator/state", { round_id: avRoundId });
      if(st.status === "lost") onAviatorCrashed(st);
      else if(st.status === "won") onAviatorCashedOut(st);
      else{ toast("Ошибка: " + e.message); avPollTimer = setInterval(pollAviator, AVIATOR_POLL_MS); }
    }catch(e2){
      toast("Ошибка: " + e.message);
    }
  }finally{
    avBusy = false;
    btn.disabled = false;
  }
});

const PVP_CARDS_PER_RARITY_CAP = 12; // 2 rows of 6 at this card-thumbnail width

function pvpFormatParticipantCards(cards){
  const groups = {};
  const order = [];
  cards.forEach(c => {
    if(!groups[c.rarity]){ groups[c.rarity] = []; order.push(c.rarity); }
    groups[c.rarity].push(c);
  });
  return order.map(rarity => {
    const group = groups[rarity];
    const shown = group.slice(0, PVP_CARDS_PER_RARITY_CAP);
    const extra = group.length - shown.length;
    return shown.map((c, i) => {
      const img = `<img class="rarity-border-${c.rarity}" src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}" title="${c.name||''}">`;
      if(extra > 0 && i === shown.length - 1){
        return `<div class="pvp-card-slot" title="ещё ${extra} ${rarity}">${img}<div class="pvp-cards-more-badge">+${extra}</div></div>`;
      }
      return img;
    }).join("");
  }).join("");
}

function renderPvp(){
  const list = document.getElementById("pvp-list");
  if(!pvpState){ list.innerHTML = ""; return; }

  const { seconds_left, participants, last_result, unseen_result } = pvpState;
  const counting = seconds_left !== null && seconds_left !== undefined;
  // Betting used to close a few seconds before the round resolved — removed by request,
  // you can now stake cards right up until the round actually resolves.
  const PVP_JOIN_CUTOFF_SECONDS = 0;
  const joinClosed = counting && seconds_left <= PVP_JOIN_CUTOFF_SECONDS;

  let timerText, holeBig, holeSmall;
  if(joinClosed){
    timerText = `Ставки закрыты — старт через ${seconds_left}с`;
    holeBig = String(seconds_left);
    holeSmall = "ставки закрыты";
  }else if(counting){
    timerText = `До начала: ${seconds_left}с`;
    holeBig = String(seconds_left);
    holeSmall = "сек до старта";
  }else if(participants.length === 1){
    timerText = "Ждём ещё одного игрока...";
    holeBig = "⏳";
    holeSmall = "ждём соперника";
  }else{
    timerText = "Банк пуст — стань первым!";
    holeBig = "🎁";
    holeSmall = "банк пуст";
  }

  const { gradient, segments } = pvpBuildWheel(participants);

  const legendRows = segments.map(p=>{
    const name = maskedName(p.username) || p.first_name || "Игрок";
    const you = p.user_id === myTelegramId ? " you" : "";
    const cards = pvpFormatParticipantCards(p.cards);
    return `
      <div class="pvp-participant">
        <div class="pvp-participant-head">
          <span class="pvp-swatch" style="background:${p.color}"></span>
          <span class="pvp-participant-name${you}">${name}</span>
          <span class="pvp-participant-chance">${p.chance_pct}%</span>
        </div>
        <div class="pvp-participant-cards">${cards}</div>
      </div>`;
  }).join("") || `<div class="empty-state"><span class="emoji">⚔️</span>Пока никто не поставил карты.<br>Стань первым в банке!</div>`;

  // Past-round results now live in "История" (see openHistory) instead of an inline
  // banner here — last_result is kept in the API response only so the unseen_result
  // reveal check below still has something to compare against.
  list.innerHTML = `
    <div class="pvp-arena">
      <div class="pvp-arena-top">
        <span class="pvp-arena-title">⚔️ Общий банк</span>
        <span class="pvp-live ${counting ? 'on' : ''}"><span class="pvp-live-dot"></span>${counting ? 'Live' : 'Ожидание'}</span>
      </div>
      <div class="pvp-header">
        <div class="pvp-timer ${counting ? 'counting' : ''}">${timerText}</div>
      </div>
      <button id="pvp-join-btn" ${joinClosed ? "disabled" : ""}>${joinClosed ? "Ставки закрыты" : "Поставить карты"}</button>
      <button id="pvp-invite-btn" class="pvp-invite-btn" ${participants.length >= 2 ? "disabled" : ""}>Позвать игрока</button>
      <div class="pvp-legend">${legendRows}</div>
    </div>
  `;
  if(!joinClosed){
    document.getElementById("pvp-join-btn").addEventListener("click", openPvpPicker);
  }
  document.getElementById("pvp-invite-btn").addEventListener("click", async (e)=>{
    const btn = e.currentTarget;
    btn.disabled = true;
    try{
      await api("/pvp/invite");
      toast("Зов отправлен в чат!");
    }catch(err){
      const m = err.message || "";
      const coolMatch = m.match(/cooldown:\s*(\d+)s/);
      if(coolMatch){
        const s = parseInt(coolMatch[1], 10);
        const mins = Math.ceil(s / 60);
        toast(`Можно звать раз в 5 минут — подожди ещё ~${mins} мин.`);
      }else if(m.includes("already_full")){
        toast("В банке уже 2+ игрока — звать больше не нужно");
      }else if(m.includes("not_joined")){
        toast("Сначала поставь карты в банк");
      }else{
        toast("Не удалось позвать: " + m);
      }
    }finally{
      setTimeout(()=>{ btn.disabled = false; }, 1500);
    }
  });

  // Only the FIRST render right after opening/returning to the PvP screen suppresses
  // the reveal -- that fetch can carry a stale unseen_result left over from a round
  // that concluded while the player was elsewhere, and they don't want a replay of
  // something already finished just from walking in. Every render after that one is
  // treated as "genuinely live": a round concluding while they're actually sitting here
  // watching still spins to reveal the winner as normal.
  const isFirstRenderSinceOpen = pvpTabJustOpened;
  pvpTabJustOpened = false;
  if(unseen_result){
    // A round just resolved -- if the round was won, the winning cards are already
    // transferred server-side, but the client's local `inventory` array is stale
    // until the next full profile load. Without this, a just-won card silently
    // doesn't show up in the PvP picker (or Profile) until something else happens
    // to reload it -- refresh it right away so it can be staked again immediately.
    api("/profile").then(data => {
      inventory = data.inventory;
      sortInventoryAndRender();
    }).catch(()=>{ /* non-critical -- next profile visit will pick it up anyway */ });

    if(!isFirstRenderSinceOpen){
      if(pvpMode === "cards"){
        openPvpReveal(unseen_result);
      }else{
        // Watching a different PvP sub-tab (Ракетка/Red&Black/Poker) when the cards
        // round concluded -- queue it so switching back to "cards" shows the reveal.
        pendingPvpReveal = unseen_result;
      }
    }
  }
}

// ---------- PvP reveal (spin the wheel to the winner) ----------
function openPvpReveal(result){
  const { gradient, segments } = pvpBuildWheel(result.participants || []);
  const wheel = document.getElementById("pvp-reveal-wheel");
  const needle = document.getElementById("pvp-spinner-needle");
  const holeIcon = document.getElementById("pvp-reveal-hole-icon");
  const legend = document.getElementById("pvp-reveal-legend");
  const outcome = document.getElementById("pvp-reveal-outcome");
  const closeBtn = document.getElementById("pvp-reveal-close");
  const title = document.getElementById("pvp-reveal-title");

  title.textContent = "\ud83c\udfb2 \u0420\u043e\u0437\u044b\u0433\u0440\u044b\u0448 \u0431\u0430\u043d\u043a\u0430...";
  outcome.style.display = "none";
  outcome.className = "";
  outcome.textContent = "";
  closeBtn.style.display = "none";
  if(holeIcon) holeIcon.textContent = "\ud83c\udf81";

  // The ring itself never moves -- it's just a static donut divided into each
  // participant's slice (same segments/gradient pvpBuildWheel already computed for
  // the old wheel). Only the needle spins, pivoting from the center, and stops
  // pointing at the winner's slice.
  wheel.style.background = segments.length ? `conic-gradient(${gradient})` : "#2a2a2a";

  legend.innerHTML = segments.map(p=>{
    const name = maskedName(p.username) || p.first_name || "\u0418\u0433\u0440\u043e\u043a";
    const isWinner = p.user_id === result.winner_id;
    return `
      <div class="pvp-reveal-row${isWinner ? ' winner' : ''}">
        <span class="pvp-swatch" style="background:${p.color}"></span>
        <span class="name">${name}</span>
        <span>${p.chance_pct}%</span>
      </div>`;
  }).join("");

  document.getElementById("pvp-reveal-overlay").classList.add("active");

  let finished = false;
  const finish = ()=>{
    if(finished) return;
    finished = true;
    if(holeIcon) holeIcon.textContent = "\ud83c\udfc6";
    const iWon = result.winner_id === myTelegramId;
    title.textContent = "\ud83c\udfc6 \u041f\u043e\u0431\u0435\u0434\u0438\u0442\u0435\u043b\u044c!";
    outcome.style.display = "block";
    outcome.className = iWon ? "won" : "";
    outcome.textContent = iWon
      ? `\u0422\u044b \u0432\u044b\u0438\u0433\u0440\u0430\u043b! \u0417\u0430\u0431\u0440\u0430\u043b \u0432\u0435\u0441\u044c \u0431\u0430\u043d\u043a — ${pluralCards(result.total_cards)}.`
      : `\u041f\u043e\u0431\u0435\u0434\u0438\u043b ${result.winner_name} — \u0437\u0430\u0431\u0440\u0430\u043b ${pluralCards(result.total_cards)} \u0443 ${result.total_players} \u0438\u0433\u0440\u043e\u043a\u043e\u0432.`;
    closeBtn.style.display = "block";
  };

  // The winner is already decided server-side (result.winner_id, from the same
  // weighted segments as before) -- the needle spin below is purely cosmetic on
  // top of that already-decided outcome. Snap the needle back to 0deg instantly
  // (no transition), then on the next frame kick off the real spin toward the
  // winner's slice, with a few extra full turns thrown in for suspense.
  needle.style.transition = "none";
  needle.style.transform = "translate(-50%,-100%) rotate(0deg)";
  void needle.offsetWidth;

  const winnerSeg = segments.find(s => s.user_id === result.winner_id);
  const targetMid = winnerSeg ? winnerSeg.mid : 0;
  const extraSpins = 5;
  const finalDeg = extraSpins * 360 + targetMid;

  requestAnimationFrame(()=>{
    requestAnimationFrame(()=>{
      needle.style.transition = "transform 4.2s cubic-bezier(.15,.85,.25,1)";
      needle.style.transform = `translate(-50%,-100%) rotate(${finalDeg}deg)`;
    });
  });

  needle.addEventListener("transitionend", finish, {once:true});
  setTimeout(finish, 4600); // fallback in case transitionend ever doesn't fire
}

document.getElementById("pvp-reveal-close").addEventListener("click", ()=>{
  document.getElementById("pvp-reveal-overlay").classList.remove("active");
});

// ---------- Daily fortune wheel ----------
// Fixed visual layout — 12 equal 30° slices: 8 empty + two "25" + one "100" + one
// "1000" (jackpot). The server never reveals the real odds (3%/2%/1% — see
// database.py's spin_fortune_wheel), so these slice sizes are NOT the true chances,
// just 12 even slots to spin across. On a loss the wheel lands on a random empty slice.
const FORTUNE_SEGMENTS = [
  { color:"#1a1a1a", amount:0 },
  { color:"#2a2a2a", amount:0 },
  { color:"#34d399", amount:25 },
  { color:"#1a1a1a", amount:0 },
  { color:"#2a2a2a", amount:0 },
  { color:"#34d399", amount:25 },
  { color:"#1a1a1a", amount:0 },
  { color:"#a78bfa", amount:100 },
  { color:"#2a2a2a", amount:0 },
  { color:"#1a1a1a", amount:0 },
  { color:"#facc15", amount:1000 },
  { color:"#2a2a2a", amount:0 },
].map((s, i)=>{
  const size = 360 / 12;
  const start = i * size, end = start + size;
  return { ...s, start, end, mid: (start + end) / 2 };
});

function fortuneGradient(){
  return FORTUNE_SEGMENTS.map(s=>`${s.color} ${s.start}deg ${s.end}deg`).join(", ");
}

async function maybeOpenFortuneWheel(){
  let spinResult;
  try{
    spinResult = await api("/wheel/spin");
  }catch(e){ return; } // already spun today (or offline) — skip silently
  openFortuneWheel(spinResult.amount);
}

function openFortuneWheel(amount){
  const wheel = document.getElementById("fortune-wheel");
  const legend = document.getElementById("fortune-legend");
  const outcome = document.getElementById("fortune-outcome");
  const closeBtn = document.getElementById("fortune-close");
  const title = document.getElementById("fortune-title");

  title.textContent = "🎡 Колесо фортуны крутится...";
  outcome.style.display = "none";
  outcome.className = "";
  outcome.textContent = "";
  closeBtn.style.display = "none";

  legend.innerHTML = `
    <div class="fortune-legend-row" style="border-left-color:#4b4b4b"><span class="pvp-swatch" style="background:#2a2a2a"></span><span class="name">Пусто</span></div>
    <div class="fortune-legend-row" style="border-left-color:#34d399"><span class="pvp-swatch" style="background:#34d399"></span><span class="name">+25 <img src="/static/icons/diamond.png" alt="" style="width:12px;height:12px;vertical-align:-1px;"></span></div>
    <div class="fortune-legend-row" style="border-left-color:#a78bfa"><span class="pvp-swatch" style="background:#a78bfa"></span><span class="name">+100 <img src="/static/icons/diamond.png" alt="" style="width:12px;height:12px;vertical-align:-1px;"></span></div>
    <div class="fortune-legend-row jackpot" style="border-left-color:#facc15"><span class="pvp-swatch" style="background:#facc15"></span><span class="name">+1000 <img src="/static/icons/diamond.png" alt="" style="width:12px;height:12px;vertical-align:-1px;"> ДЖЕКПОТ</span></div>
  `;

  wheel.style.transition = "none";
  wheel.style.transform = "rotate(0deg)";
  wheel.style.background = `conic-gradient(${fortuneGradient()})`;
  void wheel.offsetWidth; // force reflow so the transition-reset above actually applies

  const matching = FORTUNE_SEGMENTS.filter(s=>s.amount === amount);
  const targetSeg = matching[Math.floor(Math.random() * matching.length)];
  const spins = 5;
  const finalDeg = spins * 360 + ((360 - targetSeg.mid) % 360);

  document.getElementById("fortune-overlay").classList.add("active");

  requestAnimationFrame(()=>{
    requestAnimationFrame(()=>{
      wheel.style.transition = "transform 4.2s cubic-bezier(.15,.85,.25,1)";
      wheel.style.transform = `rotate(${finalDeg}deg)`;
    });
  });

  let finished = false;
  const finish = ()=>{
    if(finished) return;
    finished = true;
    const isJackpot = amount >= 1000;
    title.textContent = isJackpot ? "🏆 ДЖЕКПОТ!!!" : (amount > 0 ? "🎉 Повезло!" : "🎡 Колесо фортуны");
    outcome.style.display = "block";
    outcome.className = amount > 0 ? (isJackpot ? "won jackpot" : "won") : "";
    outcome.innerHTML = amount > 0
      ? `Ты выиграл ${amount} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">! Приходи завтра ещё раз.`
      : "Сегодня не повезло — приходи завтра, будет ещё попытка.";
    closeBtn.style.display = "block";
  };
  wheel.addEventListener("transitionend", finish, { once: true });
  setTimeout(finish, 4600); // fallback in case transitionend doesn't fire
}

document.getElementById("fortune-close").addEventListener("click", ()=>{
  document.getElementById("fortune-overlay").classList.remove("active");
});

// ---------- Gem mining (modal, opened from the Farm screen's "Добыча" button) ----------
// Same one-button "press to start, wait 60 min, press again to collect + restart"
// loop as before, just as its own modal now instead of an inline card, so it never
// pushes the Кейсы/Номера buttons around. Status is fetched fresh from /auth each
// time the modal opens; the countdown itself then runs client-side (cosmetic only —
// /api/gemmining/collect is always the source of truth for whether a claim is ready).
let miningTimer = null;
let miningBusy = false;
let lastMiningActive = false;
let miningQuickTimer = null;

function showMiningQuickModal(text){
  clearTimeout(miningQuickTimer);
  document.getElementById("mining-quick-text").innerHTML = text;
  const overlay = document.getElementById("mining-quick-overlay");
  overlay.classList.add("active");
  miningQuickTimer = setTimeout(()=>{
    overlay.classList.remove("active");
  }, 2000);
}

function formatMMSS(totalSeconds){
  const s = Math.max(0, Math.floor(totalSeconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2,"0")}:${String(r).padStart(2,"0")}`;
}

function renderMining(status){
  clearInterval(miningTimer);
  lastMiningActive = !!status.active;
  const label = document.getElementById("mining-status");
  const btn = document.getElementById("mining-action-btn");
  const openBtn = document.getElementById("mining-open-btn");
  const progressWrap = document.getElementById("mining-progress-wrap");
  const progressBar = document.getElementById("mining-progress-bar");
  const DURATION = 3600;

  if(!status.active){
    progressWrap.classList.remove("active");
    btn.classList.remove("ready");
    btn.disabled = false;
    btn.textContent = "Начать добычу";
    label.textContent = "Жми, чтобы начать — через час заберёшь 50 гемов";
    openBtn.classList.remove("ready", "mining");
    openBtn.textContent = "⛏️ Гемить";
    return;
  }

  if(status.ready){
    progressWrap.classList.remove("active");
    btn.classList.add("ready");
    btn.disabled = false;
    btn.innerHTML = 'Забрать 50 <img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">';
    label.textContent = "Готово! Жми, чтобы забрать и начать заново";
    openBtn.classList.remove("mining");
    openBtn.classList.add("ready");
    openBtn.textContent = "⛏️ Забрать";
    return;
  }

  btn.classList.remove("ready");
  btn.disabled = true;
  progressWrap.classList.add("active");
  openBtn.classList.remove("ready");
  openBtn.classList.add("mining");
  // While a cycle is running, the button itself becomes the "in progress" indicator —
  // a spinning pickaxe (CSS animation on .mining-pick) plus the live countdown next to
  // it, instead of just sitting there looking idle with the word "Гемить" on it.
  let secondsLeft = status.seconds_left;
  const tick = ()=>{
    const pct = Math.max(0, Math.min(100, ((DURATION - secondsLeft) / DURATION) * 100));
    progressBar.style.width = pct + "%";
    label.textContent = `Добывается... осталось ${formatMMSS(secondsLeft)}`;
    btn.textContent = `⏳ ${formatMMSS(secondsLeft)}`;
    openBtn.innerHTML = `<span class="mining-pick">⛏️</span> ${formatMMSS(secondsLeft)}`;
    if(secondsLeft <= 0){
      clearInterval(miningTimer);
      renderMining({ active:true, ready:true, seconds_left:0 });
      return;
    }
    secondsLeft -= 1;
  };
  tick();
  miningTimer = setInterval(tick, 1000);
}

document.getElementById("mining-open-btn").addEventListener("click", async ()=>{
  if(miningBusy) return;
  miningBusy = true;
  const wasActive = lastMiningActive;
  try{
    const result = await api("/gemmining/collect");
    if(result.claimed > 0){
      setGemsDisplay(result.gems);
      tg?.HapticFeedback?.notificationOccurred("success");
      showMiningQuickModal(`+${result.claimed} <img src="/static/icons/diamond.png" alt="" style="width:16px;height:16px;object-fit:contain;"> добыто!\nНовый цикл начался ⛏️`);
    }else if(!wasActive){
      showMiningQuickModal("Фарминг запущен! ⛏️\nЗагляни через час");
    }else{
      showMiningQuickModal(`Уже фармится...\nОсталось ${formatMMSS(result.seconds_left)}`);
    }
    renderMining(result);
  }catch(e){
    toast("Не получилось: " + e.message);
  }finally{
    miningBusy = false;
  }
});
document.getElementById("mining-close").addEventListener("click", ()=>{
  document.getElementById("mining-overlay").classList.remove("active");
  clearInterval(miningTimer);
});
document.getElementById("mining-quick-overlay").addEventListener("click", ()=>{
  clearTimeout(miningQuickTimer);
  document.getElementById("mining-quick-overlay").classList.remove("active");
});

document.getElementById("mining-action-btn").addEventListener("click", async ()=>{
  if(miningBusy) return;
  miningBusy = true;
  const btn = document.getElementById("mining-action-btn");
  btn.disabled = true;
  try{
    const result = await api("/gemmining/collect");
    if(result.claimed > 0){
      setGemsDisplay(result.gems);
      toastHTML(`+${result.claimed} <img src="/static/icons/diamond.png" alt="" style="width:16px;height:16px;object-fit:contain;"> добыто! Новый цикл начался.`);
      tg?.HapticFeedback?.notificationOccurred("success");
    }
    renderMining(result);
  }catch(e){
    toast("Не получилось: " + e.message);
    btn.disabled = false;
  }finally{
    miningBusy = false;
  }
});


// ---------- PvP picker (choose which of your own cards to stake — no taking them back) ----------
let pvpPickerSelected = new Set();

function openPvpPicker(){
  pvpPickerSelected = new Set();
  renderPvpPickerGrid();
  document.getElementById("pvp-picker-overlay").classList.add("active");
}

function getPvpPickerAvailableCards(){
  return inventory
    .filter(c => !c.listed_price && !c.swap_listed && !c.staked_at && !c.pvp_round_id && !c.pinned_at && !c.in_giveaway)
    // Bronze always first (same "worst first" ordering as the market's Bronze filter) —
    // makes it easy to stake your least valuable cards without hunting for them.
    .sort((a, b) => (RARITY_ORDER[b.rarity] ?? 4) - (RARITY_ORDER[a.rarity] ?? 4));
}

function renderPvpPickerGrid(){
  const grid = document.getElementById("pvp-picker-grid");
  const available = getPvpPickerAvailableCards();
  if(available.length === 0){
    grid.innerHTML = `<div class="empty-state">Нет свободных карт — сними с продажи/обмена/стейкинга или иди фармить 🌱</div>`;
    return;
  }
  grid.innerHTML = available.map(c => `
    <div class="grid-item picker-item rarity-border-${c.rarity} ${pvpPickerSelected.has(c.user_card_id) ? 'selected' : ''}" data-uc="${c.user_card_id}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}">
      <div class="rarity-tag ${c.rarity}">${c.rarity}</div>
      <div class="check">✓</div>
    </div>
  `).join("");
  grid.querySelectorAll(".picker-item").forEach(el=>{
    el.addEventListener("click", ()=>{
      const ucId = parseInt(el.dataset.uc);
      if(pvpPickerSelected.has(ucId)) pvpPickerSelected.delete(ucId);
      else pvpPickerSelected.add(ucId);
      el.classList.toggle("selected");
    });
  });
}

// Toggle: if every card in the given set is already selected, a second press
// deselects them all instead of being a no-op -- same idea for "Выбрать все" and
// each rarity quick-filter button.
function pvpPickerToggleSelection(cards){
  const allSelected = cards.length > 0 && cards.every(c => pvpPickerSelected.has(c.user_card_id));
  if(allSelected){
    cards.forEach(c => pvpPickerSelected.delete(c.user_card_id));
  }else{
    cards.forEach(c => pvpPickerSelected.add(c.user_card_id));
  }
}
document.getElementById("pvp-picker-select-all").addEventListener("click", ()=>{
  pvpPickerToggleSelection(getPvpPickerAvailableCards());
  renderPvpPickerGrid();
});
document.getElementById("pvp-picker-rarity-filters").addEventListener("click", (e)=>{
  const btn = e.target.closest(".pvp-picker-rarity-btn");
  if(!btn) return;
  const rarity = btn.dataset.rarity;
  pvpPickerToggleSelection(getPvpPickerAvailableCards().filter(c => c.rarity === rarity));
  renderPvpPickerGrid();
});
document.getElementById("pvp-picker-cancel").addEventListener("click", ()=>{
  document.getElementById("pvp-picker-overlay").classList.remove("active");
});

document.getElementById("pvp-picker-confirm").addEventListener("click", async ()=>{
  if(pvpPickerSelected.size === 0){ toast("Выбери хотя бы одну карту"); return; }
  try{
    pvpState = await api("/pvp/join", { user_card_ids: [...pvpPickerSelected] });
    // Refresh the inventory so the cards just staked disappear from every picker
    // (pvp_round_id is now set on them) instead of still showing as available.
    try{
      const data = await api("/profile");
      inventory = data.inventory;
    }catch(e){ /* non-critical — next profile visit will pick it up anyway */ }
    renderPvp();
    document.getElementById("pvp-picker-overlay").classList.remove("active");
  }catch(e){
    toast("Не получилось: " + e.message);
  }
});

// ---------- Card catalog (all models — used by the farm animation and the gallery) ----------
let allCardsCache = null;
async function ensureAllCards(){
  if(allCardsCache) return allCardsCache;
  try{
    const res = await fetch(API_BASE + "/cards");
    const data = await res.json();
    allCardsCache = data.cards;
  }catch(e){
    allCardsCache = [];
  }
  return allCardsCache;
}

let zoomList = [];
let zoomIndex = 0;
let zoomStartX = 0, zoomStartY = 0, zoomJustSwiped = false;

function renderZoom(){
  const c = zoomList[zoomIndex];
  if(!c) return;
  const zoomImgEl = document.getElementById("zoom-img");
  zoomImgEl.src = `/static/cards/${c.filename}?v=${CARD_IMG_VERSION}`;
  setRarityBorder(zoomImgEl, c.rarity);
  const isObsidian = !!c.custom_name;
  zoomImgEl.classList.toggle("obsidian-border", isObsidian);
  document.getElementById("zoom-img-wrap").classList.toggle("obsidian", isObsidian);
  const zoomRarityBadge = document.getElementById("zoom-rarity-tag");
  zoomRarityBadge.textContent = isObsidian ? "OBSIDIAN" : (c.rarity || "");
  zoomRarityBadge.className = `zoom-rarity-tag ${isObsidian ? "obsidian" : (c.rarity || "")}`;
  const zoomNumEl = document.getElementById("zoom-drop-number");
  if(c.drop_number){
    zoomNumEl.textContent = `#${c.drop_number}`;
    zoomNumEl.style.display = "";
  }else{
    zoomNumEl.style.display = "none";
  }
  document.getElementById("zoom-name").textContent = capName(c.name) || "";
}

function openZoom(list, index){
  zoomList = list;
  zoomIndex = index;
  renderZoom();
  document.getElementById("zoom-overlay").classList.add("active");
}

const zoomOverlayEl = document.getElementById("zoom-overlay");
zoomOverlayEl.addEventListener("touchstart", (e)=>{
  zoomStartX = e.touches[0].clientX;
  zoomStartY = e.touches[0].clientY;
  zoomJustSwiped = false;
}, { passive: true });
zoomOverlayEl.addEventListener("touchend", (e)=>{
  const dx = e.changedTouches[0].clientX - zoomStartX;
  const dy = e.changedTouches[0].clientY - zoomStartY;
  if(zoomList.length > 1 && Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)){
    zoomJustSwiped = true;
    zoomIndex = dx < 0 ? (zoomIndex + 1) % zoomList.length : (zoomIndex - 1 + zoomList.length) % zoomList.length;
    renderZoom();
  }
}, { passive: true });
zoomOverlayEl.addEventListener("click", ()=>{
  if(zoomJustSwiped){ zoomJustSwiped = false; return; }
  zoomOverlayEl.classList.remove("active");
});

const RARITY_ORDER = { diamond: 0, platinum: 1, gold: 2, silver: 3, bronze: 4 };

// Sets/refreshes the rarity-colored border class on a single standalone <img> (the
// grid-rendered lists set this class directly in their template strings instead —
// see the .rarity-border-* CSS rules).
function setRarityBorder(el, rarity){
  el.classList.remove("rarity-border-bronze","rarity-border-silver","rarity-border-gold","rarity-border-platinum","rarity-border-diamond");
  if(rarity) el.classList.add("rarity-border-" + rarity);
}

let modelsSortMode = "best_first"; // "best_first" | "worst_first"
const MODELS_SORT_LABELS = { best_first: "DIAMOND → BRONZE", worst_first: "BRONZE → DIAMOND" };
const MODELS_SORT_CYCLE = ["best_first", "worst_first"];
let modelsFilterRarity = null; // null = show every rarity; set by clicking a rarity-summary label

function renderModelsGrid(){
  const allCards = allCardsCache || [];
  const cards = modelsFilterRarity ? allCards.filter(c => c.rarity === modelsFilterRarity) : allCards;
  const sortMode = modelsSortMode;
  const sorted = [...cards].sort((a, b) => {
    const ra = RARITY_ORDER[a.rarity] ?? 4, rb = RARITY_ORDER[b.rarity] ?? 4;
    if(ra !== rb) return sortMode === "worst_first" ? rb - ra : ra - rb;
    return (a.name || "").localeCompare(b.name || "", "ru");
  });
  const grid = document.getElementById("models-grid");
  grid.innerHTML = sorted.map(c => `
    <div class="grid-item rarity-border-${c.rarity}" data-src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}">
      <div class="rarity-tag ${c.rarity}">${c.rarity}</div>
      <div class="model-name-tag">${c.name || ""}</div>
    </div>
  `).join("");
  grid.querySelectorAll(".grid-item").forEach((el, idx)=>{
    el.addEventListener("click", ()=>openZoom(sorted, idx));
  });
}
document.getElementById("models-sort").addEventListener("click", ()=>{
  const idx = MODELS_SORT_CYCLE.indexOf(modelsSortMode);
  modelsSortMode = MODELS_SORT_CYCLE[(idx + 1) % MODELS_SORT_CYCLE.length];
  document.getElementById("models-sort").textContent = MODELS_SORT_LABELS[modelsSortMode];
  renderModelsGrid();
});

function updateModelsRaritySummary(cards, obsidianCount){
  const counts = {diamond:0, platinum:0, gold:0, silver:0, bronze:0};
  cards.forEach(c=>{ if(counts[c.rarity]!==undefined) counts[c.rarity]++; });
  const summary = document.getElementById("models-rarity-summary");
  // Row 1: Obsidian, Diamond, Platinum -- Row 2: Gold, Silver, Bronze (3-col grid above).
  summary.innerHTML =
    `<span class="obsidian" data-rarity="obsidian">👑 OBSIDIAN ${obsidianCount ?? 0}</span><span class="diamond" data-rarity="diamond">DIAMOND ${counts.diamond}</span><span class="platinum" data-rarity="platinum">PLATINUM ${counts.platinum}</span><span class="gold" data-rarity="gold">GOLD ${counts.gold}</span><span class="silver" data-rarity="silver">SILVER ${counts.silver}</span><span class="bronze" data-rarity="bronze">BRONZE ${counts.bronze}</span>`;
  summary.querySelectorAll("span[data-rarity]").forEach(el=>{
    if(el.dataset.rarity === "obsidian"){
      // Not a card rarity to filter the grid by -- opens the Obsidian catalog, same as
      // the old standalone button used to.
      el.addEventListener("click", openObsidianCatalog);
      return;
    }
    el.classList.toggle("active", el.dataset.rarity === modelsFilterRarity);
    el.addEventListener("click", ()=>{
      // Clicking the already-active rarity turns the filter back off.
      modelsFilterRarity = modelsFilterRarity === el.dataset.rarity ? null : el.dataset.rarity;
      summary.querySelectorAll("span[data-rarity]").forEach(s=>s.classList.toggle("active", s.dataset.rarity === modelsFilterRarity));
      renderModelsGrid();
    });
  });
}
document.getElementById("models-btn").addEventListener("click", async ()=>{
  const cards = await ensureAllCards();
  document.getElementById("models-count").textContent = cards.length;
  let obsidianCount = 0;
  try{
    const res = await api("/obsidian/list", {});
    obsidianCount = (res.cards || []).length;
  }catch(e){ /* non-critical -- label just shows 0 if this fails */ }
  updateModelsRaritySummary(cards, obsidianCount);
  renderModelsGrid();
  document.getElementById("models-overlay").classList.add("active");
});
document.getElementById("models-title").addEventListener("click", ()=>{
  // Resets back to showing every model, DIAMOND-first, regardless of what filter/sort
  // was active -- "Все модели" acting as its own reset button.
  modelsFilterRarity = null;
  modelsSortMode = "best_first";
  document.getElementById("models-sort").textContent = MODELS_SORT_LABELS[modelsSortMode];
  document.querySelectorAll("#models-rarity-summary span[data-rarity]").forEach(s=>s.classList.remove("active"));
  renderModelsGrid();
});
document.getElementById("models-close").addEventListener("click", ()=>{
  document.getElementById("models-overlay").classList.remove("active");
});
document.getElementById("models-scroll").addEventListener("scroll", (e)=>{
  const btn = document.getElementById("models-scroll-top");
  btn.classList.toggle("visible", e.target.scrollTop > 300);
});
document.getElementById("models-scroll-top").addEventListener("click", ()=>{
  document.getElementById("models-scroll").scrollTo({top:0, behavior:"smooth"});
});
document.getElementById("models-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "models-overlay" || e.target.id === "models-scroll"){
    document.getElementById("models-overlay").classList.remove("active");
  }
});

// ---------- Obsidian catalog (button under the rarity summary in Модели) ----------
// Shows every Obsidian card across ALL players -- name + number -- styled the same way
// the profile grid shows a player's own cards (rarity border + OBSIDIAN tag + drop
// number), plus the model-name-tag from the models grid so the custom name is visible
// without opening each card.
let obsidianCards = [];

function renderObsidianGrid(){
  const grid = document.getElementById("obsidian-grid");
  if(obsidianCards.length === 0){
    grid.innerHTML = `<div class="empty-state">Пока никто не создал Obsidian-карту</div>`;
    return;
  }
  grid.innerHTML = obsidianCards.map(c => `
    <div class="grid-item rarity-border-diamond obsidian-border" data-src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}">
      <div class="rarity-tag obsidian">OBSIDIAN</div>
      <div class="drop-number">#${c.drop_number}</div>
      <div class="model-name-tag">${capName(c.name) || ""}</div>
    </div>
  `).join("");
  grid.querySelectorAll(".grid-item").forEach((el, idx)=>{
    el.addEventListener("click", ()=>openZoom(obsidianCards, idx));
  });
}

async function openObsidianCatalog(){
  const res = await api("/obsidian/list", {});
  // custom_name mirrors .name here -- added so the shared zoom/renderZoom() logic
  // (which everywhere else checks c.custom_name to decide the Obsidian border/tag)
  // recognizes these as Obsidian without special-casing this one list.
  obsidianCards = (res.cards || []).map(c => ({...c, custom_name: c.name}));
  document.getElementById("obsidian-count").textContent = obsidianCards.length;
  renderObsidianGrid();
  document.getElementById("obsidian-overlay").classList.add("active");
}
document.getElementById("obsidian-close").addEventListener("click", ()=>{
  document.getElementById("obsidian-overlay").classList.remove("active");
});
document.getElementById("obsidian-scroll").addEventListener("scroll", (e)=>{
  const btn = document.getElementById("obsidian-scroll-top");
  btn.classList.toggle("visible", e.target.scrollTop > 300);
});
document.getElementById("obsidian-scroll-top").addEventListener("click", ()=>{
  document.getElementById("obsidian-scroll").scrollTo({top:0, behavior:"smooth"});
});
document.getElementById("obsidian-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "obsidian-overlay" || e.target.id === "obsidian-scroll"){
    document.getElementById("obsidian-overlay").classList.remove("active");
  }
});

// ---------- Leaderboard ("Топы") ----------
let topsPlayers = [];
let topsMetric = "gems"; // "cards" | "gems" | "diamond"

function renderTops(){
  const list = document.getElementById("tops-list");
  if(topsPlayers.length === 0){
    list.innerHTML = `<div class="empty-state">Пока никто не сфармил ни одной карточки</div>`;
    return;
  }
  const sorted = [...topsPlayers].sort((a, b) => {
    if(topsMetric === "gems") return b.current_gems - a.current_gems;
    if(topsMetric === "diamond") return b.diamond_cards - a.diamond_cards;
    return b.total_cards - a.total_cards;
  }).slice(0, 10);
  list.innerHTML = sorted.map((p, i) => `
    <div class="tops-row">
      <div class="tops-rank ${i===0?'top1':i===1?'top2':i===2?'top3':''}">${i+1}</div>
      <div class="tops-avatar">${p.photo_url ? `<img src="${p.photo_url}" alt="">` : (maskedName(p.username) || p.first_name || "?")[0].toUpperCase()}</div>
      <div class="tops-name">${maskedName(p.username) || p.first_name || "Игрок"}</div>
      <div class="tops-count">${topsMetric === "gems" ? `<img src="/static/icons/diamond.png" alt="">${p.current_gems}` : topsMetric === "diamond" ? `<img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">${p.diamond_cards}` : p.total_cards}</div>
    </div>
  `).join("");
}

document.querySelectorAll(".tops-switch-btn").forEach(btn=>{
  btn.addEventListener("click", ()=>{
    if(btn.dataset.metric === topsMetric) return;
    topsMetric = btn.dataset.metric;
    document.querySelectorAll(".tops-switch-btn").forEach(b=>{
      b.classList.toggle("active", b.dataset.metric === topsMetric);
    });
    renderTops();
  });
});

document.getElementById("tops-btn").addEventListener("click", async ()=>{
  try{
    const res = await fetch(API_BASE + "/leaderboard");
    const data = await res.json();
    topsPlayers = data.players || [];
    renderTops();
    document.getElementById("tops-overlay").classList.add("active");
  }catch(e){
    toast("Не удалось загрузить топы: " + e.message);
  }
});
document.getElementById("tops-close").addEventListener("click", ()=>{
  document.getElementById("tops-overlay").classList.remove("active");
});
document.getElementById("tops-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "tops-overlay"){
    document.getElementById("tops-overlay").classList.remove("active");
  }
});

// ---------- Farm animation: a 10s "drawing" suspense with a progress bar and cycling art ----------
let farmCycleTimer = null;
let farmProgressTimer = null;

const FARM_DEFAULT_HINT = '<span class="diamond">DIAMOND 0.5%</span> · <span class="platinum">PLATINUM 4%</span> · <span class="gold">GOLD 16.5%</span><br><span class="silver">SILVER 29%</span> · <span class="bronze">BRONZE 50%</span>';
// Preloads each image url in the browser's own HTTP cache before the cycling animation
// touches it, so a cold cache (e.g. right after a CARD_IMG_VERSION bump invalidates
// every card image at once) can never stall a cycle frame on a live network fetch --
// each image gets at most perImageTimeoutMs to load before we give up on it and move on,
// so one slow/broken image can't hang the whole farm.
function preloadImages(urls, perImageTimeoutMs){
  return Promise.all(urls.map(url => new Promise(resolve=>{
    let done = false;
    const finish = ()=>{ if(!done){ done = true; resolve(); } };
    const img = new Image();
    img.onload = finish;
    img.onerror = finish;
    img.src = url;
    setTimeout(finish, perImageTimeoutMs);
  })));
}

function startFarmAnimation(cards, hintHTML){
  const stage = document.getElementById("farm-stage");
  const progressWrap = document.getElementById("farm-progress-wrap");
  const progressBar = document.getElementById("farm-progress-bar");
  const nameEl0 = document.getElementById("farm-result-name");
  nameEl0.innerHTML = hintHTML || FARM_DEFAULT_HINT;
  nameEl0.classList.add("chance-hint");
  document.getElementById("farm-result-number").textContent = "";
  const rarityEl0 = document.getElementById("farm-result-rarity");
  rarityEl0.textContent = "";
  rarityEl0.className = "";
  progressWrap.classList.add("active");
  progressBar.style.width = "0%";

  // Shuffle a COPY each run (Fisher-Yates) so the spin shows a different, random order of
  // cards every time you farm — previously it always cycled the catalog in the exact same
  // fixed order from the start, which looked like it kept showing "the same cards".
  const pool = cards && cards.length ? [...cards] : [];
  for(let i = pool.length - 1; i > 0; i--){
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }

  const duration = 3000;
  const frameMs = 180;
  // Only as many frames as the animation can actually show in `duration` (plus a small
  // buffer) need to be preloaded -- no point fetching the whole multi-hundred-card catalog
  // up front.
  const frameCount = Math.min(pool.length, Math.ceil(duration / frameMs) + 2);
  const framePool = pool.slice(0, frameCount);
  const frameUrls = framePool.map(c => `/static/cards/${c.filename}?v=${CARD_IMG_VERSION}`);

  clearInterval(farmCycleTimer);
  if(framePool.length){
    stage.innerHTML = `<img id="farm-cycle-img" src="${frameUrls[0]}" alt="">`;
  }

  let cycleIdx = 0;
  const start = Date.now();
  preloadImages(frameUrls, 400).then(()=>{
    if(framePool.length > 1){
      farmCycleTimer = setInterval(()=>{
        cycleIdx = (cycleIdx + 1) % framePool.length;
        const img = document.getElementById("farm-cycle-img");
        if(img) img.src = frameUrls[cycleIdx];
      }, frameMs);
    }
  });

  return new Promise(resolve=>{
    clearInterval(farmProgressTimer);
    farmProgressTimer = setInterval(()=>{
      const elapsed = Date.now() - start;
      const pct = Math.min(100, (elapsed / duration) * 100);
      progressBar.style.width = pct + "%";
      if(elapsed >= duration){
        clearInterval(farmProgressTimer);
        clearInterval(farmCycleTimer);
        progressWrap.classList.remove("active");
        resolve();
      }
    }, 100);
  });
}

let farmRequestSeq = 0;
document.getElementById("farm-btn").addEventListener("click", async ()=>{
  const btn = document.getElementById("farm-btn");
  if(btn.disabled) return;

  const currentGems = gemsBalance;
  if(currentGems < FARM_COST_GEMS){
    notEnoughGemsToast();
    return;
  }

  btn.disabled = true;
  const mySeq = ++farmRequestSeq;
  const hint = document.getElementById("farm-hint");
  try{
    const cards = await ensureAllCards();
    await startFarmAnimation(cards);
    if(mySeq !== farmRequestSeq) return; // a newer farm superseded this one — never show its stale result
    const card = await api("/farm");
    if(mySeq !== farmRequestSeq) return;
    const stage = document.getElementById("farm-stage");
    stage.innerHTML = `<img class="rarity-border-${card.rarity}" src="/static/cards/${card.filename}?v=${CARD_IMG_VERSION}" alt="${card.name||''}"><div class="rarity-tag ${card.rarity}">${card.rarity}</div><div class="drop-number">#${card.farm_number}</div>`;
    const nameEl = document.getElementById("farm-result-name");
    nameEl.classList.remove("chance-hint");
    nameEl.textContent = card.name || "Новая картинка!";
    document.getElementById("farm-result-number").textContent = `№${card.farm_number}`;
    const rarityEl = document.getElementById("farm-result-rarity");
    rarityEl.textContent = card.rarity || "";
    rarityEl.className = card.rarity || "";
    document.getElementById("total-farmed").textContent = card.total_farmed;
    setGemsDisplay(card.gems);
    tg?.HapticFeedback?.notificationOccurred("success");
    // The just-farmed card isn't in the local `inventory` array yet (the /farm response
    // only carries the new card's own fields, not a full inventory row) -- without a
    // refresh it silently can't be staked in PvP (or seen anywhere else) until the next
    // full profile load. Same fix already applied for the PvP-win case above.
    api("/profile").then(data => {
      inventory = data.inventory;
      sortInventoryAndRender();
    }).catch(()=>{ /* non-critical -- next profile visit will pick it up anyway */ });
  }catch(e){
    if(mySeq !== farmRequestSeq) return;
    document.getElementById("farm-progress-wrap").classList.remove("active");
    clearInterval(farmCycleTimer);
    clearInterval(farmProgressTimer);
    if(String(e.message).includes("not enough gems")){
      notEnoughGemsToast();
    }else{
      toast("Ошибка фарма: " + e.message);
    }
  }finally{
    if(mySeq === farmRequestSeq) btn.disabled = false;
    hint.textContent = "";
  }
});

// ---------- Profile ----------
async function loadProfile(){
  try{
    const auth = await api("/auth");
    isAdminUser = !!auth.is_admin;
    document.getElementById("profile-name").textContent = auth.first_name || auth.username || "Игрок";
    // @username line intentionally removed from the profile header for every player --
    // only the display name (first_name, falling back to username) is shown.
    document.getElementById("profile-username").textContent = "";
    const avatarEl = document.getElementById("profile-avatar");
    if(auth.photo_url){
      avatarEl.innerHTML = `<img src="${auth.photo_url}" alt="">`;
    }else{
      avatarEl.textContent = (auth.first_name||auth.username||"?")[0].toUpperCase();
    }
    document.getElementById("stat-refs").textContent = auth.referrals || 0;
    setGemsDisplay(auth.gems || 0);
    document.getElementById("stat-income").textContent = auth.daily_bonus_amount || 25;
    daysUntilNextIncome = auth.daily_bonus_days_until_next ?? null;
    applyPlayerRank(auth.player_rank);

    const data = await api("/profile");
    inventory = data.inventory;
    document.getElementById("stat-total").textContent = inventory.length;

    sortInventoryAndRender();
  }catch(e){
    toast("Не удалось загрузить профиль: " + e.message);
  }
}

// ---------- Collection sort (rank <-> number, cycling one button) ----------
const PROFILE_SORT_STATES = ["rarity_desc", "rarity_asc", "number_asc", "number_desc"];
const PROFILE_SORT_LABELS = {rarity_desc: "Ранг ↓", rarity_asc: "Ранг ↑", number_asc: "Номер ↑", number_desc: "Номер ↓"};
let profileSortStateIndex = 0;

function setProfileSortState(nextIndex){
  profileSortStateIndex = nextIndex;
  const state = PROFILE_SORT_STATES[profileSortStateIndex];
  const isRarity = state.startsWith("rarity");
  const rarityBtn = document.getElementById("profile-sort-rarity-btn");
  const numberBtn = document.getElementById("profile-sort-number-btn");
  rarityBtn.classList.toggle("active", isRarity);
  numberBtn.classList.toggle("active", !isRarity);
  rarityBtn.textContent = isRarity ? PROFILE_SORT_LABELS[state] : "Ранг";
  numberBtn.textContent = !isRarity ? PROFILE_SORT_LABELS[state] : "Номер";
  sortInventoryAndRender();
}

document.getElementById("profile-sort-rarity-btn").addEventListener("click", ()=>{
  const state = PROFILE_SORT_STATES[profileSortStateIndex];
  setProfileSortState(state === "rarity_desc" ? 1 : 0);
});
document.getElementById("profile-sort-number-btn").addEventListener("click", ()=>{
  const state = PROFILE_SORT_STATES[profileSortStateIndex];
  setProfileSortState(state === "number_asc" ? 3 : 2);
});

function sortInventoryAndRender(){
  const state = PROFILE_SORT_STATES[profileSortStateIndex];
  inventory.sort((a, b) => {
    if(state === "number_asc") return a.drop_number - b.drop_number;
    if(state === "number_desc") return b.drop_number - a.drop_number;
    const ra = RARITY_ORDER[a.rarity] ?? 4, rb = RARITY_ORDER[b.rarity] ?? 4;
    if(ra !== rb) return state === "rarity_asc" ? rb - ra : ra - rb;
    return b.user_card_id - a.user_card_id;
  });
  // Стена: pinned cards float to the very top of the SAME grid, in the order they
  // were pinned — everything else keeps the active sort right below them.
  const pinned = inventory.filter(c => c.pinned_at)
    .sort((a, b) => (a.pinned_at < b.pinned_at ? -1 : a.pinned_at > b.pinned_at ? 1 : 0));
  const rest = inventory.filter(c => !c.pinned_at);
  inventory = [...pinned, ...rest];
  renderProfileGrid();
}

let profileRarityFilter = null; // null = show all; otherwise one of the 5 rarities

document.getElementById("profile-rarity-filters").addEventListener("click", (e)=>{
  const btn = e.target.closest(".pvp-picker-rarity-btn");
  if(!btn) return;
  const rarity = btn.dataset.rarity;
  profileRarityFilter = profileRarityFilter === rarity ? null : rarity; // tap again to clear
  document.querySelectorAll("#profile-rarity-filters .pvp-picker-rarity-btn").forEach(b=>{
    b.classList.toggle("active", b.dataset.rarity === profileRarityFilter);
  });
  renderProfileGrid();
});

function renderProfileGrid(){
  const grid = document.getElementById("profile-grid");
  const items = profileRarityFilter ? inventory.filter(c => c.rarity === profileRarityFilter) : inventory;
  if(items.length === 0){
    grid.innerHTML = "";
    document.getElementById("empty-hint")?.remove();
    const emptyText = profileRarityFilter ? "Нет карт такой редкости" : "Пока пусто — иди фармить 🌱";
    grid.insertAdjacentHTML("afterend", `<div class="empty-state" id="empty-hint">${emptyText}</div>`);
    return;
  }
  document.getElementById("empty-hint")?.remove();
  grid.innerHTML = items.map(c => `
    <div class="grid-item rarity-border-${c.rarity}${c.custom_name ? " obsidian-border" : ""}${c.pinned_at ? " pinned" : ""}" data-uc="${c.user_card_id}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}">
      <div class="rarity-tag ${c.custom_name ? "obsidian" : c.rarity}">${c.custom_name ? "OBSIDIAN" : c.rarity}</div>
      <div class="drop-number">#${c.drop_number}</div>
      ${c.pinned_at ? `<div class="wall-pin-tag"><svg viewBox="0 0 24 24" width="9" height="9"><rect x="7" y="2" width="10" height="2.2" rx="1" fill="#fff"/><rect x="10.5" y="4" width="3" height="12" fill="#fff"/><polygon points="10.5,16 13.5,16 12,21" fill="#fff"/></svg></div>` : ""}
      ${c.listed_price ? `<div class="profile-sale-tag"><img src="/static/icons/diamond.png" alt=""><span>${c.listed_price}</span></div>` : ""}
      ${c.swap_listed ? `<div class="profile-swap-tag">Обмен</div>` : ""}
      ${c.staked_at ? `<div class="profile-stake-tag">${formatStakedDuration(c.staked_at)}</div>` : ""}
      ${c.pvp_round_id ? `<div class="profile-pvp-tag">PvP</div>` : ""}
      ${c.in_giveaway ? `<div class="profile-giveaway-tag">🎁</div>` : ""}
    </div>
  `).join("");

  grid.querySelectorAll(".grid-item").forEach(el=>{
    el.addEventListener("click", ()=>openModal(parseInt(el.dataset.uc)));
  });
}


// ---------- Wall (Стена) — small button next to "Коллекция"; opens a modal where any
// owned card can be tapped to pin/unpin it. Pinned state lives directly on each
// inventory card (c.pinned_at, from get_inventory()) — no separate block, no separate
// fetch: pinned cards just float to the top of the SAME collection grid (see
// sortInventoryAndRender/renderProfileGrid above).
const WALL_MAX_CARDS = 9;

function renderWallModal(){
  const pinnedCount = inventory.filter(c => c.pinned_at).length;
  document.getElementById("wall-sub").textContent =
    `Нажми на карту, чтобы закрепить или снять со Стены (${pinnedCount}/${WALL_MAX_CARDS})`;
  const grid = document.getElementById("wall-grid");
  if(!inventory.length){
    grid.innerHTML = `<div class="empty-state">Пока пусто — иди фармить</div>`;
    return;
  }
  grid.innerHTML = inventory.map(c => `
    <div class="grid-item rarity-border-${c.rarity}${c.pinned_at ? " pinned" : ""}" data-uc="${c.user_card_id}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}">
      <div class="rarity-tag ${c.rarity}">${c.rarity}</div>
      <div class="drop-number">#${c.drop_number}</div>
      ${c.pinned_at ? `<div class="wall-pin-tag"><svg viewBox="0 0 24 24" width="9" height="9"><rect x="7" y="2" width="10" height="2.2" rx="1" fill="#fff"/><rect x="10.5" y="4" width="3" height="12" fill="#fff"/><polygon points="10.5,16 13.5,16 12,21" fill="#fff"/></svg></div>` : ""}
    </div>
  `).join("");
  grid.querySelectorAll(".grid-item").forEach(el=>{
    el.addEventListener("click", async ()=>{
      const ucId = parseInt(el.dataset.uc);
      const card = inventory.find(c => c.user_card_id === ucId);
      if(!card) return;
      const isPinned = !!card.pinned_at;
      try{
        if(isPinned){
          await api("/wall/unpin", { user_card_id: ucId });
          card.pinned_at = null;
        }else{
          if(pinnedCount >= WALL_MAX_CARDS){
            toast(`Стена заполнена (макс. ${WALL_MAX_CARDS})`);
            return;
          }
          await api("/wall/pin", { user_card_id: ucId });
          card.pinned_at = new Date().toISOString();
        }
        renderWallModal();
        sortInventoryAndRender();
      }catch(err){
        toast("Не получилось: " + err.message);
      }
    });
  });
}

document.getElementById("wall-open-btn").addEventListener("click", ()=>{
  document.getElementById("wall-overlay").classList.add("active");
  renderWallModal();
});
document.getElementById("wall-close").addEventListener("click", ()=>{
  document.getElementById("wall-overlay").classList.remove("active");
});
document.getElementById("wall-close-bottom").addEventListener("click", ()=>{
  document.getElementById("wall-overlay").classList.remove("active");
});

// ---------- Collections ("Альбомы") -- opened from the "Коллекция" button in
// Профиль. List view -> tap a collection -> detail view (per-slot grid). Placing a
// card is a pure completion check server-side -- the card itself is never
// touched/locked, so this never needs to refetch `inventory`.
let currentCollectionId = null;

async function renderCollectionsList(){
  const list = document.getElementById("collections-list");
  list.innerHTML = `<div class="empty-state">Загружаю...</div>`;
  let collections;
  try{
    const res = await api("/collections");
    collections = res.collections;
    if(typeof res.gems === "number") setGemsDisplay(res.gems);
  }catch(err){
    list.innerHTML = `<div class="empty-state">Не удалось загрузить: ${err.message}</div>`;
    return;
  }
  if(!collections.length){
    list.innerHTML = `<div class="empty-state">Пока нет ни одной коллекции</div>`;
    return;
  }
  list.innerHTML = collections.map(c => {
    const pct = c.total ? Math.round((c.placed / c.total) * 100) : 0;
    return `
      <div class="collection-row${c.completed ? " completed" : ""}" data-id="${c.id}">
        <div class="icon">${c.icon_image ? `<img src="/static/cards/${c.icon_image}?v=${CARD_IMG_VERSION}" alt="">` : (c.icon || "📁")}</div>
        <div class="info">
          <div class="name">${c.name}${c.completed ? '<span class="badge">ГОТОВО</span>' : ""}</div>
          <div class="progress">${c.placed}/${c.total} собрано</div>
          <div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>
        </div>
        <button class="who-btn" data-id="${c.id}" title="Собрали коллекцию">${c.completers_count || 0}</button>
      </div>`;
  }).join("");
  list.querySelectorAll(".collection-row").forEach(el=>{
    el.addEventListener("click", ()=> openCollectionDetail(parseInt(el.dataset.id)));
  });
  list.querySelectorAll(".who-btn").forEach(btn=>{
    btn.addEventListener("click", (e)=>{
      e.stopPropagation();
      openCollectionCompleters(parseInt(btn.dataset.id));
    });
  });
}

async function openCollectionCompleters(collectionId){
  document.getElementById("completers-overlay").classList.add("active");
  const list = document.getElementById("completers-list");
  list.innerHTML = `<div class="empty-state">Загружаю...</div>`;
  let completers;
  try{
    ({ completers } = await api("/collections/completers", { collection_id: collectionId }));
  }catch(err){
    list.innerHTML = `<div class="empty-state">Не удалось загрузить: ${err.message}</div>`;
    return;
  }
  if(!completers.length){
    list.innerHTML = `<div class="empty-state">Пока никто не собрал эту коллекцию</div>`;
    return;
  }
  list.innerHTML = completers.map(p=>{
    const name = p.username || p.first_name || "Игрок";
    const d = new Date(p.completed_at);
    const dateStr = d.toLocaleDateString("ru-RU");
    return `<div class="completer-row"><span class="n">${name}</span><span class="d">${dateStr}</span></div>`;
  }).join("");
}
document.getElementById("completers-close").addEventListener("click", ()=>{
  document.getElementById("completers-overlay").classList.remove("active");
});
document.getElementById("completers-close-bottom").addEventListener("click", ()=>{
  document.getElementById("completers-overlay").classList.remove("active");
});
document.getElementById("completers-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "completers-overlay"){
    document.getElementById("completers-overlay").classList.remove("active");
  }
});

async function openCollectionDetail(collectionId){
  currentCollectionId = collectionId;
  document.getElementById("collections-overlay").classList.remove("active");
  document.getElementById("collection-detail-overlay").classList.add("active");
  await renderCollectionDetail();
}

async function renderCollectionDetail(){
  const grid = document.getElementById("collection-detail-grid");
  const title = document.getElementById("collection-detail-title");
  const progress = document.getElementById("collection-detail-progress");
  grid.innerHTML = `<div class="empty-state">Загружаю...</div>`;
  let detail;
  try{
    detail = await api("/collections/detail", { collection_id: currentCollectionId });
    if(typeof detail.gems === "number") setGemsDisplay(detail.gems);
  }catch(err){
    grid.innerHTML = `<div class="empty-state">Не удалось загрузить: ${err.message}</div>`;
    return;
  }
  const placedCount = detail.cards.filter(c => c.placed).length;
  title.innerHTML = `${detail.icon || ""} ${detail.name}${detail.completed ? ' <span class="badge">ГОТОВО</span>' : ""}`;
  progress.textContent = `${placedCount}/${detail.cards.length} собрано`;
  grid.innerHTML = detail.cards.map(c => {
    const state = c.placed ? "placed" : (c.owned ? "owned" : "not-owned");
    return `
      <div class="grid-item collection-slot ${state}" data-card-id="${c.card_id}">
        <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name||''}">
        <div class="collection-slot-plus">+</div>
      </div>`;
  }).join("");
  grid.querySelectorAll(".collection-slot.owned:not(.placed)").forEach(el=>{
    el.addEventListener("click", async ()=>{
      const cardId = parseInt(el.dataset.cardId);
      try{
        const res = await api("/collections/place", { collection_id: currentCollectionId, card_id: cardId });
        if(res.newly_completed){
          toast(`🏆 Коллекция «${detail.name}» собрана! +100 гемов`);
          if(typeof res.gems === "number") setGemsDisplay(res.gems);
        }
        await renderCollectionDetail();
      }catch(err){
        toast("Не получилось: " + err.message);
      }
    });
  });
  // Tapping a still-missing slot shows what to look for -- big, rarity-colored,
  // named -- reusing the same zoom viewer "Все модели" uses instead of leaving
  // these small greyed-out thumbnails as dead taps.
  grid.querySelectorAll(".collection-slot.not-owned").forEach(el=>{
    el.addEventListener("click", ()=>{
      const cardId = parseInt(el.dataset.cardId);
      const idx = detail.cards.findIndex(c => c.card_id === cardId);
      if(idx >= 0) openZoom(detail.cards, idx);
    });
  });
}

async function renderAchievementsList(){
  const list = document.getElementById("achievements-list");
  list.innerHTML = `<div class="empty-state">Загружаю...</div>`;
  let collections;
  try{
    ({ collections } = await api("/collections"));
  }catch(err){
    list.innerHTML = `<div class="empty-state">Не удалось загрузить: ${err.message}</div>`;
    return;
  }
  if(!collections.length){
    list.innerHTML = `<div class="empty-state">Пока нет ни одной ачивки</div>`;
    return;
  }
  list.innerHTML = collections.map((c, i) => {
    const pct = c.total ? Math.round((c.placed / c.total) * 100) : 0;
    const iconInner = c.icon_image
      ? `<img src="/static/cards/${c.icon_image}?v=${CARD_IMG_VERSION}" alt="">`
      : (c.icon || "🏆");
    return `
      <div class="achv-card${c.completed ? " completed" : ""}" data-id="${c.id}">
        <div class="achv-iconbox${c.completed ? " unlocked" : ""}">${iconInner}</div>
        <div class="achv-body">
          <div class="achv-name">${c.name}${c.completed ? '<span class="achv-done-badge">ГОТОВО</span>' : ""}</div>
          <div class="achv-desc">Собери все карты коллекции</div>
          <div class="achv-progress-row">
            <div class="achv-bar"><div class="achv-bar-fill" style="width:${pct}%"></div></div>
            <div class="achv-progress-text">${c.placed}/${c.total}</div>
          </div>
        </div>
      </div>`;
  }).join("");
  list.querySelectorAll(".achv-card").forEach(el=>{
    el.addEventListener("click", ()=>{
      document.getElementById("achievements-overlay").classList.remove("active");
      openCollectionDetail(parseInt(el.dataset.id));
    });
  });
}
function openAchievements(){
  document.getElementById("achievements-overlay").classList.add("active");
  renderAchievementsList();
}
document.getElementById("achievements-close").addEventListener("click", ()=>{
  document.getElementById("achievements-overlay").classList.remove("active");
});
document.getElementById("achievements-close-bottom").addEventListener("click", ()=>{
  document.getElementById("achievements-overlay").classList.remove("active");
});
document.getElementById("achievements-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "achievements-overlay"){
    document.getElementById("achievements-overlay").classList.remove("active");
  }
});
document.getElementById("collections-open-btn").addEventListener("click", ()=>{
  document.getElementById("collections-overlay").classList.add("active");
  renderCollectionsList();
});
document.getElementById("collections-close").addEventListener("click", ()=>{
  document.getElementById("collections-overlay").classList.remove("active");
});
document.getElementById("collections-close-bottom").addEventListener("click", ()=>{
  document.getElementById("collections-overlay").classList.remove("active");
});
document.getElementById("collection-detail-close").addEventListener("click", ()=>{
  document.getElementById("collection-detail-overlay").classList.remove("active");
});
document.getElementById("collection-detail-close-bottom").addEventListener("click", ()=>{
  document.getElementById("collection-detail-overlay").classList.remove("active");
  document.getElementById("collections-overlay").classList.add("active");
  renderCollectionsList();
});

let modalIndex = -1;

function openModal(userCardId){
  const index = inventory.findIndex(c => c.user_card_id === userCardId);
  if(index === -1) return;
  openModalAt(index);
}

function openModalAt(index){
  if(index < 0 || index >= inventory.length) return;
  // database.get_inventory() includes one real user_cards.id per card group (the most
  // recently obtained copy) specifically so the modal can share a concrete owned row.
  modalIndex = index;
  const item = inventory[index];
  selectedUserCardId = item.user_card_id;
  selectedItem = item;
  const modalImgEl = document.getElementById("modal-img");
  modalImgEl.src = `/static/cards/${item.filename}?v=${CARD_IMG_VERSION}`;
  setRarityBorder(modalImgEl, item.rarity);
  const isObsidian = !!item.custom_name;
  modalImgEl.classList.toggle("obsidian-border", isObsidian);
  document.getElementById("modal-img-wrap").classList.toggle("obsidian", isObsidian);
  document.getElementById("modal-name").textContent = `${capName(item.name) || "Без названия"} #${item.drop_number}`;
  const modalRarityBadge = document.getElementById("modal-rarity-tag");
  modalRarityBadge.textContent = isObsidian ? "OBSIDIAN" : item.rarity;
  modalRarityBadge.className = `modal-rarity-tag ${isObsidian ? "obsidian" : item.rarity}`;
  document.getElementById("modal-drop-number").textContent = `#${item.drop_number}`;
  document.getElementById("modal-overlay").classList.add("active");
  document.getElementById("modal-card").dataset.filename = item.filename;
  updateTransferButton(item);
  updateSellButton(item);
  updateSwapButton(item);
  updateStakeButton(item);
  updateCraftButton(item);
  updateNumberButton(item);
  updateCustomButton(item);
}

// Mirrors database.py's TRANSFER_FEE_GEMS — client-side copy for the fee pre-check +
// confirm/toast text, and to decide disabled state the same way transfer_card_to()
// validates server-side (busy: listed/swap_listed/staked/pvp_round/in_giveaway).
const TRANSFER_FEE_GEMS = 25;
function updateTransferButton(item){
  const btn = document.getElementById("modal-transfer");
  if(!btn) return;
  const busy = !!(item.staked_at || item.listed_price || item.swap_listed || item.pvp_round_id || item.pinned_at || item.in_giveaway);
  btn.disabled = busy;
  btn.classList.toggle("disabled", busy);
  btn.textContent = busy ? "Недоступно" : "Передать";
}

function updateCustomButton(item){
  const btn = document.getElementById("modal-custom");
  if(!btn) return;
  const busy = !!(item.staked_at || item.listed_price || item.swap_listed || item.pvp_round_id || item.pinned_at || item.in_giveaway);
  btn.disabled = busy;
  btn.classList.toggle("disabled", busy);
  btn.textContent = "Кастом";
}

// Player rank (time-played tier, NOT card rarity) — colors the avatar ring + profile
// card border and shows a small badge next to the name. Mirrors database.py's
// PLAYER_RANK_COLORS.
const PLAYER_RANK_COLORS = { bronze: "#cd7f32", silver: "#9ca3af", gold: "#facc15", platinum: "#a78bfa", diamond: "#ff2fb0" };
let currentPlayerRank = "bronze";
function applyPlayerRank(rank){
  currentPlayerRank = rank || "bronze";
  const color = PLAYER_RANK_COLORS[currentPlayerRank] || PLAYER_RANK_COLORS.bronze;
  const badge = document.getElementById("profile-rank-badge");
  badge.textContent = currentPlayerRank.toUpperCase();
  badge.style.color = color;
  badge.style.background = color + "22";
  document.getElementById("profile-avatar-ring").style.background = color;
  document.getElementById("profile-card").style.borderColor = color;
  document.getElementById("profile-card").style.boxShadow = `0 0 20px -6px ${color}66`;
  ["stat-streak-tile", "stat-cards-tile", "stat-income-tile", "stat-refs-tile", "profile-info-btn"].forEach(id=>{
    const el = document.getElementById(id);
    if(el) el.style.borderColor = color;
  });
}

// Mirrors database.py's RANK_TIERS order — used to compare rows against the
// player's current rank so a "Купить" button only shows for ranks above it.
const RANK_TIERS = ["bronze", "silver", "gold", "platinum", "diamond"];
function openRankInfo(){
  const currentTierIdx = RANK_TIERS.indexOf(currentPlayerRank);
  document.querySelectorAll("#rank-info-rows .rank-info-row").forEach(row=>{
    const isCurrent = row.dataset.rank === currentPlayerRank;
    row.classList.toggle("current", isCurrent);
    let tag = row.querySelector(".current-tag");
    if(isCurrent && !tag){
      tag = document.createElement("span");
      tag.className = "current-tag";
      tag.textContent = "ты тут";
      row.appendChild(tag);
    }else if(!isCurrent && tag){
      tag.remove();
    }
    const buyBtn = row.querySelector(".rank-buy-btn");
    if(buyBtn){
      const rowTierIdx = RANK_TIERS.indexOf(row.dataset.rank);
      buyBtn.style.display = rowTierIdx <= currentTierIdx ? "none" : "";
    }
  });
  document.getElementById("rank-info-overlay").classList.add("active");
}
document.getElementById("rank-info-rows").addEventListener("click", async (e)=>{
  const btn = e.target.closest(".rank-buy-btn");
  if(!btn) return;
  const rank = btn.dataset.rank;
  try{
    const res = await api("/rank/invoice", { rank });
    if(tg?.openInvoice){
      tg.openInvoice(res.invoice_link, (status)=>{
        if(status === "paid"){
          toast("Оплачено! Обновляем ранг...");
          document.getElementById("rank-info-overlay").classList.remove("active");
          setTimeout(refreshGems, 1500);
        }else if(status === "failed"){
          toast("Платёж не прошёл");
        }
      });
    }else{
      window.open(res.invoice_link, "_blank");
      toast("Открой ссылку оплаты и вернись сюда");
    }
  }catch(err){
    toast("Не удалось создать счёт: " + err.message);
  }
});
document.getElementById("profile-avatar-ring").addEventListener("click", openAchievements);
document.getElementById("profile-rank-badge").addEventListener("click", openRankInfo);
document.getElementById("rank-info-close").addEventListener("click", ()=>{
  document.getElementById("rank-info-overlay").classList.remove("active");
});
document.getElementById("rank-info-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "rank-info-overlay"){
    document.getElementById("rank-info-overlay").classList.remove("active");
  }
});

// Mirrors database.py's ALLOWED_AUCTION_NUMBERS (VANITY_NUMBERS + 1..LOW_NUMBER_SEED_UP_TO)
// — client-side copy just to decide whether "Номер" mode shows up on this card. Was
// 1..25, extended to 1..100 by request (matches LOW_NUMBER_SEED_UP_TO = 100).
const RARE_NUMBERS_SET = new Set([
  ...Array.from({length: 100}, (_, i) => i + 1),
  11, 22, 33, 44, 55, 66, 67, 69, 77, 88, 99,
  100, 101,
  111, 200, 222, 300, 333, 400, 444, 500, 555, 600, 666,
  700, 777, 800, 888, 900, 999, 1000, 1001,
  1111, 2000, 2222, 3000, 3333, 4000, 4444, 5000, 5555,
  6000, 6666, 7000, 7777, 8000, 8888, 9000, 9999, 10000,
]);

function updateNumberButton(item){
  // "Номер" only ever shows up when the card is sitting on a RARE_NUMBERS_SET number
  // and isn't busy (listed/staked/swapped/PvP/pinned/giveaway) -- otherwise it's just
  // hidden, since there's nothing to do with a plain number.
  const btn = document.getElementById("modal-number");
  const busy = !!(item.listed_price || item.swap_listed || item.staked_at || item.pvp_round_id || item.pinned_at || item.in_giveaway);
  const numberMode = RARE_NUMBERS_SET.has(item.drop_number) && !busy;
  btn.style.display = numberMode ? "" : "none";
  btn.textContent = numberMode ? `Номер #${item.drop_number}` : "";
}

function updateCraftButton(item){
  const btn = document.getElementById("modal-craft");
  const busy = !!(item.staked_at || item.listed_price || item.swap_listed || item.pvp_round_id || item.pinned_at || item.in_giveaway);
  btn.disabled = busy;
  btn.classList.toggle("disabled", busy);
  if(busy){
    btn.textContent = item.in_giveaway ? "Разыгрывается в чате" : "Недоступно";
  }else{
    btn.textContent = "Крафт";
  }
}

function updateSellButton(item){
  const btn = document.getElementById("modal-sell");
  btn.classList.remove("active", "listed");
  btn.disabled = false;
  if(item.in_giveaway){
    btn.textContent = "Разыгрывается в чате";
    btn.disabled = true;
    return;
  }
  if(item.pinned_at){
    btn.textContent = "Закреплено на Стене";
    btn.disabled = true;
    return;
  }
  if(item.pvp_round_id){
    btn.textContent = "В игре (PvP)";
    btn.disabled = true;
    return;
  }
  if(item.staked_at){
    btn.textContent = "В стейкинге";
    return;
  }
  if(item.listed_price){
    btn.textContent = `Убрать с продажи (${item.listed_price})`;
    btn.classList.add("listed");
  }else{
    btn.textContent = "Продать";
    btn.classList.add("active");
  }
}

function updateSwapButton(item){
  const btn = document.getElementById("modal-swap");
  btn.classList.remove("active", "listed");
  btn.disabled = false;
  if(item.in_giveaway){
    btn.textContent = "Разыгрывается в чате";
    btn.disabled = true;
    return;
  }
  if(item.pinned_at){
    btn.textContent = "Закреплено на Стене";
    btn.disabled = true;
    return;
  }
  if(item.pvp_round_id){
    btn.textContent = "В игре (PvP)";
    btn.disabled = true;
    return;
  }
  if(item.staked_at){
    btn.textContent = "В стейкинге";
    return;
  }
  if(item.swap_listed){
    btn.textContent = "Снять с обмена";
    btn.classList.add("listed");
  }else{
    btn.textContent = "Обмен";
    btn.classList.add("active");
  }
}

function updateStakeButton(item){
  const btn = document.getElementById("modal-stake");
  btn.classList.remove("active", "staked");
  btn.disabled = false;
  if(item.in_giveaway){
    btn.textContent = "Разыгрывается в чате";
    btn.disabled = true;
    return;
  }
  if(item.pvp_round_id){
    btn.textContent = "В игре (PvP)";
    btn.disabled = true;
    return;
  }
  if(item.staked_at){
    btn.textContent = "Снять";
    btn.classList.add("staked");
  }else{
    btn.textContent = "Стейк";
    btn.classList.add("active");
  }
}

function formatStakedDuration(isoString){
  const stakedMs = new Date(isoString).getTime();
  const diffMin = Math.max(0, Math.floor((Date.now() - stakedMs) / 60000));
  if(diffMin < 60) return `${diffMin}м`;
  const hours = Math.floor(diffMin / 60);
  if(hours < 24) return `${hours}ч`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours ? `${days}д ${remHours}ч` : `${days}д`;
}

// Swipe left/right on the card to browse the rest of the collection.
let touchStartX = 0, touchStartY = 0;
const modalCardEl = document.getElementById("modal-card");
modalCardEl.addEventListener("touchstart", (e)=>{
  touchStartX = e.touches[0].clientX;
  touchStartY = e.touches[0].clientY;
}, { passive: true });
modalCardEl.addEventListener("touchend", (e)=>{
  if(inventory.length <= 1) return;
  const dx = e.changedTouches[0].clientX - touchStartX;
  const dy = e.changedTouches[0].clientY - touchStartY;
  if(Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)){
    if(dx < 0) openModalAt((modalIndex + 1) % inventory.length);
    else openModalAt((modalIndex - 1 + inventory.length) % inventory.length);
  }
}, { passive: true });

document.getElementById("modal-close").addEventListener("click", ()=>{
  document.getElementById("modal-overlay").classList.remove("active");
});

document.getElementById("modal-back").addEventListener("click", ()=>{
  document.getElementById("modal-overlay").classList.remove("active");
});

// Tap anywhere outside the card itself (the dark backdrop) closes the modal.
document.getElementById("modal-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "modal-overlay"){
    document.getElementById("modal-overlay").classList.remove("active");
  }
});

// Universal backdrop-click-to-close: ANY *-overlay div closes when the tap lands
// directly on the backdrop itself (not a child/card inside it) and it's currently
// open. Covers every modal in the app in one place instead of a handler per overlay
// -- including ones added later, no need to remember to wire this up each time.
document.addEventListener("click", (e)=>{
  if(e.target.id && e.target.id.endsWith("-overlay") && e.target.classList.contains("active")){
    e.target.classList.remove("active");
  }
});

const EXTRACT_NUMBER_COST_GEMS = 100; // mirrors database.py's EXTRACT_NUMBER_COST_GEMS
document.getElementById("modal-number").addEventListener("click", async ()=>{
  if(!selectedUserCardId || !selectedItem) return;
  const oldNumber = selectedItem.drop_number;
  if(gemsBalance < EXTRACT_NUMBER_COST_GEMS){
    toastHTML(`Не хватает гемов — извлечение номера стоит ${EXTRACT_NUMBER_COST_GEMS} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    return;
  }
  const extractConfirmed = await confirmAction(
    `Извлечь номер #${oldNumber} из карты за ${EXTRACT_NUMBER_COST_GEMS} гемов? Номер уйдёт в «Номера» (можно будет продать или поставить на другую карту), а эта карта получит новый номер — как будто её только что зафармили.`
  );
  if(!extractConfirmed) return;
  try{
    const res = await api("/numbers/extract", { user_card_id: selectedUserCardId });
    selectedItem.drop_number = res.new_number;
    selectedItem.number_override = res.new_number;
    updateNumberButton(selectedItem);
    document.getElementById("modal-name").textContent = `${capName(selectedItem.name) || "Без названия"} #${selectedItem.drop_number}`;
    document.getElementById("modal-drop-number").textContent = `#${selectedItem.drop_number}`;
    if(typeof res.gems === "number") setGemsDisplay(res.gems);
    sortInventoryAndRender();
    toast(`Номер #${oldNumber} извлечён за ${EXTRACT_NUMBER_COST_GEMS} гемов! Карте присвоен новый номер #${res.new_number}. Смотри «Номера» — там он теперь твой.`);
  }catch(e){
    if(String(e.message).includes("not enough gems")){
      toastHTML(`Не хватает гемов — извлечение номера стоит ${EXTRACT_NUMBER_COST_GEMS} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    }else{
      toast("Не получилось: " + e.message);
    }
  }
});

document.getElementById("modal-transfer").addEventListener("click", async ()=>{
  if(document.getElementById("modal-transfer").disabled) return;
  if(!selectedItem || !selectedUserCardId) return;
  if(selectedItem.in_giveaway){
    toast("Карта сейчас разыгрывается в чате");
    return;
  }
  if(selectedItem.pvp_round_id){
    toast("Карта сейчас участвует в PvP игре");
    return;
  }
  if(selectedItem.staked_at || selectedItem.listed_price || selectedItem.swap_listed){
    toast("Сначала снимите карту с продажи/обмена/стейкинга");
    return;
  }
  if(selectedItem.pinned_at){
    toast("Сначала открепите карту со Стены");
    return;
  }
  if(gemsBalance < TRANSFER_FEE_GEMS){
    toastHTML(`Не хватает гемов — передача карты стоит ${TRANSFER_FEE_GEMS} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    return;
  }
  const username = await promptText("Кому передать? (юзернейм)", "@username");
  if(!username) return;
  const transferConfirmed = await confirmAction(
    `Передать карту «${capName(selectedItem.name) || "Без названия"}» игроку @${username} за ${TRANSFER_FEE_GEMS} гемов? Отменить после передачи будет нельзя.`
  );
  if(!transferConfirmed) return;

  const transferUserCardId = selectedUserCardId;
  try{
    const res = await api("/transfer/to_username", { user_card_id: transferUserCardId, username });
    inventory = inventory.filter(c => c.user_card_id !== transferUserCardId);
    setGemsDisplay(gemsBalance - TRANSFER_FEE_GEMS);
    sortInventoryAndRender();
    document.getElementById("modal-overlay").classList.remove("active");
    toast(`Карта передана игроку ${res.to_name ? "@" + res.to_name : "@" + username}!`);
  }catch(e){
    const msg = String(e.message);
    if(msg.includes("not enough gems")){
      toastHTML(`Не хватает гемов — передача карты стоит ${TRANSFER_FEE_GEMS} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    }else if(msg.includes("ещё не запускал бота")){
      toast("Этот пользователь ещё не запускал бота");
    }else if(msg.includes("самому себе")){
      toast("Нельзя подарить карту самому себе");
    }else if(msg.includes("busy")){
      toast("Карта сейчас недоступна для передачи");
    }else{
      toast("Не получилось передать: " + msg);
    }
  }
});

document.getElementById("modal-sell").addEventListener("click", async ()=>{
  if(!selectedItem) return;
  if(selectedItem.in_giveaway){
    toast("Карта сейчас разыгрывается в чате");
    return;
  }
  if(selectedItem.pvp_round_id){
    toast("Карта сейчас участвует в PvP игре");
    return;
  }
  if(selectedItem.staked_at){
    toast("Сначала снимите карту со стейкинга");
    return;
  }
  if(selectedItem.listed_price){
    try{
      await api("/market/unlist", { user_card_id: selectedUserCardId });
      selectedItem.listed_price = null;
      updateSellButton(selectedItem);
      renderProfileGrid();
      toast("Снято с продажи");
    }catch(e){
      toast("Не получилось: " + e.message);
    }
  }else{
    const minPrice = minListingPrice(selectedItem.rarity);
    const price = await promptNumber(`Цена в гемах (минимум ${minPrice})`);
    if(!price) return;
    if(price < minPrice){
      toast(`Минимальная цена продажи ${minPrice} гемов`);
      return;
    }
    try{
      await api("/market/list", { user_card_id: selectedUserCardId, price_gems: price });
      selectedItem.listed_price = price;
      updateSellButton(selectedItem);
      renderProfileGrid();
      toast("Выставлено на рынок!");
      document.getElementById("modal-overlay").classList.remove("active");
    }catch(e){
      if(String(e.message).includes("minimum price")){
        toast(`Минимальная цена продажи ${minPrice} гемов`);
      }else{
        toast("Не получилось выставить: " + e.message);
      }
    }
  }
});

document.getElementById("modal-swap").addEventListener("click", async ()=>{
  if(!selectedItem) return;
  if(selectedItem.in_giveaway){
    toast("Карта сейчас разыгрывается в чате");
    return;
  }
  if(selectedItem.pvp_round_id){
    toast("Карта сейчас участвует в PvP игре");
    return;
  }
  if(selectedItem.staked_at){
    toast("Сначала снимите карту со стейкинга");
    return;
  }
  if(selectedItem.swap_listed){
    try{
      await api("/swap/unlist", { user_card_id: selectedUserCardId });
      selectedItem.swap_listed = 0;
      updateSwapButton(selectedItem);
      renderProfileGrid();
      toast("Снято с обмена");
    }catch(e){
      toast("Не получилось: " + e.message);
    }
  }else{
    try{
      await api("/swap/list", { user_card_id: selectedUserCardId });
      selectedItem.swap_listed = 1;
      updateSwapButton(selectedItem);
      renderProfileGrid();
      toast("Выставлено на обмен!");
      document.getElementById("modal-overlay").classList.remove("active");
    }catch(e){
      toast("Не получилось выставить: " + e.message);
    }
  }
});

// Mirrors database.py's STAKE_DAILY_RATES/MAX_STAKE_DAYS/MAX_STAKED_CARDS — client-side
// copy for the fee pre-check + confirm/toast text.
const STAKE_DAILY_RATES = { bronze: 5, silver: 10, gold: 25, platinum: 50, diamond: 100 };
const STAKE_MAX_DAYS = 20;
const STAKE_MAX_CARDS = 5;
document.getElementById("modal-stake").addEventListener("click", async ()=>{
  if(!selectedItem) return;
  if(selectedItem.in_giveaway && !selectedItem.staked_at){
    toast("Карта сейчас разыгрывается в чате");
    return;
  }
  if(selectedItem.pvp_round_id && !selectedItem.staked_at){
    toast("Карта сейчас участвует в PvP игре");
    return;
  }
  if(selectedItem.staked_at){
    const unstakeFee = STAKE_ENTRY_FEE_MULTIPLIER * (STAKE_DAILY_RATES[selectedItem.rarity] ?? 5);
    const unstakeConfirmed = await confirmAction(`Точно убрать карту из стейкинга? Уплаченные ${unstakeFee} гемов за вход не возвращаются.`);
    if(!unstakeConfirmed) return;
    try{
      const res = await api("/stake/unstake", { user_card_id: selectedUserCardId });
      selectedItem.staked_at = null;
      updateStakeButton(selectedItem);
      updateSellButton(selectedItem);
      updateSwapButton(selectedItem);
      if(res && typeof res.gems === "number") setGemsDisplay(res.gems);
      renderProfileGrid();
      toast("Снято со стейкинга");
    }catch(e){
      toast("Не получилось: " + e.message);
    }
  }else{
    if(selectedItem.listed_price || selectedItem.swap_listed){
      toast("Сначала снимите карту с продажи/обмена");
      return;
    }
    const dailyRate = STAKE_DAILY_RATES[selectedItem.rarity] ?? 5;
    const fee = STAKE_ENTRY_FEE_MULTIPLIER * dailyRate;
    const currentGems = gemsBalance;
    if(currentGems < fee){
      toastHTML(`Не хватает гемов — нужно ${fee} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
      return;
    }
    const stakeConfirmed = await showStakeConfirm(fee);
    if(!stakeConfirmed) return;
    try{
      const res = await api("/stake/list", { user_card_id: selectedUserCardId });
      selectedItem.staked_at = new Date().toISOString();
      updateStakeButton(selectedItem);
      updateSellButton(selectedItem);
      updateSwapButton(selectedItem);
      if(res && typeof res.gems === "number") setGemsDisplay(res.gems);
      renderProfileGrid();
      toast(`В стейкинге! +${dailyRate} гемов в сутки за карту`);
    }catch(e){
      if(String(e.message).includes("not enough gems")){
        toastHTML(`Не хватает гемов — нужно ${fee} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
      }else if(String(e.message).includes("stake limit reached")){
        toast(`Уже застейкано максимум карт (${STAKE_MAX_CARDS}) — сними что-то со стейкинга, чтобы застейкать эту`);
      }else{
        toast("Не получилось: " + e.message);
      }
    }
  }
});

// Mirrors database.py's CRAFT_COST_GEMS — craft price scales with the rarity being burned.
const CRAFT_COST_BY_RARITY = { bronze: 25, silver: 25, gold: 100, platinum: 200, diamond: 200 };
// Mirrors database.py's CRAFT_WEIGHTS — client-side copy for the chance-hint display only.
// Mirrors database.py's CRAFT_WEIGHTS — crafting never produces a tier BELOW the one
// burned, so each row only lists tiers same-or-higher (burn Gold, only Gold/Platinum/
// Diamond are possible).
const CRAFT_CHANCES = {
  bronze:   { bronze: 69, silver: 16, gold: 8, platinum: 5, diamond: 2 },
  silver:   { silver: 75, gold: 13, platinum: 8, diamond: 4 },
  gold:     { gold: 82, platinum: 10, diamond: 8 },
  platinum: { platinum: 84, diamond: 16 },
  diamond:  { diamond: 100 },
};
function craftHintHTML(rarity){
  const t = CRAFT_CHANCES[rarity] || CRAFT_CHANCES.bronze;
  const fmt = (v) => (v % 1 === 0 ? v : v.toFixed(1));
  const parts = ["diamond", "platinum", "gold", "silver", "bronze"]
    .filter(r => t[r] !== undefined)
    .map(r => `<span class="${r}">${r.toUpperCase()} ${fmt(t[r])}%</span>`);
  return parts.length > 3 ? parts.slice(0,3).join(" · ") + "<br>" + parts.slice(3).join(" · ") : parts.join(" · ");
}
document.getElementById("modal-craft").addEventListener("click", async ()=>{
  if(document.getElementById("modal-craft").disabled) return;
  if(!selectedItem) return;
  if(selectedItem.in_giveaway){
    toast("Карта сейчас разыгрывается в чате");
    return;
  }
  if(selectedItem.staked_at || selectedItem.listed_price || selectedItem.swap_listed){
    toast("Сначала снимите карту с продажи/обмена/стейкинга");
    return;
  }
  const craftCost = CRAFT_COST_BY_RARITY[selectedItem.rarity] || CRAFT_COST_BY_RARITY.bronze;
  const currentGems = gemsBalance;
  if(currentGems < craftCost){
    toastHTML(`Не хватает гемов — нужно ${craftCost} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    return;
  }
  const craftRarityLabel = (r) => r ? r[0].toUpperCase() + r.slice(1) : "Bronze";
  const craftChances = CRAFT_CHANCES[selectedItem.rarity] || CRAFT_CHANCES.bronze;
  const craftChanceLines = selectedItem.rarity === "diamond"
    ? "Diamond: 95%\nКарта уничтожается без результата: 5%"
    : ["diamond", "platinum", "gold", "silver", "bronze"]
        .filter(r => craftChances[r] !== undefined)
        .map(r => `${craftRarityLabel(r)}: ${craftChances[r] % 1 === 0 ? craftChances[r] : craftChances[r].toFixed(1)}%`)
        .join("\n");
  const craftConfirmed = await confirmAction(
    `Крафт стоит ${craftCost} гемов, карта (${craftRarityLabel(selectedItem.rarity)}) будет уничтожена.\n\nШансы получить:\n${craftChanceLines}\n\nПродолжить?`
  );
  if(!craftConfirmed) return;

  const craftUserCardId = selectedUserCardId;
  const craftInputRarity = selectedItem.rarity;
  document.getElementById("modal-overlay").classList.remove("active");
  switchScreen("farm-screen");

  const mySeq = ++farmRequestSeq; // also invalidates any in-flight farm, so it can't clobber this result
  try{
    const cards = await ensureAllCards();
    const craftHint = craftInputRarity === "diamond"
      ? `<span class="diamond">95% — новая DIAMOND карта</span><br><span class="bronze">5% — карта уничтожается без результата</span>`
      : craftHintHTML(craftInputRarity);
    await startFarmAnimation(cards, craftHint);
    if(mySeq !== farmRequestSeq) return;
    const card = await api("/craft", { user_card_id: craftUserCardId });
    if(mySeq !== farmRequestSeq) return;
    document.getElementById("total-farmed").textContent = card.total_farmed; // a failed diamond craft destroys the card, so this can go DOWN
    const stage = document.getElementById("farm-stage");
    const nameEl = document.getElementById("farm-result-name");
    const rarityEl = document.getElementById("farm-result-rarity");
    nameEl.classList.remove("chance-hint");
    if(card.success === false){
      stage.innerHTML = `<div class="placeholder">🔥</div>`;
      nameEl.textContent = "Карта уничтожена без результата";
      document.getElementById("farm-result-number").textContent = "";
      rarityEl.textContent = "";
      rarityEl.className = "";
      setGemsDisplay(card.gems);
      tg?.HapticFeedback?.notificationOccurred("error");
    }else{
      stage.innerHTML = `<img class="rarity-border-${card.rarity}" src="/static/cards/${card.filename}?v=${CARD_IMG_VERSION}" alt="${card.name||''}"><div class="rarity-tag ${card.rarity}">${card.rarity}</div><div class="drop-number">#${card.drop_number}</div>`;
      nameEl.textContent = card.name || "Новая картинка!";
      document.getElementById("farm-result-number").textContent = `№${card.drop_number}`;
      rarityEl.textContent = card.rarity || "";
      rarityEl.className = card.rarity || "";
      setGemsDisplay(card.gems);
      tg?.HapticFeedback?.notificationOccurred("success");
    }
  }catch(e){
    if(mySeq !== farmRequestSeq) return;
    document.getElementById("farm-progress-wrap").classList.remove("active");
    clearInterval(farmCycleTimer);
    clearInterval(farmProgressTimer);
    if(String(e.message).includes("not enough gems")){
      toastHTML(`Не хватает гемов — нужно ${craftCost} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    }else{
      toast("Ошибка крафта: " + e.message);
    }
  }
});

// ---------- Gem cases ("Кейсы") ----------
const CASES = [
  {key:"hamster", name:"Хомяк", price:100, image:"case/case_hamster.jpg",
   weights:{bronze:34, silver:63, platinum:2.5, diamond:0.5}},
  {key:"duck", name:"Уточка", price:200, image:"case/case_utya.jpg",
   weights:{silver:38, gold:58, platinum:3, diamond:1}},
  {key:"capybara", name:"Капибара", price:400, image:"case/case_capybara.jpg",
   weights:{gold:33, platinum:67}},
  {key:"pepe", name:"Пепе", price:1000, image:"case/case_pep.jpg",
   weights:{platinum:75, diamond:25}},
];

function caseHintHTML(def){
  const t = def.weights;
  const fmt = (v) => (v % 1 === 0 ? v : v.toFixed(1));
  const parts = ["diamond", "platinum", "gold", "silver", "bronze"]
    .filter(r => t[r] !== undefined)
    .map(r => `<span class="${r}">${r.toUpperCase()} ${fmt(t[r])}%</span>`);
  return parts.length > 3 ? parts.slice(0,3).join(" · ") + "<br>" + parts.slice(3).join(" · ") : parts.join(" · ");
}

function renderCaseGrid(){
  const grid = document.getElementById("case-grid");
  grid.innerHTML = CASES.map(c => `
    <div class="case-item">
      <button type="button" class="case-info-btn" data-key="${c.key}">?</button>
      <img src="/static/${c.image}" alt="${c.name}" class="case-img" data-key="${c.key}" style="cursor:pointer;">
      <div class="case-name">${c.name}</div>
      <div class="case-price"><img src="/static/icons/diamond.png" alt="">${c.price}</div>
      <button type="button" class="case-open-btn" data-key="${c.key}">Открыть</button>
    </div>
  `).join("");
  grid.querySelectorAll(".case-open-btn").forEach(btn=>{
    btn.addEventListener("click", ()=> openCase(btn.dataset.key));
  });
  grid.querySelectorAll(".case-info-btn").forEach(btn=>{
    btn.addEventListener("click", ()=> showCaseInfo(btn.dataset.key));
  });
  grid.querySelectorAll(".case-img").forEach(img=>{
    img.addEventListener("click", ()=> showCaseInfo(img.dataset.key));
  });
}

function showCaseInfo(caseKey){
  const def = CASES.find(c => c.key === caseKey);
  if(!def) return;
  showAlertHTML(`<b>${def.name}</b> — ${def.price} <img src="/static/icons/diamond.png" alt="" style="width:13px;height:13px;vertical-align:-2px;"><br><br>Шансы выпадения:<br>${caseHintHTML(def)}`);
}

document.getElementById("cases-btn").addEventListener("click", ()=>{
  renderCaseGrid();
  document.getElementById("case-overlay").classList.add("active");
});
document.getElementById("case-close").addEventListener("click", ()=>{
  document.getElementById("case-overlay").classList.remove("active");
});
document.getElementById("case-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "case-overlay" || e.target.id === "case-scroll"){
    document.getElementById("case-overlay").classList.remove("active");
  }
});

let caseOpenBusy = false;
async function openCase(caseKey){
  if(caseOpenBusy) return;
  const def = CASES.find(c => c.key === caseKey);
  if(!def) return;

  const currentGems = gemsBalance;
  if(currentGems < def.price){
    toastHTML(`Не хватает гемов — нужно ${def.price} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    return;
  }
  caseOpenBusy = true;
  document.getElementById("case-overlay").classList.remove("active");
  switchScreen("farm-screen");

  const mySeq = ++farmRequestSeq; // invalidates any in-flight farm/craft so it can't clobber this result
  try{
    const cards = await ensureAllCards();
    await startFarmAnimation(cards, caseHintHTML(def));
    if(mySeq !== farmRequestSeq) return;
    const card = await api("/case/open", { case_key: caseKey });
    if(mySeq !== farmRequestSeq) return;
    const stage = document.getElementById("farm-stage");
    stage.innerHTML = `<img class="rarity-border-${card.rarity}" src="/static/cards/${card.filename}?v=${CARD_IMG_VERSION}" alt="${card.name||''}"><div class="rarity-tag ${card.rarity}">${card.rarity}</div><div class="drop-number">#${card.drop_number}</div>`;
    const nameEl = document.getElementById("farm-result-name");
    nameEl.classList.remove("chance-hint");
    nameEl.textContent = card.name || "Новая картинка!";
    document.getElementById("farm-result-number").textContent = `№${card.drop_number}`;
    const rarityEl = document.getElementById("farm-result-rarity");
    rarityEl.textContent = card.rarity || "";
    rarityEl.className = card.rarity || "";
    setGemsDisplay(card.gems);
    document.getElementById("total-farmed").textContent = card.drop_number; // drop_number IS the new global total (same COUNT(*) as farm's total_farmed)
    tg?.HapticFeedback?.notificationOccurred("success");
  }catch(e){
    if(mySeq !== farmRequestSeq) return;
    document.getElementById("farm-progress-wrap").classList.remove("active");
    clearInterval(farmCycleTimer);
    clearInterval(farmProgressTimer);
    if(String(e.message).includes("not enough gems")){
      toastHTML(`Не хватает гемов — нужно ${def.price} <img src="/static/icons/diamond.png" alt="" style="width:14px;height:14px;vertical-align:-2px;">`);
    }else{
      toast("Ошибка открытия кейса: " + e.message);
    }
  }finally{
    caseOpenBusy = false;
  }
}

// ---------- Numbers auction ("Номера") — bid gems on the lowest still-free/auctioned
// collection numbers; win one, then either attach it to your own card or resell it. ----------
let numbersMode = "auction";
let numbersBoard = { auctions: [], listings: [] };
let numbersMine = [];
let numbersOwners = [];

function numberTimeLeftLabel(expiresAt){
  if(!expiresAt) return "";
  const ms = new Date(expiresAt).getTime() - Date.now();
  if(ms <= 0) return "завершается...";
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return h > 0 ? `осталось ${h}ч ${m}м` : `осталось ${m}м`;
}

async function loadNumbers(){
  const board = await api("/numbers/board");
  numbersBoard = board;
  setGemsDisplay(board.gems);
  const mine = await api("/numbers/mine");
  numbersMine = mine.numbers;
  try{
    const owners = await api("/numbers/owners");
    numbersOwners = owners.numbers;
  }catch(e){
    console.warn("numbers/owners failed", e);
    numbersOwners = [];
  }
  renderNumbersList();
}

function renderNumbersList(){
  const list = document.getElementById("numbers-list");
  const sub = document.getElementById("numbers-sub");
  document.getElementById("numbers-owners-btn").style.display = numbersMode === "auction" ? "block" : "none";
  if(numbersMode === "auction"){
    sub.textContent = `Топ 100 самых низких свободных номеров — минимальная ставка ${numbersBoard.min_bid || 25} гемов, таймер 12ч с момента последней ставки`;
    if(!numbersBoard.auctions.length){
      list.innerHTML = `<div class="empty-state">Пока нет свободных номеров</div>`;
      return;
    }
    list.innerHTML = numbersBoard.auctions.map(a=>{
      const leading = a.status === "auction"
        ? `<div class="lbl">Ставка (${numberTimeLeftLabel(a.bid_expires_at)})</div><div class="val">${a.highest_bid} <img src="/static/icons/diamond.png" alt=""></div>`
        : `<div class="lbl">Свободен</div><div class="val">от ${numbersBoard.min_bid || 25} <img src="/static/icons/diamond.png" alt=""></div>`;
      return `
        <div class="number-row" data-number="${a.number}">
          <div class="num">#${a.number}</div>
          <div class="info">${leading}</div>
          <button class="act bid" data-number="${a.number}" data-min="${Math.max(numbersBoard.min_bid || 25, (a.highest_bid||0)+1)}">Ставка</button>
        </div>`;
    }).join("");
    list.querySelectorAll(".act.bid").forEach(btn=>{
      btn.addEventListener("click", ()=>bidOnNumber(parseInt(btn.dataset.number), parseInt(btn.dataset.min)));
    });
  }else if(numbersMode === "listings"){
    sub.textContent = "Номера, которые другие игроки выставили на продажу";
    if(!numbersBoard.listings.length){
      list.innerHTML = `<div class="empty-state">Пока никто ничего не продаёт</div>`;
      return;
    }
    list.innerHTML = numbersBoard.listings.map(l=>{
      const seller = l.username ? (maskedName(l.username) === "Бот" ? "Бот" : `@${l.username}`) : (l.first_name || "Игрок");
      return `
        <div class="number-row" data-number="${l.number}">
          <div class="num">#${l.number}</div>
          <div class="info"><div class="lbl">${seller}</div><div class="val">${l.list_price} <img src="/static/icons/diamond.png" alt=""></div></div>
          <button class="act buy" data-number="${l.number}">Купить</button>
        </div>`;
    }).join("");
    list.querySelectorAll(".act.buy").forEach(btn=>{
      btn.addEventListener("click", ()=>buyListedNumber(parseInt(btn.dataset.number)));
    });
  }else if(numbersMode === "mine"){
    sub.textContent = "Номера, которые ты выиграл или купил";
    if(!numbersMine.length){
      list.innerHTML = `<div class="empty-state">У тебя пока нет купленных номеров</div>`;
      return;
    }
    list.innerHTML = numbersMine.map(n=>{
      const status = n.user_card_id
        ? `<div class="lbl">Закреплён за картой</div><div class="val">${n.list_price ? `Продаётся за ${n.list_price}` : "Не продаётся"}</div>`
        : `<div class="lbl">Не закреплён</div><div class="val">${n.list_price ? `Продаётся за ${n.list_price}` : "Свободен для действия"}</div>`;
      // Only a FREE (unpinned) number can be listed for sale (server enforces this
      // too, see list_number_for_sale) -- while it's attached to a card, show it as
      // simply in use, same as the names tab's "Заюзан" -- pin a different number to
      // that card first to free this one up before it can be sold.
      const actions = n.list_price
        ? `<button class="act cancel" data-number="${n.number}" data-action="cancel">Снять</button>`
        : n.user_card_id
          ? `<button class="act used" disabled>Занят</button>`
          : `<button class="act attach" data-number="${n.number}" data-action="attach">Закрепить</button><button class="act sell" data-number="${n.number}" data-action="sell">Продать</button>`;
      return `
        <div class="number-row number-row-mine" data-number="${n.number}">
          <div class="row-top">
            <div class="num">#${n.number}</div>
            <div class="info">${status}</div>
          </div>
          <div class="row-actions">${actions}</div>
        </div>`;
    }).join("");
    list.querySelectorAll("[data-action='attach']").forEach(btn=>{
      btn.addEventListener("click", ()=>attachNumberFlow(parseInt(btn.dataset.number)));
    });
    list.querySelectorAll("[data-action='sell']").forEach(btn=>{
      btn.addEventListener("click", ()=>sellNumberFlow(parseInt(btn.dataset.number)));
    });
    list.querySelectorAll("[data-action='cancel']").forEach(btn=>{
      btn.addEventListener("click", ()=>cancelNumberListingFlow(parseInt(btn.dataset.number)));
    });
  }else{
    sub.textContent = "Все занятые номера и их текущие владельцы";
    if(!numbersOwners.length){
      list.innerHTML = `<div class="empty-state">Пока ни один номер никому не принадлежит</div>`;
      return;
    }
    list.innerHTML = numbersOwners.map(n=>{
      const owner = n.username ? (maskedName(n.username) === "Бот" ? "Бот" : `@${n.username}`) : (n.first_name || "Игрок");
      const status = n.user_card_id
        ? "закреплён за картой"
        : (n.list_price ? `продаётся за ${n.list_price} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">` : "не закреплён");
      return `
        <div class="number-row" data-number="${n.number}">
          <div class="num">#${n.number}</div>
          <div class="info"><div class="lbl">${owner}</div><div class="val">${status}</div></div>
        </div>`;
    }).join("");
  }
}

async function bidOnNumber(number, minBid){
  const amount = await promptNumber(`Ставка на номер #${number} (минимум ${minBid} гемов)`);
  if(!amount) return;
  try{
    const result = await api("/numbers/bid", { number, amount });
    setGemsDisplay(result.gems);
    toast(`Ставка ${amount} гемов принята — таймер обновлён на 12ч`);
    await loadNumbers();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

async function buyListedNumber(number){
  const userCardId = await openNumberCardPicker("Выбери свою карту, которая получит номер");
  if(!userCardId) return;
  try{
    const result = await api("/numbers/buy_listed", { number, user_card_id: userCardId });
    setGemsDisplay(result.gems);
    toast(`Номер #${number} куплен и закреплён`);
    await loadNumbers();
    sortInventoryAndRender();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

async function attachNumberFlow(number){
  const userCardId = await openNumberCardPicker("Выбери свою карту для номера #" + number);
  if(!userCardId) return;
  try{
    await api("/numbers/attach", { number, user_card_id: userCardId });
    toast(`Номер #${number} закреплён`);
    await loadNumbers();
    sortInventoryAndRender();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

async function sellNumberFlow(number){
  const price = await promptNumber(`За сколько гемов продать номер #${number}?`);
  if(!price) return;
  try{
    await api("/numbers/list", { number, price_gems: price });
    toast(`Номер #${number} выставлен за ${price} гемов`);
    await loadNumbers();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

async function cancelNumberListingFlow(number){
  try{
    await api("/numbers/cancel_listing", { number });
    toast(`Продажа номера #${number} снята`);
    await loadNumbers();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

// Single-card picker, reused by "buy a listed number" and "attach a number to a card".
let numberCardPickerResolve = null;
function openNumberCardPicker(title){
  document.getElementById("number-card-picker-title").textContent = title;
  const grid = document.getElementById("number-card-picker-grid");
  const eligible = inventory.filter(c => !c.pvp_round_id && !c.listed_price && !c.swap_listed && !c.staked_at && !c.in_giveaway);
  if(!eligible.length){
    grid.innerHTML = `<div class="empty-state">Нет свободных карт</div>`;
  }else{
    grid.innerHTML = eligible.map(c => `
      <div class="grid-item picker-item rarity-border-${c.rarity}" data-uc="${c.user_card_id}">
        <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name || ''}">
        <div class="rarity-tag ${c.rarity}">${c.rarity}</div>
        <div class="check">✓</div>
      </div>
    `).join("");
  }
  let selected = null;
  grid.querySelectorAll(".picker-item").forEach(el=>{
    el.addEventListener("click", ()=>{
      grid.querySelectorAll(".picker-item").forEach(x=>x.classList.remove("selected"));
      el.classList.add("selected");
      selected = parseInt(el.dataset.uc);
    });
  });
  document.getElementById("number-card-picker-overlay").classList.add("active");
  return new Promise(resolve=>{
    numberCardPickerResolve = (confirmed)=>{
      document.getElementById("number-card-picker-overlay").classList.remove("active");
      resolve(confirmed ? selected : null);
    };
  });
}
document.getElementById("number-card-picker-confirm").addEventListener("click", ()=>{
  if(numberCardPickerResolve) numberCardPickerResolve(true);
});
document.getElementById("number-card-picker-cancel").addEventListener("click", ()=>{
  if(numberCardPickerResolve) numberCardPickerResolve(false);
});

document.getElementById("numbers-btn").addEventListener("click", async ()=>{
  document.getElementById("numbers-overlay").classList.add("active");
  try{ await loadNumbers(); }catch(e){ toast("Ошибка загрузки номеров: " + e.message); }
});
document.getElementById("numbers-close").addEventListener("click", ()=>{
  document.getElementById("numbers-overlay").classList.remove("active");
});
document.getElementById("numbers-close-bottom").addEventListener("click", ()=>{
  document.getElementById("numbers-overlay").classList.remove("active");
});
document.getElementById("numbers-card").addEventListener("scroll", (e)=>{
  const btn = document.getElementById("numbers-scroll-top");
  btn.classList.toggle("visible", e.target.scrollTop > 300);
});
document.getElementById("numbers-scroll-top").addEventListener("click", ()=>{
  document.getElementById("numbers-card").scrollTo({top:0, behavior:"smooth"});
});
document.getElementById("numbers-mode-switch").addEventListener("click", (e)=>{
  const btn = e.target.closest(".mode-switch-btn");
  if(!btn) return;
  document.querySelectorAll("#numbers-mode-switch .mode-switch-btn").forEach(b=>b.classList.remove("active"));
  btn.classList.add("active");
  numbersMode = btn.dataset.mode;
  renderNumbersList();
});
document.getElementById("numbers-owners-btn").addEventListener("click", ()=>{
  document.querySelectorAll("#numbers-mode-switch .mode-switch-btn").forEach(b=>b.classList.remove("active"));
  numbersMode = "owners";
  renderNumbersList();
});

// ---------- Names auction ("Имена") — create your own name (25 gems, opens a 12h
// auction that anyone can outbid), or bid on/buy someone else's; won/bought names sit
// in your bank until used via the "Кастом" (Custom NFT) flow below. ----------
let namesMode = "auction";
let namesBoard = { auctions: [], listings: [], min_bid: 25 };
let namesMine = [];
let namesOwners = [];

async function loadNames(){
  const board = await api("/names/board");
  namesBoard = board;
  setGemsDisplay(board.gems);
  const mine = await api("/names/mine");
  namesMine = mine.names;
  try{
    const owners = await api("/names/owners");
    namesOwners = owners.names;
  }catch(e){
    console.warn("names/owners failed", e);
    namesOwners = [];
  }
  renderNamesList();
}

function renderNamesList(){
  const list = document.getElementById("names-list");
  const sub = document.getElementById("names-sub");
  document.getElementById("names-create-btn").style.display = namesMode === "auction" ? "block" : "none";
  document.getElementById("names-owners-btn").style.display = namesMode === "auction" ? "block" : "none";
  if(namesMode === "auction"){
    sub.textContent = `Создай своё имя (от ${namesBoard.min_bid || 25} гемов) или перебей чужую ставку — таймер 12ч с момента последней ставки`;
    if(!namesBoard.auctions.length){
      list.innerHTML = `<div class="empty-state">Пока никто не создал имя</div>`;
    }else{
      list.innerHTML = namesBoard.auctions.map(a => `
        <div class="number-row" data-name="${a.name}">
          <div class="num">${capName(a.name)}</div>
          <div class="info"><div class="lbl">Ставка (${numberTimeLeftLabel(a.bid_expires_at)})</div><div class="val">${a.highest_bid} <img src="/static/icons/diamond.png" alt=""></div></div>
          <button class="act bid" data-name="${a.name}" data-min="${Math.max(namesBoard.min_bid || 25, (a.highest_bid||0)+1)}">Ставка</button>
        </div>`).join("");
      list.querySelectorAll(".act.bid").forEach(btn=>{
        btn.addEventListener("click", ()=>bidOnName(btn.dataset.name, parseInt(btn.dataset.min)));
      });
    }
  }else if(namesMode === "listings"){
    sub.textContent = "Имена, которые другие игроки выставили на продажу";
    if(!namesBoard.listings.length){
      list.innerHTML = `<div class="empty-state">Пока никто ничего не продаёт</div>`;
    }else{
      list.innerHTML = namesBoard.listings.map(l=>{
        const seller = l.username ? (maskedName(l.username) === "Бот" ? "Бот" : `@${l.username}`) : (l.first_name || "Игрок");
        return `
        <div class="number-row" data-name="${l.name}">
          <div class="num">${capName(l.name)}</div>
          <div class="info"><div class="lbl">${seller}</div><div class="val">${l.list_price} <img src="/static/icons/diamond.png" alt=""></div></div>
          <button class="act buy" data-name="${l.name}">Купить</button>
        </div>`;
      }).join("");
      list.querySelectorAll(".act.buy").forEach(btn=>{
        btn.addEventListener("click", ()=>buyListedName(btn.dataset.name));
      });
    }
  }else if(namesMode === "mine"){
    sub.textContent = "Имена, которые ты создал или купил";
    if(!namesMine.length){
      list.innerHTML = `<div class="empty-state">У тебя пока нет имён — создай своё выше</div>`;
    }else{
      list.innerHTML = namesMine.map(n=>{
        const status = n.user_card_id
          ? `<div class="lbl">Используется на карте</div><div class="val">${n.list_price ? `Продаётся за ${n.list_price}` : "Не продаётся"}</div>`
          : `<div class="lbl">В банке</div><div class="val">${n.list_price ? `Продаётся за ${n.list_price}` : "Свободно для Кастом"}</div>`;
        // A name attached to a card can't be listed until it's detached first (put a
        // different name on that card via Кастом) -- so instead of a "Продать" button
        // that would need that extra step first, show it as simply in use.
        const actions = n.list_price
          ? `<button class="act cancel" data-name="${n.name}" data-action="cancel">Снять</button>`
          : n.user_card_id
            ? `<button class="act used" disabled>Заюзан</button>`
            : `<button class="act sell" data-name="${n.name}" data-action="sell">Продать</button>`;
        return `
        <div class="number-row number-row-mine" data-name="${n.name}">
          <div class="row-top">
            <div class="num">${capName(n.name)}</div>
            <div class="info">${status}</div>
          </div>
          <div class="row-actions">${actions}</div>
        </div>`;
      }).join("");
      list.querySelectorAll("[data-action='sell']").forEach(btn=>{
        btn.addEventListener("click", ()=>sellNameFlow(btn.dataset.name));
      });
      list.querySelectorAll("[data-action='cancel']").forEach(btn=>{
        btn.addEventListener("click", ()=>cancelNameListingFlow(btn.dataset.name));
      });
    }
  }else{
    sub.textContent = "Все занятые имена и их текущие владельцы";
    if(!namesOwners.length){
      list.innerHTML = `<div class="empty-state">Пока ни одно имя никому не принадлежит</div>`;
    }else{
      list.innerHTML = namesOwners.map(n=>{
        const owner = n.username ? (maskedName(n.username) === "Бот" ? "Бот" : `@${n.username}`) : (n.first_name || "Игрок");
        const status = n.user_card_id
          ? "используется на карте"
          : (n.list_price ? `продаётся за ${n.list_price} <img class="gem-icon-inline" src="/static/icons/diamond.png" alt="">` : "в банке, не используется");
        return `
        <div class="number-row" data-name="${n.name}">
          <div class="num">${capName(n.name)}</div>
          <div class="info"><div class="lbl">${owner}</div><div class="val">${status}</div></div>
        </div>`;
      }).join("");
    }
  }
}

async function createNameFlow(){
  const raw = await promptText(`Придумай имя (от ${CUSTOM_NAME_MIN_LEN_CLIENT} до ${CUSTOM_NAME_MAX_LEN_CLIENT} латинских букв/цифр) — создание стоит ${namesBoard.min_bid || 25} гемов и выставляет имя на 12ч аукцион`, "durov");
  if(!raw) return;
  try{
    const result = await api("/names/create", { name: raw });
    setGemsDisplay(result.gems);
    toast(`Имя "${capName(result.name)}" создано и выставлено на аукцион на 12ч`);
    await loadNames();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}
const CUSTOM_NAME_MIN_LEN_CLIENT = 3;
const CUSTOM_NAME_MAX_LEN_CLIENT = 16;

async function bidOnName(name, minBid){
  const amount = await promptNumber(`Ставка на имя "${name}" (минимум ${minBid} гемов)`);
  if(!amount) return;
  try{
    const result = await api("/names/bid", { name, amount });
    setGemsDisplay(result.gems);
    toast(`Ставка ${amount} гемов принята — таймер обновлён на 12ч`);
    await loadNames();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

async function buyListedName(name){
  try{
    const result = await api("/names/buy_listed", { name });
    setGemsDisplay(result.gems);
    toast(`Имя "${name}" куплено`);
    await loadNames();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

async function sellNameFlow(name){
  const price = await promptNumber(`За сколько гемов продать имя "${name}"?`);
  if(!price) return;
  try{
    await api("/names/list", { name, price_gems: price });
    toast(`Имя "${name}" выставлено за ${price} гемов`);
    await loadNames();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

async function cancelNameListingFlow(name){
  try{
    await api("/names/cancel_listing", { name });
    toast(`Продажа имени "${name}" снята`);
    await loadNames();
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}

document.getElementById("names-btn").addEventListener("click", async ()=>{
  document.getElementById("names-overlay").classList.add("active");
  try{ await loadNames(); }catch(e){ toast("Ошибка загрузки имён: " + e.message); }
});
document.getElementById("names-close").addEventListener("click", ()=>{
  document.getElementById("names-overlay").classList.remove("active");
});
document.getElementById("names-close-bottom").addEventListener("click", ()=>{
  document.getElementById("names-overlay").classList.remove("active");
});
document.getElementById("names-card").addEventListener("scroll", (e)=>{
  const btn = document.getElementById("names-scroll-top");
  btn.classList.toggle("visible", e.target.scrollTop > 300);
});
document.getElementById("names-scroll-top").addEventListener("click", ()=>{
  document.getElementById("names-card").scrollTo({top:0, behavior:"smooth"});
});
document.getElementById("names-mode-switch").addEventListener("click", (e)=>{
  const btn = e.target.closest(".mode-switch-btn");
  if(!btn) return;
  document.querySelectorAll("#names-mode-switch .mode-switch-btn").forEach(b=>b.classList.remove("active"));
  btn.classList.add("active");
  namesMode = btn.dataset.mode;
  renderNamesList();
});
document.getElementById("names-create-btn").addEventListener("click", createNameFlow);
document.getElementById("names-owners-btn").addEventListener("click", ()=>{
  document.querySelectorAll("#names-mode-switch .mode-switch-btn").forEach(b=>b.classList.remove("active"));
  namesMode = "owners";
  renderNamesList();
});

// ---------- Generic single-choice list picker (reused by the Custom NFT flow to pick
// a name / a number from the player's own bank) — mirrors openNumberCardPicker()'s
// Promise-based shape, just rendering plain rows instead of a card grid. ----------
let simplePickerResolve = null;
function openSimplePicker(title, items, formatFn){
  document.getElementById("simple-picker-title").textContent = title;
  const list = document.getElementById("simple-picker-list");
  if(!items.length){
    list.innerHTML = `<div class="empty-state">Пусто</div>`;
  }else{
    list.innerHTML = items.map((it, i) => `<div class="simple-picker-item" data-i="${i}">${formatFn(it)}</div>`).join("");
  }
  let selected = null;
  list.querySelectorAll(".simple-picker-item").forEach(el=>{
    el.addEventListener("click", ()=>{
      list.querySelectorAll(".simple-picker-item").forEach(x=>x.classList.remove("selected"));
      el.classList.add("selected");
      selected = items[parseInt(el.dataset.i)];
    });
  });
  document.getElementById("simple-picker-overlay").classList.add("active");
  return new Promise(resolve=>{
    simplePickerResolve = (confirmed)=>{
      document.getElementById("simple-picker-overlay").classList.remove("active");
      resolve(confirmed ? selected : null);
    };
  });
}
document.getElementById("simple-picker-confirm").addEventListener("click", ()=>{
  if(simplePickerResolve) simplePickerResolve(true);
});
document.getElementById("simple-picker-cancel").addEventListener("click", ()=>{
  if(simplePickerResolve) simplePickerResolve(false);
});

// ---------- Custom NFT ("Кастом" -> Obsidian) ----------
// Charged in gems (checked server-side too): CUSTOM_NFT_CREATE_COST_GEMS the first
// time a card becomes Obsidian, or the cheaper CUSTOM_NFT_EDIT_COST_GEMS to swap the
// name/number on one that already is -- plus a free name from your own name-bank, a
// free number from your own number-bank, and the target card must not be busy
// (listed/staked/swapped/in a PvP round/giveaway) -- same as craft/burn.
const CUSTOM_NFT_CREATE_COST_GEMS = 500;
const CUSTOM_NFT_EDIT_COST_GEMS = 100;
async function openCustomNftFlow(){
  if(!selectedItem) return;
  const targetCardId = selectedItem.user_card_id;
  const isEdit = !!selectedItem.custom_name;
  const cost = isEdit ? CUSTOM_NFT_EDIT_COST_GEMS : CUSTOM_NFT_CREATE_COST_GEMS;
  try{
    const namesRes = await api("/names/mine");
    const freeNames = namesRes.names.filter(n => !n.user_card_id || n.user_card_id === targetCardId);
    if(!freeNames.length){
      toast("Сначала создай/купи свободное имя в блоке «Имена»");
      return;
    }
    const numbersRes = await api("/numbers/mine");
    const freeNumbers = numbersRes.numbers.filter(n => !n.user_card_id || n.user_card_id === targetCardId);
    if(!freeNumbers.length){
      toast("Сначала выиграй/купи свободный номер в блоке «Номера»");
      return;
    }
    const chosenName = await openSimplePicker("Выбери имя", freeNames, n => n.name);
    if(!chosenName) return;
    const chosenNumber = await openSimplePicker("Выбери номер", freeNumbers, n => "#" + n.number);
    if(!chosenNumber) return;
    const confirmMsg = isEdit
      ? `Замена имени/номера на уже Obsidian-карте стоит ${cost} гемов. Продолжить?`
      : `Создание Obsidian-карты стоит ${cost} гемов. Продолжить?`;
    const confirmed = await confirmAction(confirmMsg);
    if(!confirmed) return;
    const result = await api("/custom_nft/create", { user_card_id: targetCardId, name: chosenName.name, number: chosenNumber.number });
    setGemsDisplay(result.gems);
    toast(`Готово! Карта теперь Obsidian: ${capName(result.custom_name)} (−${result.cost} гемов)`);
    document.getElementById("modal-overlay").classList.remove("active");
    try{
      const data = await api("/profile");
      inventory = data.inventory;
      sortInventoryAndRender();
    }catch(e2){ /* non-critical -- next profile visit picks it up anyway */ }
  }catch(e){
    toast("Не получилось: " + e.message);
  }
}
document.getElementById("modal-custom").addEventListener("click", openCustomNftFlow);

// ---------- Burn (сжигание): sacrifice 4 same-rarity cards for a guaranteed shot at the
// next tier up. Free, but a real chance of total loss (unlike craft, which always
// gives SOMETHING back). ----------
const BURN_RECIPES = [
  {rarity:"bronze", target:"silver", count:4},
  {rarity:"silver", target:"gold", count:4},
  {rarity:"gold", target:"platinum", count:4},
  {rarity:"platinum", target:"diamond", count:4},
];
const RARITY_LABEL = {bronze:"Bronze", silver:"Silver", gold:"Gold", platinum:"Platinum", diamond:"Diamond"};
const RARITY_COLOR = {bronze:"#cd7f32", silver:"#9ca3af", gold:"#facc15", platinum:"#a78bfa", diamond:"#ff2fb0"};

function countEligibleCards(rarity){
  return inventory.filter(c => c.rarity === rarity && !c.staked_at && !c.listed_price && !c.swap_listed && !c.pvp_round_id && !c.in_giveaway).length;
}

function renderBurnGrid(){
  const grid = document.getElementById("burn-grid");
  grid.innerHTML = BURN_RECIPES.map(r=>{
    const have = countEligibleCards(r.rarity);
    const ready = have >= r.count;
    return `
      <div class="burn-item" data-rarity="${r.rarity}" style="cursor:pointer;">
        <div class="burn-item-icon" style="background:${RARITY_COLOR[r.rarity]};"></div>
        <div class="burn-item-info">
          <div class="burn-item-title">${r.count} ${RARITY_LABEL[r.rarity]} → 1 ${RARITY_LABEL[r.target]}</div>
          <div class="burn-item-count ${ready ? 'ready' : ''}">У тебя: ${have}/${r.count}</div>
        </div>
        <button type="button" class="burn-btn" data-rarity="${r.rarity}" ${ready ? '' : 'disabled'}>Эволюция</button>
      </div>
    `;
  }).join("");
  // Tapping the button itself, OR anywhere else on the row (icon/title/count), both open
  // the picker -- the button alone is a small target on mobile, so the whole row is a
  // tap target too. The button's own click is stopped from bubbling so this never fires twice.
  grid.querySelectorAll(".burn-btn:not([disabled])").forEach(btn=>{
    btn.addEventListener("click", (e)=>{
      e.stopPropagation();
      const recipe = BURN_RECIPES.find(r => r.rarity === btn.dataset.rarity);
      if(recipe) openBurnPicker(recipe);
    });
  });
  grid.querySelectorAll(".burn-item").forEach(item=>{
    item.addEventListener("click", ()=>{
      const recipe = BURN_RECIPES.find(r => r.rarity === item.dataset.rarity);
      if(!recipe) return;
      const have = countEligibleCards(recipe.rarity);
      if(have < recipe.count){
        toast(`Не хватает карт — нужно ${recipe.count} ${RARITY_LABEL[recipe.rarity]} (есть ${have})`);
        return;
      }
      openBurnPicker(recipe);
    });
  });
}

// ---------- Burn picker (choose which SPECIFIC cards to burn) ----------
let burnPickerRarity = null;
let burnPickerCount = 0;
let burnPickerTarget = null;
let burnPickerSelected = new Set();

function burnPickerUpdateTitle(){
  const n = burnPickerSelected.size;
  const batches = Math.floor(n / burnPickerCount);
  document.getElementById("burn-picker-title").textContent =
    (n > 0 && n % burnPickerCount === 0)
      ? `Выбрано ${n} ${RARITY_LABEL[burnPickerRarity]} (${batches}x эволюция)`
      : `Выбери карты ${RARITY_LABEL[burnPickerRarity]}, кратно ${burnPickerCount} (сейчас ${n})`;
}

function openBurnPicker(recipe){
  burnPickerRarity = recipe.rarity;
  burnPickerCount = recipe.count;
  burnPickerTarget = recipe.target;
  burnPickerSelected = new Set();
  burnPickerUpdateTitle();
  renderBurnPickerGrid();
  document.getElementById("burn-overlay").classList.remove("active");
  document.getElementById("burn-picker-overlay").classList.add("active");
}

function renderBurnPickerGrid(){
  const grid = document.getElementById("burn-picker-grid");
  const eligible = inventory.filter(c => c.rarity === burnPickerRarity && !c.pvp_round_id && !c.listed_price && !c.swap_listed && !c.staked_at && !c.pinned_at && !c.in_giveaway);
  if(eligible.length === 0){
    grid.innerHTML = `<div class="empty-state">Нет подходящих карт</div>`;
    return;
  }
  grid.innerHTML = eligible.map(c => `
    <div class="grid-item picker-item rarity-border-${c.rarity} ${burnPickerSelected.has(c.user_card_id) ? 'selected' : ''}" data-uc="${c.user_card_id}">
      <img src="/static/cards/${c.filename}?v=${CARD_IMG_VERSION}" alt="${c.name || ''}">
      <div class="rarity-tag ${c.rarity}">${c.rarity}</div>
      <div class="check">✓</div>
    </div>
  `).join("");
  grid.querySelectorAll(".picker-item").forEach(el=>{
    el.addEventListener("click", ()=>{
      const ucId = parseInt(el.dataset.uc);
      if(burnPickerSelected.has(ucId)){
        burnPickerSelected.delete(ucId);
        el.classList.remove("selected");
      }else{
        // Fast mode: no cap here -- the player can pick as many eligible cards as they
        // own, in any multiple of burnPickerCount, so many evolutions fire in one go.
        burnPickerSelected.add(ucId);
        el.classList.add("selected");
      }
      burnPickerUpdateTitle();
    });
  });
}

document.getElementById("burn-picker-cancel").addEventListener("click", ()=>{
  document.getElementById("burn-picker-overlay").classList.remove("active");
});
// Fast mode: one tap selects EVERY eligible card, trimmed down to the nearest multiple
// of burnPickerCount (any remainder can't form a full set and is left unselected) -- so
// a whole stack evolves in one confirm instead of repeating the 4-card cycle by hand.
// A second tap while something is already selected clears the selection back to zero.
document.getElementById("burn-picker-select-all").addEventListener("click", ()=>{
  const eligible = inventory.filter(c => c.rarity === burnPickerRarity && !c.pvp_round_id && !c.listed_price && !c.swap_listed && !c.staked_at && !c.pinned_at && !c.in_giveaway);
  if(burnPickerSelected.size > 0){
    burnPickerSelected = new Set();
  }else{
    const usable = Math.floor(eligible.length / burnPickerCount) * burnPickerCount;
    if(usable === 0){
      toast(`Нужно минимум ${burnPickerCount} карт ${RARITY_LABEL[burnPickerRarity]}, а есть ${eligible.length}`);
      return;
    }
    burnPickerSelected = new Set(eligible.slice(0, usable).map(c => c.user_card_id));
    if(usable < eligible.length){
      toast(`Выбрано ${usable} из ${eligible.length} — остаток не кратен ${burnPickerCount}`);
    }
  }
  burnPickerUpdateTitle();
  renderBurnPickerGrid();
});

document.getElementById("burn-picker-confirm").addEventListener("click", async ()=>{
  const n = burnPickerSelected.size;
  if(n === 0 || n % burnPickerCount !== 0){
    toast(`Нужно выбрать кратно ${burnPickerCount} карт (сейчас ${n})`);
    return;
  }
  const batches = n / burnPickerCount;
  const confirmed = await confirmAction(
    batches === 1
      ? `Эволюционировать эти ${burnPickerCount} карт(ы) в 1 карту редкости ${RARITY_LABEL[burnPickerTarget]}?\n\nБесплатно, гарантированный результат, но карты уничтожаются безвозвратно. Продолжить?`
      : `Эволюционировать ${n} карт (${batches} раз по ${burnPickerCount}) в ${batches} карт(ы) редкости ${RARITY_LABEL[burnPickerTarget]}?\n\nБесплатно, гарантированный результат, но карты уничтожаются безвозвратно. Продолжить?`
  );
  if(!confirmed) return;
  const rarity = burnPickerRarity;
  const ids = [...burnPickerSelected];
  document.getElementById("burn-picker-overlay").classList.remove("active");
  await runBurn(rarity, ids);
});

document.getElementById("burn-open-btn").addEventListener("click", ()=>{
  renderBurnGrid();
  document.getElementById("burn-overlay").classList.add("active");
});
document.getElementById("burn-close").addEventListener("click", ()=>{
  document.getElementById("burn-overlay").classList.remove("active");
});
document.getElementById("burn-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "burn-overlay" || e.target.id === "burn-scroll"){
    document.getElementById("burn-overlay").classList.remove("active");
  }
});

function showBurnSingleResult(result){
  const stage = document.getElementById("farm-stage");
  const nameEl = document.getElementById("farm-result-name");
  const rarityEl = document.getElementById("farm-result-rarity");
  nameEl.classList.remove("chance-hint");
  document.getElementById("farm-result-number").textContent = "";

  if(result.success && result.new_card){
    const card = result.new_card;
    stage.innerHTML = `<img class="rarity-border-${card.rarity}" src="/static/cards/${card.filename}?v=${CARD_IMG_VERSION}" alt="${card.name || ''}"><div class="rarity-tag ${card.rarity}">${card.rarity}</div>${card.drop_number ? `<div class="drop-number">#${card.drop_number}</div>` : ""}`;
    nameEl.textContent = card.name || "Новая карта!";
    document.getElementById("farm-result-number").textContent = card.drop_number ? `№${card.drop_number}` : "";
    rarityEl.textContent = card.rarity || "";
    rarityEl.className = card.rarity || "";
    tg?.HapticFeedback?.notificationOccurred("success");
  }else{
    stage.innerHTML = `<div class="placeholder">❌</div>`;
    nameEl.textContent = "Эволюция не удалась";
    rarityEl.textContent = "";
    rarityEl.className = "";
    tg?.HapticFeedback?.notificationOccurred("error");
  }
}

function showBurnBulkResult(recipe, totalBatches, successes, gained){
  const stage = document.getElementById("farm-stage");
  const nameEl = document.getElementById("farm-result-name");
  const rarityEl = document.getElementById("farm-result-rarity");
  nameEl.classList.remove("chance-hint");

  const gainedLine = Object.entries(gained).map(([r, n]) => `${n}× ${RARITY_LABEL[r] || r}`).join(", ") || "ничего";
  stage.innerHTML = `<div class="placeholder">${successes > 0 ? "✨" : "❌"}</div>`;
  nameEl.textContent = `Готово: ${successes}/${totalBatches} успешно`;
  document.getElementById("farm-result-number").textContent = `Получено: ${gainedLine}`;
  rarityEl.textContent = recipe.target || "";
  rarityEl.className = recipe.target || "";
  tg?.HapticFeedback?.notificationOccurred(successes > 0 ? "success" : "error");
}

let burnBusy = false;
async function runBurn(rarity, userCardIds){
  if(burnBusy) return;
  const recipe = BURN_RECIPES.find(r => r.rarity === rarity);
  if(!recipe) return;
  const count = recipe.count;
  const batches = [];
  for(let i = 0; i < userCardIds.length; i += count){
    batches.push(userCardIds.slice(i, i + count));
  }
  if(batches.length === 0) return;

  burnBusy = true;
  switchScreen("farm-screen");

  const mySeq = ++farmRequestSeq; // invalidates any in-flight farm/craft/case so it can't clobber this result
  try{
    const cards = await ensureAllCards();

    if(batches.length === 1){
      const hint = `<span class="${recipe.target}">1 ${RARITY_LABEL[recipe.target].toUpperCase()}</span>`;
      await startFarmAnimation(cards, hint);
      if(mySeq !== farmRequestSeq) return;
      const result = await api("/burn", { rarity, user_card_ids: batches[0] });
      if(mySeq !== farmRequestSeq) return;
      document.getElementById("total-farmed").textContent = result.total_farmed; // burning DELETES cards, so this can go DOWN
      showBurnSingleResult(result);
    }else{
      // Fast mode: fire every batch of `count` back-to-back with no per-card flip
      // animation, then show one aggregated summary -- replaying the full reveal for
      // every batch would defeat the point of doing it all "разом" (at once).
      await startFarmAnimation(cards, `Эволюция ×${batches.length}...`);
      let successes = 0;
      const gained = {};
      let lastTotalFarmed = null;
      for(const chunk of batches){
        if(mySeq !== farmRequestSeq) return;
        try{
          const result = await api("/burn", { rarity, user_card_ids: chunk });
          lastTotalFarmed = result.total_farmed;
          if(result.success && result.new_card){
            successes++;
            const r = result.new_card.rarity || recipe.target;
            gained[r] = (gained[r] || 0) + 1;
          }
        }catch(e){
          // one batch failing (card went busy mid-run, etc.) shouldn't abort the rest
        }
      }
      if(mySeq !== farmRequestSeq) return;
      if(lastTotalFarmed != null) document.getElementById("total-farmed").textContent = lastTotalFarmed;
      showBurnBulkResult(recipe, batches.length, successes, gained);
    }
  }catch(e){
    if(mySeq !== farmRequestSeq) return;
    document.getElementById("farm-progress-wrap").classList.remove("active");
    clearInterval(farmCycleTimer);
    clearInterval(farmProgressTimer);
    if(String(e.message).includes("not enough")){
      toast("Недостаточно подходящих карт этой редкости (проверь стейк/продажу/обмен)");
    }else{
      toast("Ошибка эволюции: " + e.message);
    }
  }finally{
    burnBusy = false;
  }
}

// ---------- Referral modal (tap the "Рефералы" stat tile in Profile) ----------
document.getElementById("profile-info-btn").addEventListener("click", ()=>{
  document.getElementById("profile-info-overlay").classList.add("active");
});
document.getElementById("profile-info-close").addEventListener("click", ()=>{
  document.getElementById("profile-info-overlay").classList.remove("active");
});
document.getElementById("profile-info-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "profile-info-overlay"){
    document.getElementById("profile-info-overlay").classList.remove("active");
  }
});

document.getElementById("stat-income-tile").addEventListener("click", ()=>{
  const amount = parseInt(document.getElementById("stat-income").textContent, 10) || 25;
  const tierIndex = Math.min(4, Math.max(0, Math.round((amount - 25) / 25)));
  document.querySelectorAll("#income-rows .income-info-row").forEach(row=>{
    const isCurrent = parseInt(row.dataset.tier, 10) === tierIndex;
    row.classList.toggle("current", isCurrent);
    let tag = row.querySelector(".current-tag");
    if(isCurrent && !tag){
      tag = document.createElement("span");
      tag.className = "current-tag";
      tag.textContent = "ты тут";
      row.querySelector(".income-info-left").appendChild(tag);
    }else if(!isCurrent && tag){
      tag.remove();
    }
  });
  document.getElementById("income-footnote-next").textContent =
    (daysUntilNextIncome != null) ? `Следующее повышение через ${daysUntilNextIncome} дн.` : "";
  document.getElementById("income-overlay").classList.add("active");
});
document.getElementById("income-close").addEventListener("click", ()=>{
  document.getElementById("income-overlay").classList.remove("active");
});
document.getElementById("income-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "income-overlay"){
    document.getElementById("income-overlay").classList.remove("active");
  }
});

document.getElementById("stat-refs-tile").addEventListener("click", ()=>{
  document.getElementById("referral-overlay").classList.add("active");
});
document.getElementById("referral-close").addEventListener("click", ()=>{
  document.getElementById("referral-overlay").classList.remove("active");
});
document.getElementById("referral-overlay").addEventListener("click", (e)=>{
  if(e.target.id === "referral-overlay"){
    document.getElementById("referral-overlay").classList.remove("active");
  }
});
document.getElementById("referral-share-btn").addEventListener("click", ()=>{
  const myId = tg?.initDataUnsafe?.user?.id;
  if(!myId){
    toast("Открой через Телеграм, чтобы поделиться");
    return;
  }
  const refLink = `https://t.me/Peeppobot?start=ref${myId}`;
  openShareSheet(refLink, "Залетай в Peeppo — фарми, торгуй, обменивайся и крафти карточки! 🎮");
  document.getElementById("referral-overlay").classList.remove("active");
});

// ---------- Invite friends (header button, visible on every tab) ----------
function openShareSheet(url, text){
  const shareUrl = `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`;
  if(tg?.openTelegramLink){
    tg.openTelegramLink(shareUrl);
  }else{
    window.open(shareUrl, "_blank");
  }
}

document.getElementById("invite-btn").addEventListener("click", ()=>{
  const myId = tg?.initDataUnsafe?.user?.id;
  if(!myId){
    toast("Открой через Телеграм, чтобы поделиться");
    return;
  }
  const refLink = `https://t.me/Peeppobot?start=ref${myId}`;
  openShareSheet(refLink, "Залетай в Peeppo — фарми, торгуй, обменивайся и крафти карточки! 🎮");
});

// initial load
loadProfile();

(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const DEV_USER = new URLSearchParams(location.search).get("dev");
  const COIN_FORMS = ["таджикоин", "таджикоина", "таджикоинов"];
  const SVG_NS = "http://www.w3.org/2000/svg";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const fmt = (n) => Math.round(n).toLocaleString("ru-RU");
  const reflow = (node) => void node.offsetWidth;

  function plural(n, forms) {
    const tens = Math.abs(n) % 100;
    const last = tens % 10;
    if (tens > 10 && tens < 20) return forms[2];
    if (last === 1) return forms[0];
    if (last >= 2 && last <= 4) return forms[1];
    return forms[2];
  }
  const coins = (n) => `${fmt(n)} ${plural(Math.round(n), COIN_FORMS)}`;
  const signedCoins = (n) => `${n >= 0 ? "+" : "−"}${coins(Math.abs(n))}`;
  const signedShort = (n) => `${n >= 0 ? "+" : "−"}${fmt(Math.abs(n))}`;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  const state = {
    loaded: false,
    balance: 0,
    shownBalance: 0,
    bet: 100,
    minBet: 10,
    busy: false,
    screen: "lobby",
    rouletteKind: "red",
    rouletteNumber: 17,
    coinSide: "heads",
    rrLeft: 6,
    rrReward: 50,
    rrCooldownUntil: 0,
  };

  // ─── Telegram ──────────────────────────────────────────────────────────────

  function setupTelegram() {
    if (!tg) return;
    const safe = (fn) => { try { fn(); } catch (error) { /* старый клиент Telegram */ } };
    safe(() => tg.ready());
    safe(() => tg.expand());
    safe(() => tg.setHeaderColor("#0a0f0c"));
    safe(() => tg.setBackgroundColor("#0a0f0c"));
    safe(() => tg.disableVerticalSwipes && tg.disableVerticalSwipes());
    safe(() => tg.BackButton.onClick(() => go("lobby")));
  }

  const haptic = {
    tap: () => { try { tg.HapticFeedback.selectionChanged(); } catch (error) { /* нет вибро */ } },
    impact: (style) => { try { tg.HapticFeedback.impactOccurred(style); } catch (error) { /* нет вибро */ } },
    notify: (type) => { try { tg.HapticFeedback.notificationOccurred(type); } catch (error) { /* нет вибро */ } },
  };

  // ─── Сервер ────────────────────────────────────────────────────────────────

  async function api(path, body = {}) {
    const headers = { "Content-Type": "application/json" };
    if (tg && tg.initData) headers["X-Init-Data"] = tg.initData;
    if (DEV_USER) headers["X-Dev-User"] = DEV_USER;
    let response;
    try {
      response = await fetch(`api/${path}`, { method: "POST", headers, body: JSON.stringify(body) });
    } catch (error) {
      throw new ApiError("Нет связи с казино — проверь интернет", 0);
    }
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      /* ответ не JSON */
    }
    if (!response.ok) throw new ApiError(data.error || "Казино временно закрыто", response.status);
    return data;
  }

  // ─── Общие кусочки интерфейса ──────────────────────────────────────────────

  let toastTimer = 0;
  function toast(text, kind = "") {
    const box = $("#toast");
    box.textContent = text;
    box.className = `toast ${kind}`;
    reflow(box);
    box.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove("show"), 2800);
  }

  function showError(error) {
    if (error.status === 401 || error.status === 403) {
      $("#gate-text").textContent = error.message;
      $("#gate").hidden = false;
      return;
    }
    toast(error.message || "Что-то пошло не так", "error");
    haptic.notify("error");
  }

  function setBalance(value, animate = true) {
    const from = state.shownBalance;
    state.balance = value;
    state.shownBalance = value;
    $("#hero-currency").textContent = plural(value, COIN_FORMS);
    const targets = [$("#balance"), $("#hero-balance")];
    if (!animate || from === value) {
      targets.forEach((node) => { node.textContent = fmt(value); });
    } else {
      const wallet = $("#wallet");
      wallet.classList.remove("up", "down");
      reflow(wallet);
      wallet.classList.add(value > from ? "up" : "down");
      const started = performance.now();
      const tick = (now) => {
        if (state.shownBalance !== value) return; // пришло новое значение — эта анимация уже не нужна
        const t = Math.min(1, (now - started) / 700);
        const current = from + (value - from) * (1 - Math.pow(1 - t, 3));
        targets.forEach((node) => { node.textContent = fmt(current); });
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }
    renderBet();
  }

  function setBusy(busy) {
    state.busy = busy;
    ["#roulette-play", "#slots-play", "#coin-play", "#bj-deal", "#bj-hit", "#bj-stand"].forEach((selector) => {
      $(selector).disabled = busy;
    });
    $("#bj-double").disabled = busy || !(bjHand && bjHand.active && bjHand.can_double && state.balance >= bjHand.bet);
    updateRussianButton();
  }

  function go(screen) {
    if (!$(`.screen[data-screen="${screen}"]`)) return;
    state.screen = screen;
    $$(".screen").forEach((node) => node.classList.toggle("active", node.dataset.screen === screen));
    $$(".tabbar button").forEach((node) => node.classList.toggle("active", node.dataset.tab === screen));
    window.scrollTo(0, 0);
    try {
      if (screen === "lobby") tg.BackButton.hide();
      else tg.BackButton.show();
    } catch (error) { /* вне Telegram */ }
    if (screen === "lobby" || screen === "rr") refresh();
  }

  // ─── Ставка ────────────────────────────────────────────────────────────────

  const CHIPS = [10, 50, 100, 500, 1000];

  function renderBet() {
    // пока баланс не пришёл с сервера, ставку сверху не ограничиваем — иначе она «сбросится» до минимума
    const max = state.loaded ? Math.max(state.minBet, state.balance) : Infinity;
    state.bet = Math.max(state.minBet, Math.min(max, Math.round(state.bet)));
    $$("[data-bet-control]").forEach((root) => {
      if (!root.firstElementChild) {
        root.innerHTML = `
          <div class="bet-row">
            <button class="bet-step" type="button" data-step="half" aria-label="Ставку пополам">½</button>
            <div class="bet-value"><span class="bet-amount"></span><small>ставка</small></div>
            <button class="bet-step" type="button" data-step="double" aria-label="Удвоить ставку">×2</button>
          </div>
          <div class="chips">
            ${CHIPS.map((chip) => `<button class="chip" type="button" data-chip="${chip}">${chip >= 1000 ? `${chip / 1000}к` : chip}</button>`).join("")}
            <button class="chip allin" type="button" data-chip="all">Ва-банк</button>
          </div>`;
      }
      root.querySelector(".bet-amount").textContent = fmt(state.bet);
      root.querySelectorAll(".chip").forEach((chip) => {
        const value = chip.dataset.chip;
        const active = value === "all"
          ? state.balance >= state.minBet && state.bet === state.balance
          : Number(value) === state.bet;
        chip.classList.toggle("active", active);
      });
    });
    $("#roulette-play").textContent = `Крутить · ${fmt(state.bet)}`;
    $("#slots-play").textContent = `Крутить · ${fmt(state.bet)}`;
    $("#coin-play").textContent = `Подбросить · ${fmt(state.bet)}`;
    $("#bj-deal").textContent = `Раздать · ${fmt(state.bet)}`;
  }

  function onBetButton(button) {
    if (state.busy) return;
    if (button.dataset.step === "half") state.bet /= 2;
    else if (button.dataset.step === "double") state.bet *= 2;
    else if (button.dataset.chip === "all") state.bet = state.balance;
    else if (button.dataset.chip) state.bet = Number(button.dataset.chip);
    haptic.tap();
    renderBet();
  }

  // ─── Лобби ─────────────────────────────────────────────────────────────────

  function timeAgo(seconds) {
    if (seconds < 60) return "только что";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} мин`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч`;
    return `${Math.floor(seconds / 86400)} дн`;
  }

  function renderFeed(list, items, emptyText) {
    list.replaceChildren();
    if (!items.length) {
      if (emptyText) list.append(el("li", "empty", emptyText));
      return;
    }
    items.forEach((item) => {
      const row = el("li");
      row.append(el("span", "text", item.text), el("span", "ago", timeAgo(item.ago)));
      list.append(row);
    });
  }

  function renderState(data) {
    state.minBet = data.min_bet;
    const firstLoad = !state.loaded;
    state.loaded = true;
    setBalance(data.balance, !firstLoad);

    $("#hello").textContent = `Привет, ${data.user.name}!`;
    $("#hero-place").textContent = `${data.place}-е место в Форбсе`;
    $("#spam-banner").hidden = !data.spam_day;

    const bonus = $("#bonus-btn");
    bonus.disabled = !data.bonus_available;
    $("#bonus-title").textContent = data.bonus_available ? "Забрать ежедневный бонус" : "Бонус уже получен";
    $("#bonus-sub").textContent = data.bonus_available ? "150–400 таджикоинов раз в день" : "Следующий — завтра";

    const tajik = $("#tajik-banner");
    tajik.hidden = !data.tajik.name;
    tajik.classList.toggle("me", data.tajik.is_me);
    tajik.textContent = data.tajik.is_me
      ? `👑 Сегодня таджик дня — ты! Приз ${coins(data.tajik.prize)} уже в кошельке`
      : `👑 Таджик дня сегодня: ${data.tajik.name}`;

    const top = $("#top-list");
    top.replaceChildren();
    if (!data.top.length) top.append(el("li", "empty", "Здесь появятся самые богатые"));
    data.top.forEach((row, index) => {
      const item = el("li", row.me ? "me" : "");
      if (row.username) item.dataset.username = row.username; // нажатие откроет профиль в Telegram
      item.append(
        el("span", "place", ["🥇", "🥈", "🥉"][index] || String(index + 1)),
        el("span", "who", row.name),
        el("span", "sum", fmt(row.balance)),
      );
      top.append(item);
    });

    renderFeed($("#feed"), data.feed, "Пока тихо. Сыграй первым!");
    renderFeed($("#rr-feed"), data.feed.filter((item) => item.text.startsWith("💥") || item.text.startsWith("🔫")).slice(0, 6));

    const stats = $("#my-stats");
    stats.replaceChildren();
    [["Игр", fmt(data.stats.games)], ["Выиграно", fmt(data.stats.won)], ["Проиграно", fmt(data.stats.lost)]].forEach(([label, value]) => {
      const box = el("div");
      box.append(el("small", "", label), el("b", "", value));
      stats.append(box);
    });

    state.donate = data.donate;
    $("#donate-link").hidden = !data.donate;

    if (!state.busy) {
      state.rrLeft = data.rr.chambers_left;
      state.rrReward = data.rr.reward;
      state.rrCooldownUntil = Date.now() + data.rr.cooldown * 1000;
      syncRevolver(6 - data.rr.chambers_left);
      renderRussianInfo();
      if (data.blackjack && !(bjHand && bjHand.active)) {
        clearBlackjackTable(); // недоигранная раздача — показываем её как была
        renderBlackjack(data.blackjack, false);
      }
    }
  }

  let refreshing = null;
  function refresh() {
    if (!refreshing) {
      refreshing = api("state")
        .then(renderState)
        .catch((error) => { if (!state.loaded || error.status === 401 || error.status === 403) showError(error); })
        .finally(() => { refreshing = null; });
    }
    return refreshing;
  }

  async function claimBonus() {
    const button = $("#bonus-btn");
    if (button.disabled) return;
    button.disabled = true;
    try {
      const result = await api("bonus");
      haptic.notify("success");
      confetti.burst(90);
      toast(`${result.source}: ${signedCoins(result.amount)}`, "win");
      setBalance(result.balance);
    } catch (error) {
      showError(error);
    }
    refresh();
  }

  // ─── Рулетка ───────────────────────────────────────────────────────────────

  const WHEEL = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26];
  const RED = new Set([1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]);
  const colorOf = (n) => (n === 0 ? "green" : RED.has(n) ? "red" : "black");
  const KINDS = [
    ["red", "Красное", "×2"], ["black", "Чёрное", "×2"], ["even", "Чёт", "×2"], ["odd", "Нечет", "×2"], ["number", "Число", "×36"],
    ["low", "1–18", "×2"], ["high", "19–36", "×2"], ["dozen1", "1–12", "×3"], ["dozen2", "13–24", "×3"], ["dozen3", "25–36", "×3"],
  ];

  function svg(tag, attributes) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  function polar(cx, cy, radius, degrees) {
    const radians = ((degrees - 90) * Math.PI) / 180;
    return [cx + radius * Math.cos(radians), cy + radius * Math.sin(radians)];
  }

  function buildWheel() {
    const rotor = $("#wheel-rotor");
    const step = 360 / WHEEL.length;
    const outer = 150;
    const inner = 104;
    WHEEL.forEach((number, index) => {
      const [x1, y1] = polar(160, 160, outer, (index - 0.5) * step);
      const [x2, y2] = polar(160, 160, outer, (index + 0.5) * step);
      const [x3, y3] = polar(160, 160, inner, (index + 0.5) * step);
      const [x4, y4] = polar(160, 160, inner, (index - 0.5) * step);
      rotor.append(svg("path", {
        d: `M${x1} ${y1}A${outer} ${outer} 0 0 1 ${x2} ${y2}L${x3} ${y3}A${inner} ${inner} 0 0 0 ${x4} ${y4}Z`,
        class: `pocket ${colorOf(number)}`,
      }));
      const [tx, ty] = polar(160, 160, 134, index * step);
      const label = svg("text", {
        x: tx.toFixed(2), y: ty.toFixed(2), class: "pocket-label",
        transform: `rotate(${(index * step).toFixed(2)} ${tx.toFixed(2)} ${ty.toFixed(2)})`,
      });
      label.textContent = number;
      rotor.append(label);
    });
    rotor.append(svg("circle", { cx: 160, cy: 160, r: inner, fill: "url(#cone-grad)", stroke: "#d9a32b", "stroke-width": 2 }));
    for (let i = 0; i < 4; i += 1) {
      const [x1, y1] = polar(160, 160, 30, i * 90 + 45);
      const [x2, y2] = polar(160, 160, 92, i * 90 + 45);
      rotor.append(svg("line", { x1, y1, x2, y2, stroke: "#e2ad3a", "stroke-width": 5, "stroke-linecap": "round" }));
    }
    rotor.append(svg("circle", { cx: 160, cy: 160, r: 34, fill: "url(#hub-grad)", stroke: "#8a5d12", "stroke-width": 2 }));
    const hub = svg("text", { x: 160, y: 162, "text-anchor": "middle", "dominant-baseline": "central", "font-size": 30 });
    hub.textContent = "🍲";
    rotor.append(hub);
    $("#ball-dot").setAttribute("cy", 160 - 116);
  }

  function buildRouletteControls() {
    const kinds = $("#bet-kinds");
    KINDS.forEach(([kind, label, pays]) => {
      const button = el("button", `kind ${kind === "red" || kind === "black" ? kind : ""}`);
      button.type = "button";
      button.dataset.kind = kind;
      button.append(el("span", "", label), el("small", "", pays));
      kinds.append(button);
    });
    kinds.addEventListener("click", (event) => {
      const button = event.target.closest("[data-kind]");
      if (!button || state.busy) return;
      state.rouletteKind = button.dataset.kind;
      haptic.tap();
      renderRouletteControls();
    });

    const numbers = $("#numbers");
    for (let n = 0; n <= 36; n += 1) {
      const button = el("button", colorOf(n), String(n));
      button.type = "button";
      button.dataset.number = n;
      numbers.append(button);
    }
    numbers.addEventListener("click", (event) => {
      const button = event.target.closest("[data-number]");
      if (!button || state.busy) return;
      state.rouletteNumber = Number(button.dataset.number);
      haptic.tap();
      renderRouletteControls();
    });
    renderRouletteControls();
  }

  function renderRouletteControls() {
    $$("#bet-kinds .kind").forEach((button) => button.classList.toggle("active", button.dataset.kind === state.rouletteKind));
    $("#numbers").hidden = state.rouletteKind !== "number";
    $$("#numbers button").forEach((button) => button.classList.toggle("active", Number(button.dataset.number) === state.rouletteNumber));
    $('#bet-kinds [data-kind="number"] small').textContent = state.rouletteKind === "number" ? `на ${state.rouletteNumber}` : "×36";
  }

  let rotorAngle = 0;
  let ballAngle = 0;

  async function playRoulette() {
    if (state.busy) return;
    const body = { bet: state.bet, kind: state.rouletteKind };
    if (state.rouletteKind === "number") body.number = state.rouletteNumber;
    setBusy(true);
    try {
      const result = await api("roulette", body);
      setBalance(state.balance - body.bet);
      haptic.impact("medium");
      $("#wheel-result").hidden = true;

      const step = 360 / WHEEL.length;
      const target = (360 - WHEEL.indexOf(result.result) * step) % 360;
      rotorAngle += 360 * 5 + ((target - (rotorAngle % 360)) % 360 + 360) % 360;
      ballAngle -= 360 * 7;
      const dot = $("#ball-dot");
      dot.style.transition = "transform .3s ease-out";
      dot.style.transform = "translateY(-24px)";
      $("#wheel-rotor").style.transform = `rotate(${rotorAngle}deg)`;
      $("#wheel-ball").style.transform = `rotate(${ballAngle}deg)`;

      await wait(3400);
      dot.style.transition = "transform .9s cubic-bezier(.3,1.8,.5,1)";
      dot.style.transform = "translateY(0)";
      haptic.impact("light");
      await wait(1350);
      showRouletteResult(result, body.bet);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  function showRouletteResult(result, bet) {
    const box = $("#wheel-result");
    box.className = `wheel-result ${result.color}`;
    box.replaceChildren(el("div", "num", String(result.result)), el("div", `net ${result.won ? "win" : "lose"}`, signedShort(result.net)));
    box.hidden = false;

    const history = $("#roulette-history");
    history.prepend(el("span", result.color, String(result.result)));
    while (history.children.length > 10) history.lastElementChild.remove();

    setBalance(result.balance);
    if (!result.won) {
      haptic.notify("warning");
    } else if (result.payout >= bet * 36) {
      haptic.notify("success");
      confetti.burst(160);
      toast(`💥 В яблочко! ${signedCoins(result.net)}`, "win");
    } else {
      haptic.notify("success");
      if (result.payout >= bet * 3) confetti.burst(60);
    }
  }

  // ─── Слоты ─────────────────────────────────────────────────────────────────

  const SYMBOLS = { cherry: "🍒", lemon: "🍋", grapes: "🍇", bell: "🔔", diamond: "💎", seven: "7️⃣" };
  const SYMBOL_KEYS = Object.keys(SYMBOLS);
  const randomSymbol = () => SYMBOL_KEYS[Math.floor(Math.random() * SYMBOL_KEYS.length)];

  function fillStrip(strip, keys) {
    strip.replaceChildren(...keys.map((key) => {
      const cell = el("div", "cell", SYMBOLS[key]);
      cell.dataset.symbol = key;
      return cell;
    }));
  }

  function buildReels() {
    $$("#reels .strip").forEach((strip) => fillStrip(strip, [randomSymbol(), randomSymbol(), randomSymbol()]));
  }

  function spinReel(strip, target, duration) {
    const cellHeight = strip.firstElementChild.getBoundingClientRect().height || 70;
    const keys = Array.from(strip.children).slice(-3).map((cell) => cell.dataset.symbol);
    const length = 24 + Math.floor(Math.random() * 6);
    while (keys.length < length - 2) keys.push(randomSymbol());
    keys.push(target, randomSymbol()); // выпавший символ встаёт ровно на линию выплат
    fillStrip(strip, keys);
    strip.style.transition = "none";
    strip.style.transform = "translateY(0)";
    reflow(strip);
    strip.classList.add("blur");
    strip.style.transition = `transform ${duration}ms cubic-bezier(.15,.85,.3,1.04)`;
    strip.style.transform = `translateY(${-(keys.length - 3) * cellHeight}px)`;
    setTimeout(() => strip.classList.remove("blur"), duration - 400);
    return wait(duration);
  }

  async function playSlots() {
    if (state.busy) return;
    const machine = $(".machine");
    const message = $("#slots-message");
    const bet = state.bet;
    setBusy(true);
    machine.classList.remove("win");
    $$("#reels .cell.hit").forEach((cell) => cell.classList.remove("hit"));
    try {
      const result = await api("slots", { bet });
      setBalance(state.balance - bet);
      haptic.impact("medium");
      machine.classList.add("spinning");
      message.className = "machine-message";
      message.textContent = "Крутим…";
      const strips = $$("#reels .strip");
      await Promise.all(strips.map((strip, index) =>
        spinReel(strip, result.reels[index], 1400 + index * 550).then(() => haptic.impact("light"))));
      machine.classList.remove("spinning");
      showSlotsResult(result, strips);
    } catch (error) {
      machine.classList.remove("spinning");
      message.className = "machine-message";
      message.textContent = "Крути барабаны!";
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  function showSlotsResult(result, strips) {
    const message = $("#slots-message");
    const line = strips.map((strip) => strip.children[strip.children.length - 2]);
    setBalance(result.balance);

    if (result.combo === "lose") {
      message.className = "machine-message lose";
      message.textContent = `Мимо… ${signedCoins(result.net)}`;
      haptic.notify("warning");
      return;
    }
    const counts = {};
    result.reels.forEach((key) => { counts[key] = (counts[key] || 0) + 1; });
    const winning = result.combo === "sevens" ? "seven" : Object.keys(counts).find((key) => counts[key] >= 2);
    line.forEach((cell) => cell.classList.toggle("hit", cell.dataset.symbol === winning));
    haptic.notify("success");

    if (result.combo === "pair") {
      message.className = "machine-message";
      message.textContent = "Пара — ставка вернулась";
      return;
    }
    $(".machine").classList.add("win");
    if (result.combo === "jackpot") {
      message.className = "machine-message jackpot";
      message.textContent = `💎 ДЖЕКПОТ! ${signedCoins(result.net)}`;
      confetti.burst(220);
      setTimeout(() => confetti.burst(160), 600);
    } else {
      message.className = "machine-message win";
      message.textContent = `${result.combo === "sevens" ? "Две семёрки" : "Три в ряд"} ×${result.multiplier} · ${signedCoins(result.net)}`;
      confetti.burst(result.multiplier >= 20 ? 160 : 70);
    }
    setTimeout(() => $(".machine").classList.remove("win"), 2500);
  }

  // ─── Монетка ───────────────────────────────────────────────────────────────

  let coinAngle = 0;

  async function playCoin() {
    if (state.busy) return;
    const bet = state.bet;
    const side = state.coinSide;
    const coin = $("#coin");
    const toss = $("#coin-toss");
    const message = $("#coin-message");
    setBusy(true);
    try {
      const result = await api("coin", { bet, side });
      setBalance(state.balance - bet);
      haptic.impact("medium");
      message.className = "coin-message";
      message.textContent = "Летит…";

      coin.classList.remove("stolen");
      const base = ((coinAngle % 360) + 360) % 360;
      coin.style.transition = "none";
      coin.style.transform = `rotateY(${base}deg)`;
      reflow(coin);
      const face = result.result === "tails" ? 180 : result.result === "edge" ? 90 : 0;
      coinAngle = base + 360 * 5 + ((face - base) % 360 + 360) % 360;
      coin.style.transition = "";
      coin.style.transform = `rotateY(${coinAngle}deg)`;
      toss.classList.remove("tossing");
      reflow(toss);
      toss.classList.add("tossing");

      if (result.result === "stolen") {
        await wait(600);
        coin.classList.add("stolen");
        await wait(1300);
      } else {
        await wait(1750);
      }
      setBalance(result.balance);
      if (result.result === "edge") {
        message.className = "coin-message win";
        message.textContent = "🪙 Встала на ребро! Ставка вернулась";
        confetti.burst(120);
      } else if (result.result === "stolen") {
        message.className = "coin-message lose";
        message.textContent = `🇺🇿 Узбеки украли монетку! ${signedCoins(result.net)}`;
        haptic.notify("error");
      } else {
        const title = result.result === "heads" ? "🦅 Орёл" : "👑 Решка";
        message.className = `coin-message ${result.won ? "win" : "lose"}`;
        message.textContent = `${title}! ${signedCoins(result.net)}`;
        haptic.notify(result.won ? "success" : "warning");
      }
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  // ─── Русская рулетка ───────────────────────────────────────────────────────

  const revolver = { steps: 0, reloadAt: 0, fired: 0 };

  function buildRevolver() {
    const chambers = $("#chambers");
    for (let i = 0; i < 6; i += 1) {
      const [cx, cy] = polar(120, 120, 62, i * 60);
      chambers.append(svg("circle", { cx: cx.toFixed(2), cy: cy.toFixed(2), r: 23, class: "chamber" }));
    }
  }

  function syncRevolver(fired, reloaded = false) {
    if (reloaded || fired < revolver.fired) {
      revolver.steps += 6; // перезарядка — барабан делает полный оборот
      revolver.reloadAt = revolver.steps;
    }
    revolver.fired = fired;
    revolver.steps = Math.max(revolver.steps, revolver.reloadAt + fired);
    $("#cylinder-rotor").style.transform = `rotate(${-revolver.steps * 60}deg)`;
    const top = revolver.steps % 6;
    $$("#chambers .chamber").forEach((hole, index) => {
      hole.classList.toggle("spent", (index - (revolver.reloadAt % 6) + 6) % 6 < fired);
      hole.classList.toggle("next", index === top);
    });
  }

  function renderRussianInfo() {
    $("#rr-left").textContent = state.rrLeft;
    $("#rr-chance").textContent = `1/${state.rrLeft}`;
    $("#rr-reward").textContent = `+${fmt(state.rrReward)}`;
    updateRussianButton();
  }

  function updateRussianButton() {
    const button = $("#rr-play");
    const left = Math.ceil((state.rrCooldownUntil - Date.now()) / 1000);
    if (left > 0) {
      button.disabled = true;
      button.textContent = `Перезарядка ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
    } else {
      button.disabled = state.busy;
      button.textContent = "Нажать на спуск";
    }
  }

  function bang() {
    haptic.impact("heavy");
    haptic.notify("error");
    const flash = $("#flash");
    flash.classList.remove("bang");
    reflow(flash);
    flash.classList.add("bang");
    document.body.classList.remove("dead");
    reflow(document.body);
    document.body.classList.add("dead");
    const main = $("main");
    main.classList.remove("shake");
    reflow(main);
    main.classList.add("shake");
    setTimeout(() => document.body.classList.remove("dead"), 2300);
  }

  async function playRussian() {
    if (state.busy || Date.now() < state.rrCooldownUntil) return;
    const stage = $("#rr-stage");
    const message = $("#rr-message");
    setBusy(true);
    try {
      const result = await api("rr");
      message.className = "rr-message";
      message.textContent = "Палец на спусковом крючке…";
      stage.classList.add("aiming");
      haptic.impact("light");
      await wait(1400);
      stage.classList.remove("aiming");

      if (result.dead) {
        const chamber = $("#chambers .chamber.next");
        if (chamber) chamber.style.fill = "#d32f2f";
        bang();
        message.className = "rr-message dead";
        message.textContent = "💥 БАХ!";
        setBalance(result.balance);
        await wait(1600);
        toast(`⚰️ На похороны: ${signedCoins(result.delta)}`, "error");
        if (chamber) chamber.style.fill = "";
        syncRevolver(0, true);
        message.className = "rr-message";
        message.textContent = "Барабан перезаряжен. Кто следующий?";
      } else {
        haptic.notify("success");
        message.className = "rr-message alive";
        message.textContent = `Щёлк… живой! ${signedCoins(result.delta)}`;
        setBalance(result.balance);
        syncRevolver(6 - result.chambers_left);
      }
      state.rrLeft = result.chambers_left;
      state.rrReward = result.next_reward;
      state.rrCooldownUntil = Date.now() + result.cooldown * 1000;
    } catch (error) {
      stage.classList.remove("aiming");
      showError(error);
    } finally {
      setBusy(false);
      renderRussianInfo();
      refresh();
    }
  }

  // ─── Блэкджек ──────────────────────────────────────────────────────────────

  const SUITS = { S: "♠", H: "♥", D: "♦", C: "♣" };
  let bjHand = null; // последний ответ сервера о раздаче

  function fillCard(card, code) {
    const rank = code.slice(0, -1);
    const suit = code.slice(-1);
    card.classList.remove("back");
    card.classList.toggle("red", suit === "H" || suit === "D");
    card.replaceChildren(
      el("span", "corner", `${rank}${SUITS[suit]}`),
      el("span", "pip", SUITS[suit]),
      el("span", "corner bottom", `${rank}${SUITS[suit]}`),
    );
  }

  function makeCard(code, animate) {
    const card = el("div", animate ? "pcard deal" : "pcard");
    if (code) fillCard(card, code);
    else card.classList.add("back");
    return card;
  }

  function clearBlackjackTable() {
    $("#bj-dealer").replaceChildren();
    $("#bj-player").replaceChildren();
    $("#bj-dealer-total").hidden = true;
    $("#bj-player-total").hidden = true;
  }

  function renderTotals(view) {
    const player = $("#bj-player-total");
    player.hidden = !view.player.length;
    player.textContent = view.player_soft && view.player_total < 21
      ? `${view.player_total - 10}/${view.player_total}`
      : view.player_total;
    player.classList.toggle("bust", view.player_total > 21);
    const dealer = $("#bj-dealer-total");
    dealer.hidden = !view.dealer.length;
    dealer.textContent = view.dealer_total;
    dealer.classList.toggle("bust", view.dealer_total > 21);
  }

  async function renderBlackjack(view, animate) {
    const dealerBox = $("#bj-dealer");
    const playerBox = $("#bj-player");
    const pause = () => (animate ? wait(300) : Promise.resolve());
    // карты раздаются по очереди: игроку, дилеру, игроку, дилеру…
    for (let i = 0; i < Math.max(view.player.length, view.dealer.length); i += 1) {
      if (i < view.player.length && !playerBox.children[i]) {
        playerBox.append(makeCard(view.player[i], animate));
        if (animate) haptic.impact("light");
        await pause();
      }
      if (i >= view.dealer.length) continue;
      const existing = dealerBox.children[i];
      if (!existing) {
        dealerBox.append(makeCard(view.dealer[i], animate));
        if (animate) haptic.impact("light");
        await pause();
      } else if (existing.classList.contains("back") && view.dealer[i]) {
        existing.classList.remove("deal", "flip");
        reflow(existing);
        existing.classList.add("flip");
        if (animate) await wait(220);
        fillCard(existing, view.dealer[i]); // переворачиваем закрытую карту дилера
        await pause();
      }
    }
    renderTotals(view);

    bjHand = view;
    $("#bj-actions").hidden = !view.active;
    $("#bj-betting").hidden = view.active;
    const message = $("#bj-message");
    if (view.active) {
      message.className = "bj-message";
      message.textContent = view.player_total === 21 ? "21! Жми «Хватит»" : "Ещё карту или хватит?";
      return;
    }
    setBalance(view.balance, animate);
    message.textContent = `${view.message} · ${signedCoins(view.net)}`;
    if (view.outcome === "blackjack") {
      message.className = "bj-message big";
      haptic.notify("success");
      confetti.burst(200);
    } else if (view.net > 0) {
      message.className = "bj-message win";
      haptic.notify("success");
      confetti.burst(70);
    } else {
      message.className = `bj-message ${view.net < 0 ? "lose" : ""}`;
      haptic.notify(view.net < 0 ? "warning" : "success");
    }
  }

  async function blackjackDeal() {
    if (state.busy) return;
    const bet = state.bet;
    setBusy(true);
    try {
      const view = await api("blackjack/start", { bet });
      clearBlackjackTable();
      setBalance(state.balance - bet);
      haptic.impact("medium");
      $("#bj-message").className = "bj-message";
      $("#bj-message").textContent = "Раздаю…";
      await renderBlackjack(view, true);
    } catch (error) {
      showError(error);
      if (error.status === 409) refresh(); // осталась недоигранная раздача — подтянем её
    } finally {
      setBusy(false);
    }
  }

  async function blackjackAction(action) {
    if (state.busy || !bjHand || !bjHand.active) return;
    const stake = bjHand.bet;
    setBusy(true);
    try {
      const view = await api(`blackjack/${action}`);
      if (action === "double") setBalance(state.balance - stake);
      haptic.impact(action === "hit" ? "light" : "medium");
      if (action !== "hit") $("#bj-message").textContent = "Ход дилера…";
      await renderBlackjack(view, true);
    } catch (error) {
      showError(error);
      if (error.status === 409) {
        bjHand = null;
        refresh();
      }
    } finally {
      setBusy(false);
    }
  }

  // ─── Донаты ────────────────────────────────────────────────────────────────

  function openDonate() {
    if (!state.donate) return;
    $("#donate-phone").textContent = state.donate.pretty;
    const bank = $("#donate-bank");
    bank.hidden = !state.donate.bank;
    bank.textContent = state.donate.bank ? `Банк: ${state.donate.bank}` : "";
    $("#donate-sheet").hidden = false;
    haptic.tap();
  }

  function closeDonate() {
    $("#donate-sheet").hidden = true;
  }

  async function copyDonateDetails() {
    try {
      await navigator.clipboard.writeText(state.donate ? state.donate.phone : "");
      toast("Номер скопирован 📋");
    } catch (error) {
      const range = document.createRange();
      range.selectNodeContents($("#donate-phone"));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      toast("Выделил — скопируй вручную");
    }
  }

  async function sendDonate(event) {
    event.preventDefault();
    const amount = Number($("#donate-amount").value);
    if (!Number.isInteger(amount) || amount < 10) {
      toast("Сумма — от 10 ₽", "error");
      return;
    }
    const button = $("#donate-send");
    button.disabled = true;
    try {
      await api("donate", { amount, message: $("#donate-message").value });
      closeDonate();
      $("#donate-form").reset();
      haptic.notify("success");
      toast("❤️ Спасибо! Донат появится в беседе после проверки", "win");
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  }

  // ─── Конфетти ──────────────────────────────────────────────────────────────

  const confetti = (() => {
    const canvas = $("#confetti");
    const ctx = canvas.getContext("2d");
    const colors = ["#f5c542", "#ffe08a", "#54e08a", "#e23c3c", "#ffffff", "#b388ff"];
    let particles = [];
    let running = false;

    function resize() {
      canvas.width = window.innerWidth * window.devicePixelRatio;
      canvas.height = window.innerHeight * window.devicePixelRatio;
    }
    window.addEventListener("resize", resize);
    resize();

    function frame() {
      const scale = window.devicePixelRatio;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      particles = particles.filter((p) => p.life < 150 && p.y < canvas.height + 50);
      particles.forEach((p) => {
        p.life += 1;
        p.vy += 0.32 * scale;
        p.vx *= 0.99;
        p.x += p.vx;
        p.y += p.vy;
        p.rotation += p.spin;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rotation);
        ctx.globalAlpha = Math.max(0, 1 - p.life / 150);
        ctx.fillStyle = p.color;
        ctx.fillRect(-p.size / 2, -p.size / 3, p.size, p.size * 0.6);
        ctx.restore();
      });
      if (particles.length) {
        requestAnimationFrame(frame);
      } else {
        running = false;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }

    function burst(count) {
      const scale = window.devicePixelRatio;
      for (let i = 0; i < count; i += 1) {
        particles.push({
          x: canvas.width * (0.35 + Math.random() * 0.3),
          y: canvas.height * 0.38,
          vx: (Math.random() - 0.5) * 16 * scale,
          vy: (-6 - Math.random() * 13) * scale,
          size: (5 + Math.random() * 6) * scale,
          rotation: Math.random() * Math.PI,
          spin: (Math.random() - 0.5) * 0.35,
          color: colors[i % colors.length],
          life: 0,
        });
      }
      if (!running) {
        running = true;
        requestAnimationFrame(frame);
      }
    }

    return { burst };
  })();

  // ─── Запуск ────────────────────────────────────────────────────────────────

  function bindEvents() {
    document.addEventListener("click", (event) => {
      const tab = event.target.closest("[data-tab]");
      if (tab) {
        haptic.tap();
        go(tab.dataset.tab);
        return;
      }
      const tile = event.target.closest("[data-go]");
      if (tile) {
        haptic.tap();
        go(tile.dataset.go);
        return;
      }
      const betButton = event.target.closest("[data-bet-control] button");
      if (betButton) {
        onBetButton(betButton);
        return;
      }
      const sideButton = event.target.closest("#coin-side [data-side]");
      if (sideButton && !state.busy) {
        state.coinSide = sideButton.dataset.side;
        $$("#coin-side button").forEach((button) => button.classList.toggle("active", button === sideButton));
        haptic.tap();
      }
    });
    $("#bonus-btn").addEventListener("click", claimBonus);
    $("#roulette-play").addEventListener("click", playRoulette);
    $("#slots-play").addEventListener("click", playSlots);
    $("#coin-play").addEventListener("click", playCoin);
    $("#rr-play").addEventListener("click", playRussian);
    $("#bj-deal").addEventListener("click", blackjackDeal);
    $("#bj-hit").addEventListener("click", () => blackjackAction("hit"));
    $("#bj-stand").addEventListener("click", () => blackjackAction("stand"));
    $("#bj-double").addEventListener("click", () => blackjackAction("double"));
    $("#donate-link").addEventListener("click", openDonate);
    $("#donate-copy").addEventListener("click", copyDonateDetails);
    $("#donate-form").addEventListener("submit", sendDonate);
    $("#donate-sheet").addEventListener("click", (event) => {
      if (event.target.id === "donate-sheet") closeDonate();
    });
    $("#top-list").addEventListener("click", (event) => {
      const row = event.target.closest("[data-username]");
      if (!row) return;
      const url = `https://t.me/${row.dataset.username}`;
      try {
        tg.openTelegramLink(url);
      } catch (error) {
        window.open(url, "_blank");
      }
    });
    $("#gate-retry").addEventListener("click", () => {
      $("#gate").hidden = true;
      refresh();
    });
  }

  setupTelegram();
  buildWheel();
  buildRouletteControls();
  buildReels();
  buildRevolver();
  syncRevolver(0);
  renderBet();
  bindEvents();
  refresh();

  setInterval(updateRussianButton, 1000);
  setInterval(() => {
    if (!state.busy && document.visibilityState === "visible" && (state.screen === "lobby" || state.screen === "rr")) refresh();
  }, 20000);
})();

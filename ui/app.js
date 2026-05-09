let __halted = false;

async function fetchJson(url, options = {}) {
  if (__halted) throw new Error("halted");
  const response = await fetch(url, options);
  if (response.status === 401) {
    __halted = true;
    window.location.replace("/ui/login.html");
    throw new Error("not-authenticated");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || JSON.stringify(data) || "Request failed");
  return data;
}

async function refreshAll() {
  await loadSessionInfo();
  await Promise.all([
    loadWatchlist(),
    loadOutstanding(),
    loadTradeLogs(),
    loadActivityLogs(),
    loadPositions(),
    loadAccountSummary(),
    loadTopMovers(currentMoversMode),
    loadExchangeState(),
    loadIndices(),
    loadConsolidated(),
    loadCash(),
    loadWithdrawals(),
    loadExposureSummary(),
    loadExposureCat(currentExpCatMode),
  ]);
}

// ── Session ─────────────────────────────────────────────────────

function applyMarketStatus(status) {
  const el = document.getElementById("session-market");
  const value = String(status || "-").toUpperCase();
  el.textContent = value;
  el.classList.remove("open", "closed");
  if (value === "OPEN") el.classList.add("open");
  else if (value === "CLOSED") el.classList.add("closed");
}

async function loadSessionInfo() {
  try {
    const data = await fetchJson("/session/info");
    document.getElementById("session-user").textContent = data.user_name || data.user_id || "-";
    applyMarketStatus(data.market_status);
    return true;
  } catch {
    document.getElementById("session-user").textContent = "Not connected";
    return false;
  }
}

// ── Symbols ──────────────────────────────────────────────────────

let allSymbols = [];

function renderSymbolSuggestions(items) {
  const list = document.getElementById("symbol-suggestions");
  list.innerHTML = "";
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = item.symbol || "";
    opt.label = [item.name, item.sector].filter(Boolean).join(" - ");
    list.appendChild(opt);
  }
}

function refreshSymbolSuggestionsFromInput() {
  const q = document.getElementById("buy-symbol").value.trim().toLowerCase();
  if (!q) { renderSymbolSuggestions(allSymbols.slice(0, 200)); return; }
  const filtered = allSymbols.filter(s =>
    (s.symbol || "").toLowerCase().includes(q) || (s.name || "").toLowerCase().includes(q)
  );
  renderSymbolSuggestions(filtered.slice(0, 200));
}

async function loadSymbols() {
  try {
    const data = await fetchJson("/symbols?limit=1000");
    allSymbols = data.items || [];
    refreshSymbolSuggestionsFromInput();
  } catch { allSymbols = []; renderSymbolSuggestions([]); }
}

// ── Watchlist ────────────────────────────────────────────────────

function renderWatchlist(items) {
  const tbody = document.querySelector("#watchlist-table tbody");
  tbody.innerHTML = "";
  for (const item of items) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${item.symbol || ""}</td>
      <td>${item.last || ""}</td>
      <td>${item.bid || ""}</td>
      <td>${item.ask || ""}</td>
      <td>${item.change || ""}</td>
      <td>${item.volume || ""}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadWatchlist() {
  try { renderWatchlist((await fetchJson("/watchlist")).items || []); }
  catch (e) { console.warn("watchlist:", e.message); }
}

// ── Account Summary ───────────────────────────────────────────────

const SUMMARY_LABELS = {
  ledger_balance:          "Ledger Balance",
  net_worth:               "Net Worth",
  realized_pl:             "Realized P/L",
  inventory_realized_pl:   "Inventory Real. P/L",
  inventory_unrealized_pl: "Inventory Unreal. P/L",
  unrealized_mtm_profit:   "Unreal. MTM Profit",
  unrealized_mtm_loss:     "Unreal. MTM Loss",
  account_percentage:      "Account %",
  pending_buy:             "Pending Buy",
  pending_sell:            "Pending Sell",
  current_buy:             "Current Buy",
  current_sell:            "Current Sell",
  open_position:           "Open Position",
  cash_margin_req:         "Cash Margin Req",
  collateral_margin_req:   "Collateral Margin",
  held_amount:             "Held Amount",
  expense_amount:          "Expense Amt",
  cdc_amount:              "CDC Amount",
  cdc_amount_hc:           "CDC Amt (HC)",
  loan_amount:             "Loan Amount",
  cash_block:              "Cash Block",
  cash_withdrawal:         "Cash Withdrawal",
  inventory_sold:          "Inventory Sold",
  client_name:             "Client Name",
  dealer_code:             "Dealer Code",
  cdc_id:                  "CDC ID",
  email:                   "Email",
  mobile:                  "Mobile",
};

function renderAccountSummary(data) {
  const wrap = document.getElementById("account-summary-wrap");
  wrap.innerHTML = "";
  if (!data || !Object.keys(data).length) {
    wrap.innerHTML = '<span class="empty">No data</span>';
    return;
  }
  let rendered = 0;
  for (const [key, label] of Object.entries(SUMMARY_LABELS)) {
    const val = data[key];
    if (val == null) continue;
    const item = document.createElement("div");
    item.className = "summary-item";
    const num = parseFloat(val);
    const colored = !isNaN(num) ? (num < 0 ? "neg" : num > 0 ? "pos" : "") : "";
    item.innerHTML = `<span class="summary-label">${label}</span>
      <span class="summary-value ${colored}">${isNaN(num) ? val : num.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</span>`;
    wrap.appendChild(item);
    rendered++;
  }
  if (!rendered) {
    wrap.innerHTML =
      '<span class="empty">Broker returned summary fields but all values were empty — try Refresh or re-login.</span>';
  }
}

async function loadAccountSummary() {
  try { renderAccountSummary(await fetchJson("/account/summary")); }
  catch (e) { console.warn("account-summary:", e.message); }
}

// ── Open Positions ────────────────────────────────────────────────

function renderPositions(items) {
  const tbody = document.querySelector("#positions-table tbody");
  tbody.innerHTML = "";
  if (!items.length) { tbody.innerHTML = '<tr><td colspan="10" class="empty">No open positions</td></tr>'; return; }
  for (const p of items) {
    const tr = document.createElement("tr");
    const mtm = parseFloat(p.total_mtm) || 0;
    const mtmCls = mtm < 0 ? "neg" : mtm > 0 ? "pos" : "";
    tr.innerHTML = `
      <td><strong>${p.symbol || ""}</strong></td>
      <td>${p.market || ""}</td>
      <td>${p.net_position ?? ""}</td>
      <td>${p.market_rate ?? ""}</td>
      <td>${p.open_avg_price ?? ""}</td>
      <td>${p.open_volume ?? ""}</td>
      <td>${p.inventory_holdings ?? ""}</td>
      <td class="${p.unrealized_mtm < 0 ? "neg" : "pos"}">${fmt2(p.unrealized_mtm)}</td>
      <td class="${p.realized_gain_loss < 0 ? "neg" : "pos"}">${fmt2(p.realized_gain_loss)}</td>
      <td class="${mtmCls}">${fmt2(p.total_mtm)}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadPositions() {
  try { renderPositions((await fetchJson("/account/positions")).positions || []); }
  catch (e) { console.warn("positions:", e.message); }
}

// ── Top Movers ────────────────────────────────────────────────────

let currentMoversMode = 1;

function renderTopMovers(items) {
  const tbody = document.querySelector("#topmovers-table tbody");
  tbody.innerHTML = "";
  if (!items.length) { tbody.innerHTML = '<tr><td colspan="6" class="empty">No data</td></tr>'; return; }
  for (const m of items) {
    const tr = document.createElement("tr");
    const chg = parseFloat(m.change) || 0;
    const chgCls = chg < 0 ? "neg" : chg > 0 ? "pos" : "";
    tr.innerHTML = `
      <td><strong>${m.symbol || ""}</strong></td>
      <td>${m.price ?? ""}</td>
      <td class="${chgCls}">${m.change ?? ""}</td>
      <td class="${chgCls}">${m.change_pct != null ? m.change_pct + "%" : ""}</td>
      <td>${m.volume != null ? Number(m.volume).toLocaleString() : ""}</td>
      <td>${m.trades != null ? Number(m.trades).toLocaleString() : ""}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadTopMovers(mode = 1) {
  try { renderTopMovers((await fetchJson(`/market/top-movers?mode=${mode}`)).items || []); }
  catch (e) { console.warn("top-movers:", e.message); }
}

// ── Exchange State (indices + advance/decline summary) ────────────

function renderExchangeState(data) {
  // Summary cards
  const wrap = document.getElementById("exchange-summary-wrap");
  if (wrap) {
    wrap.innerHTML = "";
    const s = data.summary || {};
    const items = [
      ["Advanced", s.adv, "pos"],
      ["Declined", s.dec, "neg"],
      ["Unchanged", s.unc, ""],
      ["Total Symbols", s.total, ""],
      ["Total Volume", s.total_volume ? Number(s.total_volume).toLocaleString() : null, ""],
    ];
    for (const [label, val, cls] of items) {
      if (val == null) continue;
      const el = document.createElement("div");
      el.className = "summary-item";
      el.innerHTML = `<span class="summary-label">${label}</span>
        <span class="summary-value ${cls}">${val}</span>`;
      wrap.appendChild(el);
    }
  }

  // Index table
  const tbody = document.querySelector("#exchange-indices-table tbody");
  if (!tbody) return;
  tbody.innerHTML = "";
  const indices = data.indices || [];
  if (!indices.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">No data</td></tr>'; return; }
  for (const idx of indices) {
    const tr = document.createElement("tr");
    const chg = parseFloat(idx.net_change) || 0;
    const chgCls = chg < 0 ? "neg" : chg > 0 ? "pos" : "";
    tr.innerHTML = `
      <td><strong>${idx.name || ""}</strong></td>
      <td>${idx.current_index ?? ""}</td>
      <td>${idx.high_index ?? ""}</td>
      <td>${idx.low_index ?? ""}</td>
      <td class="${chgCls}">${idx.net_change ?? ""}</td>
      <td>${idx.volume_traded != null ? Number(idx.volume_traded).toLocaleString() : ""}</td>
      <td>${idx.value_traded != null ? Number(idx.value_traded).toLocaleString() : ""}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadExchangeState() {
  try { renderExchangeState(await fetchJson("/market/exchange-state")); }
  catch (e) { console.warn("exchange-state:", e.message); }
}

// ── Market Segments ───────────────────────────────────────────────

function renderIndices(rows) {
  const tbody = document.querySelector("#indices-table tbody");
  tbody.innerHTML = "";
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty">No data</td></tr>'; return; }
  for (const r of rows) {
    const tr = document.createElement("tr");
    const statusCls = (r.market_status || "").toUpperCase() === "OPEN" ? "pos" : "neg";
    tr.innerHTML = `
      <td><strong>${r.market_code || ""}</strong></td>
      <td class="${statusCls}">${r.market_status || ""}</td>
      <td>${r.total_trades != null ? Number(r.total_trades).toLocaleString() : ""}</td>
      <td>${r.total_volume != null ? Number(r.total_volume).toLocaleString() : ""}</td>
      <td>${r.total_value != null ? Number(r.total_value).toLocaleString() : ""}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadIndices() {
  try { renderIndices((await fetchJson("/market/indices")).indices || []); }
  catch (e) { console.warn("indices:", e.message); }
}

// ── Outstanding Orders ────────────────────────────────────────────

function outstandingOrderType(code) {
  return { "1": "Market", "2": "Limit" }[String(code)] || (code ?? "");
}


function renderOutstanding(orders) {
  const tbody = document.querySelector("#outstanding-table tbody");
  tbody.innerHTML = "";
  for (const o of orders) {
    const tr = document.createElement("tr");
    const sideClass = (o.HOUSE_ORDER_SIDE || "").toLowerCase().includes("sell") ? "side-sell" : "side-buy";
    tr.innerHTML = `
      <td class="nowrap">${o.ORDER_TIME || ""}</td>
      <td><strong>${o.SECURITY_SYMBOL || ""}</strong></td>
      <td>${o.MARKET_TYPE || ""}</td>
      <td class="${sideClass}">${o.HOUSE_ORDER_SIDE || ""}</td>
      <td>${outstandingOrderType(o.ORDER_TYPE)}</td>
      <td>${o.ORDERED_QTY ?? ""}</td>
      <td>${o.ORDER_CUM_QTY ?? ""}</td>
      <td>${o.REMAINING_QTY ?? ""}</td>
      <td>${o.ORDER_PRICE || ""}</td>
      <td class="mono">${o.HOUSE_ORDER_ID || ""}</td>
      <td class="mono">${o.EXCH_ORDER_ID || ""}</td>
      <td><button class="cancel-btn action-btn" type="button">Cancel</button></td>`;

    tr.querySelector(".cancel-btn").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      try {
        btn.disabled = true; btn.textContent = "Cancelling…";
        await fetchJson("/order/cancel", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol: o.SECURITY_SYMBOL,
            exch_order_id: o.EXCH_ORDER_ID,
            house_order_id: o.HOUSE_ORDER_ID,
            order_side: o.HOUSE_ORDER_SIDE || o.ORDER_SIDE,
            market_type: o.MARKET_TYPE || "REG",
          }),
        });
        await loadOutstanding();
      } catch (err) {
        alert(`Cancel failed: ${err.message}`);
      } finally { btn.disabled = false; btn.textContent = "Cancel"; }
    });

    tbody.appendChild(tr);
  }
}

async function loadOutstanding() {
  try { renderOutstanding((await fetchJson("/orders/outstanding")).orders || []); }
  catch (e) { console.warn("outstanding:", e.message); }
}

// ── Trade Logs ────────────────────────────────────────────────────

function humanOrderStatus(code) {
  return ({"0":"Queued","1":"Partial Fill","2":"Filled","4":"Cancelled","8":"Rejected"})[String(code)] || (code ?? "");
}

function renderTradeLogs(orders) {
  const tbody = document.querySelector("#tradelogs-table tbody");
  tbody.innerHTML = "";
  for (const o of orders) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${o.order_time || ""}</td>
      <td>${o.symbol_code || ""}</td>
      <td>${o.house_order_side || ""}</td>
      <td>${humanOrderStatus(o.order_status)}</td>
      <td>${o.ordered_qty || ""}</td>
      <td>${o.order_price || ""}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadTradeLogs() {
  try { renderTradeLogs((await fetchJson("/orders/tradelogs")).orders || []); }
  catch (e) { console.warn("tradelogs:", e.message); }
}

// ── Activity Logs ─────────────────────────────────────────────────

function activityStatusPill(status) {
  const cls = { queued:"act-queued", filled:"act-filled", cancelled:"act-cancelled",
                rejected:"act-rejected", partial_fill:"act-partial" }[status] || "";
  return `<span class="act-pill ${cls}">${(status || "").replace("_", " ")}</span>`;
}

function renderActivityLogs(orders) {
  const tbody = document.querySelector("#activitylogs-table tbody");
  tbody.innerHTML = "";
  for (const o of orders) {
    const tr = document.createElement("tr");
    const sideClass = (o.side || "").toLowerCase().includes("sell") ? "side-sell" : "side-buy";
    tr.innerHTML = `
      <td class="nowrap">${o.order_time || ""}</td>
      <td><strong>${o.symbol || ""}</strong></td>
      <td class="${sideClass}">${o.side || ""}</td>
      <td>${activityStatusPill(o.status)}</td>
      <td>${o.ordered_qty ?? ""}</td>
      <td>${o.filled_qty ?? ""}</td>
      <td>${o.remaining_qty ?? ""}</td>
      <td>${o.order_price != null ? o.order_price : ""}</td>
      <td class="mono">${o.house_order_id || ""}</td>
      <td class="mono">${o.exch_order_id || ""}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadActivityLogs() {
  try { renderActivityLogs((await fetchJson("/orders/activitylogs")).orders || []); }
  catch (e) { console.warn("activitylogs:", e.message); }
}

// ── Consolidated Trades ───────────────────────────────────────────

function renderConsolidated(rows) {
  const tbody = document.querySelector("#consolidated-table tbody");
  tbody.innerHTML = "";
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="11" class="empty">No data</td></tr>'; return; }
  for (const r of rows) {
    const tr = document.createElement("tr");
    const netCls = (r.net_qty || 0) < 0 ? "neg" : (r.net_qty || 0) > 0 ? "pos" : "";
    tr.innerHTML = `
      <td><strong>${r.symbol || ""}</strong></td>
      <td>${r.market || ""}</td>
      <td>${r.buy_qty ?? ""}</td>
      <td>${r.buy_rate ?? ""}</td>
      <td>${fmt2(r.buy_amount)}</td>
      <td>${r.sell_qty ?? ""}</td>
      <td>${r.sell_rate ?? ""}</td>
      <td>${fmt2(r.sell_amount)}</td>
      <td class="${netCls}">${r.net_qty ?? ""}</td>
      <td>${r.net_rate ?? ""}</td>
      <td class="${netCls}">${fmt2(r.net_amount)}</td>`;
    tbody.appendChild(tr);
  }
}

async function loadConsolidated() {
  try { renderConsolidated((await fetchJson("/orders/consolidated")).orders || []); }
  catch (e) { console.warn("consolidated:", e.message); }
}

// ── Place Buy Order ───────────────────────────────────────────────

async function placeBuyOrder(event) {
  event.preventDefault();
  const resultEl = document.getElementById("buy-result");
  const symbol    = document.getElementById("buy-symbol").value.trim();
  const orderType = document.getElementById("buy-order-type").value;
  const price     = document.getElementById("buy-price").value.trim() || "0";
  const volume    = document.getElementById("buy-volume").value.trim();
  try {
    const data = await fetchJson("/order/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, side: "buy", order_type: orderType, price, volume }),
    });
    resultEl.textContent = JSON.stringify(data, null, 2);
    await loadOutstanding();
  } catch (err) { resultEl.textContent = `Order failed: ${err.message}`; }
}

// ── Logout ────────────────────────────────────────────────────────

async function logoutUi() {
  try { await fetchJson("/auth/logout", { method: "POST" }); }
  finally { window.location.href = "/"; }
}

// ── Helpers ───────────────────────────────────────────────────────

function fmt2(val) {
  const n = parseFloat(val);
  return isNaN(n) ? (val ?? "") : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ── Event Wiring ─────────────────────────────────────────────────

document.getElementById("refresh-watchlist").addEventListener("click", loadWatchlist);
document.getElementById("refresh-outstanding").addEventListener("click", loadOutstanding);
document.getElementById("refresh-tradelogs").addEventListener("click", loadTradeLogs);
document.getElementById("refresh-activitylogs").addEventListener("click", loadActivityLogs);
document.getElementById("refresh-positions").addEventListener("click", loadPositions);
document.getElementById("refresh-summary").addEventListener("click", loadAccountSummary);
document.getElementById("refresh-topmovers").addEventListener("click", () => loadTopMovers(currentMoversMode));
document.getElementById("refresh-indices").addEventListener("click", () => { loadExchangeState(); loadIndices(); });
document.getElementById("refresh-consolidated").addEventListener("click", loadConsolidated);
document.getElementById("refresh-cash").addEventListener("click", loadCash);
document.getElementById("refresh-withdrawals").addEventListener("click", loadWithdrawals);
document.getElementById("refresh-statement").addEventListener("click", () => loadStatement());
document.getElementById("refresh-exp-summary").addEventListener("click", loadExposureSummary);
document.getElementById("refresh-exp-cat").addEventListener("click", () => loadExposureCat(currentExpCatMode));
document.getElementById("buy-form").addEventListener("submit", placeBuyOrder);
document.getElementById("logout-btn").addEventListener("click", logoutUi);
document.getElementById("buy-symbol").addEventListener("input", refreshSymbolSuggestionsFromInput);

// Top Movers tabs
document.getElementById("movers-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  document.querySelectorAll("#movers-tabs .tab-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  currentMoversMode = parseInt(btn.dataset.mode, 10) || 0;
  loadTopMovers(currentMoversMode);
});

// Exposure Category tabs
document.getElementById("exp-cat-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  document.querySelectorAll("#exp-cat-tabs .tab-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  currentExpCatMode = btn.dataset.mode;
  loadExposureCat(currentExpCatMode);
});

// Withdrawal form
document.getElementById("withdrawal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const amount = document.getElementById("wd-amount").value.trim();
  if (!amount) return;
  const btn = e.target.querySelector("button");
  btn.textContent = "Sending…";
  btn.disabled = true;
  try {
    const res = await fetchJson("/account/withdrawal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount }),
    });
    btn.textContent = res.success ? "Sent ✓" : "Failed ✗";
    if (res.success) {
      document.getElementById("wd-amount").value = "";
      await loadWithdrawals();
    }
  } catch (err) {
    btn.textContent = "Error ✗";
  } finally {
    setTimeout(() => { btn.textContent = "Request"; btn.disabled = false; }, 2000);
  }
});

// Statement form
document.getElementById("statement-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const from = document.getElementById("stmt-from").value;
  const to = document.getElementById("stmt-to").value;
  if (!from || !to) return;
  // Convert yyyy-mm-dd → dd-MM-yyyy (API format)
  const fmtDate = d => d.split("-").reverse().join("-");
  await loadStatement(fmtDate(from), fmtDate(to));
});

// Scrip Lookup form
document.getElementById("scrip-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const sym = document.getElementById("scrip-symbol").value.trim().toUpperCase();
  const mkt = document.getElementById("scrip-mkt-type").value;
  if (!sym) return;
  await loadScripDetail(sym, mkt);
});

// Periodic data tabs
document.getElementById("periodic-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab-btn");
  if (!btn) return;
  document.querySelectorAll("#periodic-tabs .tab-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  const sym = document.getElementById("scrip-symbol").value.trim().toUpperCase();
  const mkt = document.getElementById("scrip-mkt-type").value;
  if (sym) loadPeriodicData(sym, btn.dataset.dur);
});


// ── Cash & Withdrawals ────────────────────────────────────────────

async function loadCash() {
  try {
    const data = await fetchJson("/account/cash");
    const el = document.getElementById("cash-value");
    if (data.available_cash !== null && data.available_cash !== undefined) {
      el.textContent = fmtNum(data.available_cash);
      el.className = "cash-value " + (data.available_cash >= 0 ? "pos" : "neg");
    } else {
      el.textContent = data.message || "N/A";
      el.className = "cash-value";
    }
  } catch (err) {
    document.getElementById("cash-value").textContent = "Error";
  }
}

async function loadWithdrawals() {
  const tbody = document.querySelector("#withdrawals-table tbody");
  try {
    const rows = await fetchJson("/account/withdrawals");
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">No pending withdrawals</td></tr>'; return; }
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${r.serial_no || "-"}</td>
        <td>${r.requested_date || "-"}</td>
        <td class="num">${fmtNum(r.amount_requested)}</td>
        <td class="num">${fmtNum(r.approved_amount)}</td>
        <td>${r.pay_method || "-"}</td>
        <td><span class="pill ${wdStatusCls(r.status)}">${r.status_label || r.status}</span></td>
        <td class="action-cell">
          ${r.status === "P" ? `<button class="action-btn cancel-btn" data-sno="${r.serial_no}">Cancel</button>` : ""}
        </td>
      </tr>`).join("");
    tbody.querySelectorAll(".cancel-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        btn.disabled = true; btn.textContent = "Cancelling…";
        try {
          const res = await fetchJson("/account/withdrawal/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ serial_no: btn.dataset.sno }),
          });
          if (res.success) await loadWithdrawals();
          else { btn.disabled = false; btn.textContent = "Cancel"; }
        } catch { btn.disabled = false; btn.textContent = "Cancel"; }
      });
    });
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">Error loading withdrawals</td></tr>';
  }
}

function wdStatusCls(status) {
  return { P: "pill-pending", A: "pill-open", C: "pill-cancel", R: "pill-cancel", F: "pill-fill", N: "" }[status] || "";
}


// ── Account Statement ─────────────────────────────────────────────

async function loadStatement(fromDate, toDate) {
  let url = "/account/statement";
  if (fromDate && toDate) url += `?from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}`;
  const tbody = document.querySelector("#statement-table tbody");
  tbody.innerHTML = '<tr><td colspan="5" class="empty">Loading…</td></tr>';
  try {
    const rows = await fetchJson(url);
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="5" class="empty">No data</td></tr>'; return; }
    let runningBalance = null;
    tbody.innerHTML = rows.map(r => {
      runningBalance = r.balance;
      return `<tr>
        <td>${r.date || "-"}</td>
        <td>${r.description || "-"}</td>
        <td class="num neg">${r.debit ? fmtNum(r.debit) : ""}</td>
        <td class="num pos">${r.credit ? fmtNum(r.credit) : ""}</td>
        <td class="num ${r.balance >= 0 ? "pos" : "neg"}">${fmtNum(r.balance)}</td>
      </tr>`;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty">Error: ${err.message}</td></tr>`;
  }
}


// ── Scrip Lookup ──────────────────────────────────────────────────

const LIVE_LABELS = {
  LAST_TRADE_PRICE: "Last Price", NET_CHANGE: "Change", OPEN_PRICE: "Open",
  HIGH_PRICE: "High", LOW_PRICE: "Low", LAST_DAY_CLOSE_PRICE: "Prev Close",
  TOTAL_TRADED_VOLUME: "Volume", TOTAL_TRADES: "Trades",
  BID_PRICE: "Bid", BID_VOLUME: "Bid Vol", ASK_PRICE: "Ask", ASK_VOLUME: "Ask Vol",
  AVERAGE_PRICE: "Avg Price", LAST_TRADE_VOLUME: "Last Vol",
};

const FUND_LABELS = {
  haircut: "Haircut", acceptable_qty: "Acceptable Qty", face_value: "Face Value",
  var_margin: "VAR Margin", sector: "Sector", settlement_type: "Settlement",
  effective: "Effective", lot_size: "Lot Size",
};

async function loadScripDetail(symbol, mktType = "REG") {
  try {
    const data = await fetchJson(`/market/item/${encodeURIComponent(symbol)}?mkt_type=${mktType}`);
    if (data.error) {
      document.getElementById("scrip-live-wrap").style.display = "none";
      return;
    }
    document.getElementById("scrip-live-wrap").style.display = "";

    // Live quote grid
    const liveGrid = document.getElementById("scrip-live-grid");
    const live = data.live || {};
    liveGrid.innerHTML = Object.entries(LIVE_LABELS).map(([k, label]) => {
      const v = live[k];
      if (v === undefined || v === null || v === "") return "";
      const cls = k === "NET_CHANGE" ? (parseFloat(v) >= 0 ? "pos" : "neg") : "";
      return `<div class="summary-item"><span class="summary-label">${label}</span><span class="summary-value ${cls}">${v}</span></div>`;
    }).join("");

    // Fundamentals grid
    const fundGrid = document.getElementById("scrip-fund-grid");
    const fund = data.fundamentals || {};
    fundGrid.innerHTML = Object.entries(FUND_LABELS).map(([k, label]) => {
      const v = fund[k];
      if (v === undefined || v === null || v === "") return "";
      return `<div class="summary-item"><span class="summary-label">${label}</span><span class="summary-value">${v}</span></div>`;
    }).join("");

    // Load default periodic data
    await loadPeriodicData(symbol, "1MO");
  } catch (err) {
    document.getElementById("scrip-live-wrap").style.display = "none";
  }
}

async function loadPeriodicData(symbol, duration = "1MO") {
  const tbody = document.querySelector("#periodic-table tbody");
  try {
    const data = await fetchJson(`/market/item/${encodeURIComponent(symbol)}/periodic?duration=${duration}`);
    const rows = data.rows || [];
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">No data</td></tr>'; return; }
    tbody.innerHTML = rows.map((r, i) => `<tr>
      <td>${i + 1}</td>
      <td class="num">${fmtNum(r.low_price)}</td>
      <td class="num">${fmtNum(r.high_price)}</td>
      <td class="num">${fmtNum(r.avg_price)}</td>
      <td class="num">${fmtNum(r.low_volume)}</td>
      <td class="num">${fmtNum(r.high_volume)}</td>
      <td class="num">${fmtNum(r.avg_volume)}</td>
    </tr>`).join("");
  } catch {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">Error</td></tr>';
  }
}


// ── Exposure Summary ──────────────────────────────────────────────

const EXP_SUMMARY_LABELS = {
  ledger_balance: "Ledger Balance",
  cdc_amount:     "CDC Amount",
  inventory_sold: "Inventory Sold",
  held_amount:    "Held Amount",
  realized_pl:    "Realized P/L",
  mtm_pl:         "MTM P/L",
  expense_amount: "Expenses",
  net_worth:      "Net Worth",
  loan_amount:    "Loan Amount",
  markup_amount:  "Markup Amount",
  cdc_amount_hc:  "CDC Amt HC",
  account_pct:    "Account %",
  account_pct_lev:"Account % (Lev)",
  fpr_amount:     "FPR Amount",
};

async function loadExposureSummary() {
  const wrap = document.getElementById("exp-summary-wrap");
  wrap.innerHTML = "";
  try {
    const data = await fetchJson("/account/exposure-summary-cat");
    if (!data || !Object.keys(data).length) { wrap.innerHTML = '<span class="empty">No data</span>'; return; }
    wrap.innerHTML = Object.entries(EXP_SUMMARY_LABELS).map(([k, label]) => {
      const v = data[k];
      if (v === undefined || v === null) return "";
      const cls = ["realized_pl","mtm_pl"].includes(k) ? (v >= 0 ? "pos" : "neg") : "";
      return `<div class="summary-item"><span class="summary-label">${label}</span><span class="summary-value ${cls}">${fmtNum(v)}</span></div>`;
    }).join("");
  } catch {
    wrap.innerHTML = '<span class="empty">Error loading</span>';
  }
}


// ── Exposure by Category ──────────────────────────────────────────

let currentExpCatMode = "O";

const EXP_CAT_OPEN_HEADERS = [
  "Symbol","Market","MTM Amt","Total Sell","Avg Sell","Pending Buy",
  "Pending Sell","Settled P/L","Unsettled P/L","Net Qty","MFS Qty",
  "MTS Qty","Total Buy","Avg Buy","Trans Amt","MTM Price","BO Avg Rate",
];

const EXP_CAT_COL_HEADERS = [
  "Symbol","Quantity","Avg Buy","Sold Qty","Avg Sell","MTM","MTM Amt",
  "Pending Sell","Settled P/L","Unsettled P/L",
];

async function loadExposureCat(mode = "O") {
  const thead = document.getElementById("exp-cat-header");
  const tbody = document.querySelector("#exp-cat-table tbody");
  tbody.innerHTML = '<tr><td class="empty">Loading…</td></tr>';
  try {
    const data = await fetchJson(`/account/exposure-cat?mode=${mode}`);
    const rows = data.rows || [];
    const headers = mode === "O" ? EXP_CAT_OPEN_HEADERS : EXP_CAT_COL_HEADERS;
    thead.innerHTML = headers.map(h => `<th>${h}</th>`).join("");
    if (!rows.length) { tbody.innerHTML = `<tr><td colspan="${headers.length}" class="empty">No data</td></tr>`; return; }
    tbody.innerHTML = rows.map(r => {
      const cells = Object.values(r);
      return "<tr>" + cells.map(v => `<td>${v ?? "-"}</td>`).join("") + "</tr>";
    }).join("");
  } catch (err) {
    const headers = mode === "O" ? EXP_CAT_OPEN_HEADERS : EXP_CAT_COL_HEADERS;
    tbody.innerHTML = `<tr><td colspan="${headers.length}" class="empty">Error: ${err.message}</td></tr>`;
  }
}


// ── Helpers ───────────────────────────────────────────────────────

function fmtNum(v) {
  if (v === null || v === undefined || v === "") return "-";
  const n = parseFloat(v);
  if (isNaN(n)) return String(v);
  return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}


// ── Init ──────────────────────────────────────────────────────────

async function initDashboard() {
  const ok = await loadSessionInfo();
  if (!ok) return;
  await loadSymbols();
  await refreshAll();
}

initDashboard();

// ── Daily Trader UI ──────────────────────────────────────────────

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

function fmtNum(v, dp = 2) {
  if (v === null || v === undefined || v === "") return "-";
  const n = parseFloat(v);
  if (isNaN(n)) return String(v);
  return n.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

function fmtPct(v) {
  if (v === null || v === undefined) return "-";
  const n = parseFloat(v);
  if (isNaN(n)) return "-";
  return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
}

function fmtTime(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour12: false });
}

function colourClass(v) {
  if (v === null || v === undefined) return "";
  const n = parseFloat(v);
  if (isNaN(n) || n === 0) return "";
  return n > 0 ? "pos" : "neg";
}

// ─────────────────────────────────────────────────────────────────
// CONFIG SCHEMA
// Every entry has:
//   key     — matches the API field name exactly
//   label   — short human name shown in bold
//   hint    — plain-English explanation shown in small text below
//   type    — "bool" | "num" | "int" | "text" | "select"
//   options — (only for "select") array of {value, label}
// ─────────────────────────────────────────────────────────────────

const CONFIG_SCHEMA = [

  // ── BOT SETTINGS ─────────────────────────────────────────────
  {
    section: "🤖 Bot Settings",
    desc: "Turn the bot on or off and choose which stocks to scan."
  },
  {
    key: "enabled",
    label: "Bot Enabled",
    type: "bool",
    hint: "Master on/off switch. Set to No to pause the bot without stopping the server."
  },
  {
    key: "tick_driven",
    label: "React to Live Price Ticks",
    type: "bool",
    hint: "Yes (recommended) — the bot evaluates entries instantly whenever a subscribed stock's price changes (~200 ms reaction time). No — only checks every 30 seconds via a poll scan."
  },
  {
    key: "feed_type",
    label: "Which Market Feed to Scan",
    type: "select",
    options: [
      { value: "I", label: "I — Index  (KSE-100, KSE-30, etc.)" },
      { value: "S", label: "S — Sector  (enter sector code below)" },
      { value: "F", label: "F — Futures" },
    ],
    hint: "The bot scans this feed every 30 seconds looking for buying opportunities. 'Index' is the best starting point — it covers the most traded PSX stocks."
  },
  {
    key: "feed_code",
    label: "Feed Code",
    type: "text",
    hint: "For Index feed → 0 = KSE-100  |  1 = KSE-30  |  2 = KMI-30.   For Sector feed → enter the sector code (e.g. 'TECH', 'BANK')."
  },

  // ── ENTRY SIGNAL ─────────────────────────────────────────────
  {
    section: "📈 Entry Signal",
    desc: "The rules the bot uses to decide whether a stock is worth buying. A stock must pass ALL of these filters before the bot buys."
  },
  {
    key: "min_change_pct",
    label: "Minimum Price Change % Today",
    type: "num",
    hint: "The stock must already be up at least this % on the day. 0.5 means it needs to be up at least +0.5%. This confirms the stock has upward momentum before entry."
  },
  {
    key: "max_change_pct",
    label: "Maximum Price Change % Today",
    type: "num",
    hint: "Skip stocks that have already moved too much. PSX has a 7.5% upper circuit limit — if a stock is already up 5%, there is very little room left. 5.0 is a safe ceiling."
  },
  {
    key: "use_vwap_filter",
    label: "Only Buy Near Fair Value (VWAP Filter)",
    type: "bool",
    hint: "VWAP = Volume Weighted Average Price — the average price everyone paid for the stock today, weighted by volume. It is the market's fair value for the day. When Yes, the bot only buys if the stock's price is close to or below VWAP, not after it has already shot far above it."
  },
  {
    key: "vwap_max_above_pct",
    label: "How Far Above VWAP is Still Acceptable (%)",
    type: "num",
    hint: "Works with the VWAP filter above. 0.5 means: only buy if the stock is within 0.5% above its VWAP. A stock trading 2% above VWAP has already moved — leave it alone."
  },
  {
    key: "min_volume",
    label: "Minimum Volume (total shares traded today)",
    type: "int",
    hint: "Ignore stocks with low trading activity — they are hard to exit quickly. 200,000 means the stock must have had at least 200k shares traded today before the bot will consider it."
  },
  {
    key: "min_price",
    label: "Minimum Stock Price (PKR)",
    type: "num",
    hint: "Skip very cheap stocks. Cheap stocks (under PKR 10) often have wide spreads and erratic moves that are hard to trade."
  },
  {
    key: "max_price",
    label: "Maximum Stock Price (PKR)",
    type: "num",
    hint: "Skip very expensive stocks where even 1 share costs a lot. Adjust this based on your available capital. PKR 2,000 is a reasonable ceiling for most accounts."
  },
  {
    key: "max_spread_pct",
    label: "Maximum Bid-Ask Spread (%)",
    type: "num",
    hint: "The spread is the gap between the buy price and sell price. You 'pay' the spread on entry AND exit. 0.5% or less is ideal. A 1% spread already costs you 1% before the stock even moves."
  },

  // ── PROFIT & STOP LOSS ────────────────────────────────────────
  {
    section: "🎯 Profit Target & Stop Loss",
    desc: "When the bot takes profit and when it cuts losses. The target should be at least 2× the stop loss for a healthy risk/reward ratio."
  },
  {
    key: "target_pct",
    label: "Take Profit at (% gain from entry)",
    type: "num",
    hint: "The bot sells when the stock rises this % above your entry price. Example: 2.0 means sell when you are up +2%. With a 1% stop loss this gives a 2:1 reward-to-risk ratio."
  },
  {
    key: "stop_pct",
    label: "Stop Loss at (% drop from entry)",
    type: "num",
    hint: "The bot sells immediately when the stock falls this % below your entry price to limit the loss. Example: 1.0 means sell when you are down -1%. Keep this tight — small losses are manageable, large ones are not."
  },
  {
    key: "trailing_stop_pct",
    label: "Trailing Stop Loss (%)",
    type: "num",
    hint: "When the stock rises, the stop loss automatically rises with it, locking in profit. Example: 0.8 means the stop sits 0.8% below the highest price the stock has reached. Set to 0 to use a fixed stop loss instead."
  },
  {
    key: "use_slo",
    label: "Place Stop-Loss Order at Broker (SLO)",
    type: "bool",
    hint: "When Yes, the bot sends a stop-loss order directly to the broker exchange. This triggers even if your server goes offline. When No, the bot watches the stop internally (requires the server to be running)."
  },

  // ── POSITION SIZING ───────────────────────────────────────────
  {
    section: "💰 How Much to Buy",
    desc: "The bot uses the Risk % formula as the primary sizer — the PKR limits below are safety guardrails only."
  },
  {
    key: "risk_per_trade_pct",
    label: "Risk Per Trade (% of your available cash)",
    type: "num",
    hint: "This is the main sizing formula. The bot calculates how many shares to buy so that if the stop loss triggers, you lose exactly this % of your cash. Example: 2.0 and a 1% stop → the bot buys shares worth (2% ÷ 1%) = 2× your risked amount."
  },
  {
    key: "max_concurrent",
    label: "Maximum Positions Open at the Same Time",
    type: "int",
    hint: "The bot will not open more trades than this at once. 3 is a good balance — enough diversification without spreading your attention too thin."
  },

  // ── SAFETY LIMITS ─────────────────────────────────────────────
  {
    section: "🛡️ Safety Limits (PKR Hard Caps)",
    desc: "These are hard guardrails. If the risk formula produces a position larger than any of these limits, the limit wins."
  },
  {
    key: "max_orders_per_day",
    label: "Maximum Trades to Open Today",
    type: "int",
    hint: "The bot stops opening new positions after this many entries in a single day. Prevents overtrading. 6 is a conservative starting point."
  },
  {
    key: "max_buy_amount_per_trade",
    label: "Maximum PKR Spent on Any Single Buy",
    type: "num",
    hint: "The bot will never spend more than this PKR on a single buy order, regardless of what the formula says. Example: 100,000 = never spend more than PKR 1 lac in one trade."
  },
  {
    key: "daily_max_buy_amount",
    label: "Maximum Total PKR Spent Buying Today",
    type: "num",
    hint: "The bot stops buying for the rest of the day once total purchases reach this amount. Example: 400,000 = stop after PKR 4 lac in buys total."
  },
  {
    key: "max_sell_amount_per_trade",
    label: "Maximum PKR on Any Single Sell",
    type: "num",
    hint: "Hard ceiling on any single sell order. Usually set the same as Max Buy per Trade."
  },
  {
    key: "daily_max_sell_amount",
    label: "Maximum Total PKR Sold Today",
    type: "num",
    hint: "Total sell cap for the day. Usually set the same as Maximum Total Buying."
  },
  {
    key: "daily_loss_limit_pct",
    label: "Auto-Halt If Daily Loss Reaches (% of start cash)",
    type: "num",
    hint: "If the bot's combined P/L for the day drops below this threshold, it HALTS and refuses to open any more trades. Example: 3.0 = halt if the bot has lost 3% of the cash it started the day with."
  },

  // ── TRADING HOURS ─────────────────────────────────────────────
  {
    section: "⏰ Trading Hours  (Pakistan Standard Time)",
    desc: "PSX trading hours are 09:30 – 15:30. The bot needs time windows both for entering and for exiting before close."
  },
  {
    key: "entry_start_hhmm",
    label: "Earliest Time to Open a Trade",
    type: "text",
    hint: "The first few minutes after open are volatile and spreads are wide. 09:35 gives the market 5 minutes to settle before the bot starts buying."
  },
  {
    key: "entry_stop_hhmm",
    label: "Latest Time to Open a New Trade",
    type: "text",
    hint: "After this time the bot will not enter any new positions. 14:00 leaves 75 minutes for open positions to hit their target before the forced exit at 15:15."
  },
  {
    key: "force_exit_hhmm",
    label: "Force-Close Everything At This Time",
    type: "text",
    hint: "At this exact time the bot sells ALL open positions regardless of P/L. PSX closes at 15:30 — 15:15 gives a 15-minute buffer to exit cleanly."
  },
  {
    key: "cooldown_after_loss_s",
    label: "Wait After a Losing Trade (seconds)",
    type: "int",
    hint: "After a trade closes at a loss, the bot pauses this many seconds before opening the next position. This prevents emotional revenge-trading. 300 seconds = 5 minutes."
  },

  // ── ADVANCED ─────────────────────────────────────────────────
  {
    section: "⚙️ Advanced Settings",
    desc: "These control execution details. The defaults work well — only change if you understand the impact."
  },
  {
    key: "entry_offset_pct",
    label: "Entry Price Offset (%)",
    type: "num",
    hint: "The bot places the buy order at the ask price + this %. The small premium ensures the order fills instead of sitting in the queue. 0.05% on a PKR 50 stock = PKR 0.025 extra per share."
  },
  {
    key: "exit_offset_pct",
    label: "Exit Price Offset (%)",
    type: "num",
    hint: "The bot places the sell order at the bid price - this %. The small discount helps the sell fill quickly."
  },
  {
    key: "scan_interval_sec",
    label: "Poll Scan Frequency (seconds)",
    type: "int",
    hint: "How often the bot pulls the full market feed to discover new candidate stocks. With live tick reaction enabled, this mainly matters for stocks not yet subscribed to the feed. 10 seconds is fine."
  },
  {
    key: "monitor_interval_sec",
    label: "Position Check Frequency (seconds)",
    type: "int",
    hint: "How often the bot checks open positions for target/stop hits when no tick arrives. Target/stop checks also happen on every live price tick, so this is just the fallback interval."
  },
];

// ─────────────────────────────────────────────────────────────────
// Build the config form
// ─────────────────────────────────────────────────────────────────

function buildConfigForm(cfg) {
  const form = document.getElementById("dt-config-form");
  form.innerHTML = "";

  for (const item of CONFIG_SCHEMA) {
    // Section header
    if (item.section) {
      const wrap = document.createElement("div");
      wrap.className = "config-section-header";

      const title = document.createElement("div");
      title.className = "config-section-title";
      title.textContent = item.section;
      wrap.appendChild(title);

      if (item.desc) {
        const desc = document.createElement("div");
        desc.className = "config-section-desc";
        desc.textContent = item.desc;
        wrap.appendChild(desc);
      }
      form.appendChild(wrap);
      continue;
    }

    // Field row
    const row = document.createElement("div");
    row.className = "config-field";

    const labelEl = document.createElement("label");
    labelEl.className = "config-label";
    labelEl.textContent = item.label;

    let input;
    if (item.type === "bool") {
      input = document.createElement("select");
      input.className = "config-input";
      input.innerHTML = `<option value="true">Yes</option><option value="false">No</option>`;
      input.value = (cfg[item.key] === true || cfg[item.key] === "true") ? "true" : "false";
    } else if (item.type === "select") {
      input = document.createElement("select");
      input.className = "config-input";
      for (const opt of item.options) {
        const o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        input.appendChild(o);
      }
      input.value = cfg[item.key] ?? "";
    } else {
      input = document.createElement("input");
      input.className = "config-input";
      input.type = (item.type === "num" || item.type === "int") ? "number" : "text";
      if (item.type === "num") input.step = "0.01";
      if (item.type === "int") input.step = "1";
      input.value = cfg[item.key] ?? "";
    }
    input.dataset.key  = item.key;
    input.dataset.type = item.type;

    const hint = document.createElement("div");
    hint.className = "config-hint";
    hint.textContent = item.hint || "";

    row.appendChild(labelEl);
    row.appendChild(input);
    row.appendChild(hint);
    form.appendChild(row);
  }
}

function readConfigForm() {
  const form = document.getElementById("dt-config-form");
  const out = {};
  form.querySelectorAll("input[data-key], select[data-key]").forEach(el => {
    const key  = el.dataset.key;
    const type = el.dataset.type;
    const raw  = el.value;
    if (raw === "") return;
    if (type === "bool")     out[key] = raw === "true";
    else if (type === "int") out[key] = parseInt(raw, 10);
    else if (type === "num") out[key] = parseFloat(raw);
    else                     out[key] = raw;
  });
  return out;
}

// ── Status & loaders ───────────────────────────────────────────────

async function loadStatus() {
  try {
    const s = await fetchJson("/daily-trader/status");
    const pill = document.getElementById("dt-state-pill");
    pill.textContent = s.state;
    pill.className = "dt-state-pill " + s.state.toLowerCase();
    document.getElementById("dt-halt-reason").textContent = s.halt_reason ? `(${s.halt_reason})` : "";

    const mPill = document.getElementById("dt-market-pill");
    const ms = (s.market_status || "UNKNOWN").toUpperCase();
    mPill.textContent = ms;
    mPill.className = "dt-state-pill " + (ms === "OPEN" ? "open" : ms === "UNKNOWN" ? "unknown" : "closed");

    document.getElementById("dt-now").textContent        = s.now;
    document.getElementById("dt-day").textContent        = s.day_key;
    document.getElementById("dt-start-cash").textContent = fmtNum(s.start_of_day_cash);
    document.getElementById("dt-cur-cash").textContent   = fmtNum(s.current_cash);

    const plEl = document.getElementById("dt-day-pl");
    plEl.textContent = fmtNum(s.day_pl);
    plEl.className   = "dt-metric-value " + colourClass(s.day_pl);

    const realizedEl = document.getElementById("dt-day-pl-realized");
    realizedEl.textContent = fmtNum(s.day_pl_realized);
    realizedEl.className   = "dt-metric-value " + colourClass(s.day_pl_realized);

    const unrealizedEl = document.getElementById("dt-day-pl-unrealized");
    unrealizedEl.textContent = fmtNum(s.day_pl_unrealized);
    unrealizedEl.className   = "dt-metric-value " + colourClass(s.day_pl_unrealized);

    const plPctEl = document.getElementById("dt-day-pl-pct");
    plPctEl.textContent = fmtPct(s.day_pl_pct);
    plPctEl.className   = "dt-metric-value " + colourClass(s.day_pl_pct);

    document.getElementById("dt-trades-today").textContent    = s.trade_count_today;
    document.getElementById("dt-daily-buy-used").textContent  = fmtNum(s.daily_buy_value);
    document.getElementById("dt-daily-sell-used").textContent = fmtNum(s.daily_sell_value);
    document.getElementById("dt-open-count").textContent      = s.open_count;

    renderPositions(s.open_positions || []);
    renderOrphanBanner(s);
  } catch (err) {
    console.error("status load failed", err);
  }
}

function renderOrphanBanner(s) {
  const banner = document.getElementById("dt-orphan-banner");
  const showOrphan = s.state === "STOPPED" && (s.open_count || 0) > 0;
  banner.style.display = showOrphan ? "flex" : "none";
  if (showOrphan) {
    document.getElementById("dt-orphan-title").textContent =
      `${s.open_count} open position${s.open_count === 1 ? "" : "s"} from a previous session.`;
    document.getElementById("dt-orphan-msg").textContent =
      "Bot is STOPPED — target/stop are NOT being monitored. Resume to protect them, or close all now.";
  }
}

function renderPositions(rows) {
  const tbody = document.querySelector("#dt-positions-table tbody");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty">No open positions</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(p => `
    <tr>
      <td><code>${p.pos_id}</code></td>
      <td><strong>${p.symbol}</strong></td>
      <td><span class="dt-pill ${statusPillCls(p.status)}">${p.status.replace(/_/g," ")}</span></td>
      <td class="num">${p.qty}</td>
      <td class="num">${fmtNum(p.entry_price)}</td>
      <td class="num">${fmtNum(p.last_price)}</td>
      <td class="num">${fmtNum(p.target_price)}</td>
      <td class="num">${fmtNum(p.stop_price)}</td>
      <td class="num ${colourClass(p.mtm_pl)}">${fmtNum(p.mtm_pl)}</td>
      <td class="num ${colourClass(p.mtm_pl_pct)}">${fmtPct(p.mtm_pl_pct)}</td>
      <td>
        ${p.status === "holding" || p.status === "pending_entry"
          ? `<button class="action-btn cancel-btn dt-close-btn" data-pid="${p.pos_id}">Close</button>`
          : ""}
      </td>
    </tr>
  `).join("");
  tbody.querySelectorAll(".dt-close-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      btn.disabled = true; btn.textContent = "Closing…";
      try {
        await fetchJson("/daily-trader/close", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_id: btn.dataset.pid, reason: "manual" }),
        });
        await loadStatus();
      } catch (e) {
        btn.disabled = false; btn.textContent = "Close";
      }
    });
  });
}

function statusPillCls(s) {
  return {
    "pending_entry": "dt-pill-pending",
    "holding":       "dt-pill-holding",
    "pending_exit":  "dt-pill-exit",
    "closed":        "dt-pill-closed",
  }[s] || "";
}

async function loadConfig() {
  try {
    const cfg = await fetchJson("/daily-trader/config");
    buildConfigForm(cfg);
  } catch (err) {
    console.error("config load failed", err);
  }
}

async function loadSignals() {
  const tbody = document.querySelector("#dt-signals-table tbody");
  try {
    const data = await fetchJson("/daily-trader/signals?limit=50");
    const rows = (data.items || []).slice().reverse();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty">No signals yet — starts when bot is running and market is open</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(s => `
      <tr>
        <td>${fmtTime(s.ts)}</td>
        <td><strong>${s.symbol}</strong></td>
        <td class="num">${fmtNum(s.last_price)}</td>
        <td class="num ${colourClass(s.change_pct)}">${fmtPct(s.change_pct)}</td>
        <td class="num">${fmtNum(s.volume, 0)}</td>
        <td class="num">${fmtNum(s.vwap || 0)}</td>
        <td class="num">${fmtNum(s.spread_pct)}</td>
        <td class="num">${fmtNum(s.score, 3)}</td>
        <td>${s.rejected
          ? `<span class="dt-pill dt-pill-closed" title="${s.rejected}">${signalRejectLabel(s.rejected)}</span>`
          : `<span class="dt-pill dt-pill-holding">✓ accepted</span>`}</td>
      </tr>
    `).join("");
  } catch (err) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">Error loading signals</td></tr>';
  }
}

// Human-readable reason labels for signal rejections
function signalRejectLabel(reason) {
  const labels = {
    "change<min":    "⬇ change too small",
    "change>max":    "⬆ already moved too much",
    "volume<min":    "📉 low volume",
    "price<min":     "💲 price too low",
    "price>max":     "💲 price too high",
    "spread>max":    "↔ spread too wide",
    "no_quote":      "❓ no live quote",
    "already_held":  "📌 already holding",
    "broker_busy":   "⏳ order pending",
    "above_vwap":    "📈 too far above VWAP",
  };
  // Map compound reasons like "change<min,volume<min"
  return reason.split(",").map(r => labels[r.trim()] || r.trim()).join("  ·  ");
}

async function loadReport() {
  try {
    const r = await fetchJson("/daily-trader/report");
    const grid = document.getElementById("dt-report-summary");
    grid.innerHTML = `
      <div class="dt-metric"><span class="dt-metric-label">Completed Trades</span><span class="dt-metric-value">${r.trades_count}</span></div>
      <div class="dt-metric"><span class="dt-metric-label">Wins</span><span class="dt-metric-value pos">${r.wins}</span></div>
      <div class="dt-metric"><span class="dt-metric-label">Losses</span><span class="dt-metric-value neg">${r.losses}</span></div>
      <div class="dt-metric"><span class="dt-metric-label">Win Rate</span><span class="dt-metric-value">${fmtPct(r.win_rate_pct)}</span></div>
      <div class="dt-metric"><span class="dt-metric-label">Gross P/L (PKR)</span><span class="dt-metric-value ${colourClass(r.gross_pl)}">${fmtNum(r.gross_pl)}</span></div>
      <div class="dt-metric"><span class="dt-metric-label">Avg Winning Trade</span><span class="dt-metric-value pos">${fmtNum(r.avg_winner)}</span></div>
      <div class="dt-metric"><span class="dt-metric-label">Avg Losing Trade</span><span class="dt-metric-value neg">${fmtNum(r.avg_loser)}</span></div>
    `;
    const tbody = document.querySelector("#dt-report-table tbody");
    if (!r.trades.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">No completed trades today</td></tr>';
      return;
    }
    tbody.innerHTML = r.trades.slice().reverse().map(t => `
      <tr>
        <td><code>${t.pos_id}</code></td>
        <td><strong>${t.symbol}</strong></td>
        <td class="num">${t.qty}</td>
        <td class="num">${fmtNum(t.entry_price)}</td>
        <td class="num">${fmtNum(t.exit_price)}</td>
        <td class="num ${colourClass(t.realized_pl)}">${fmtNum(t.realized_pl)}</td>
        <td>${exitReasonLabel(t.exit_reason)}</td>
      </tr>
    `).join("");
  } catch (err) {
    console.error("report load failed", err);
  }
}

function exitReasonLabel(r) {
  const m = {
    target:       "✅ Hit Target",
    stop:         "🛑 Stop Loss",
    time_cutoff:  "⏰ Time Cutoff",
    manual:       "👤 Manually Closed",
    broker_drift: "⚠️ Broker Mismatch",
  };
  return m[r] || r || "-";
}

async function loadEvents() {
  const wrap = document.getElementById("dt-events-list");
  try {
    const data = await fetchJson("/daily-trader/events?limit=80");
    const rows = (data.items || []).slice().reverse();
    if (!rows.length) {
      wrap.innerHTML = '<div class="dt-event-row"><span class="dt-event-msg">No events yet</span></div>';
      return;
    }
    wrap.innerHTML = rows.map(e => `
      <div class="dt-event-row ${e.level || "info"}">
        <span class="dt-event-ts">${fmtTime(e.ts)}</span>
        <span class="dt-event-level">${(e.level || "info").toUpperCase()}</span>
        <span class="dt-event-msg">
          ${escapeHtml(e.msg)}
          ${Object.keys(e.detail || {}).length
            ? `<span class="dt-event-detail"> — ${escapeHtml(JSON.stringify(e.detail))}</span>`
            : ""}
        </span>
      </div>
    `).join("");
  } catch (err) {
    wrap.innerHTML = '<div class="dt-event-row error"><span class="dt-event-msg">Error loading events</span></div>';
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
  }[c]));
}

// ── Refresh loop ───────────────────────────────────────────────────

async function refreshAll() {
  await Promise.all([loadStatus(), loadSignals(), loadReport(), loadEvents()]);
}

// ── Action handlers ────────────────────────────────────────────────

async function postCmd(url, body = null) {
  const opts = { method: "POST" };
  if (body) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  return fetchJson(url, opts);
}

document.getElementById("dt-start-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try { await postCmd("/daily-trader/start"); await refreshAll(); }
  catch (err) { alert("Start failed: " + err.message); }
  finally { e.target.disabled = false; }
});

document.getElementById("dt-stop-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  try { await postCmd("/daily-trader/stop"); await refreshAll(); }
  catch (err) { alert("Stop failed: " + err.message); }
  finally { e.target.disabled = false; }
});

document.getElementById("dt-halt-btn").addEventListener("click", async (e) => {
  if (!confirm("Halt the bot? It will stop entering new trades. Open positions will still be monitored.")) return;
  e.target.disabled = true;
  try { await postCmd("/daily-trader/halt", { reason: "manual" }); await refreshAll(); }
  catch (err) { alert("Halt failed: " + err.message); }
  finally { e.target.disabled = false; }
});

document.getElementById("dt-reset-btn").addEventListener("click", async (e) => {
  if (!confirm("Reset today's counters? (trade count, buy/sell totals, daily P/L). The bot state does not change.")) return;
  e.target.disabled = true;
  try { await postCmd("/daily-trader/reset-day"); await refreshAll(); }
  catch (err) { alert("Reset failed: " + err.message); }
  finally { e.target.disabled = false; }
});

document.getElementById("dt-sync-btn").addEventListener("click", async (e) => {
  e.target.disabled = true;
  const orig = e.target.textContent;
  e.target.textContent = "Syncing…";
  try {
    await postCmd("/daily-trader/sync");
    e.target.textContent = "Synced ✓";
    await refreshAll();
  } catch (err) {
    alert("Sync failed: " + err.message);
    e.target.textContent = orig;
  } finally {
    setTimeout(() => { e.target.disabled = false; e.target.textContent = orig; }, 1500);
  }
});

document.getElementById("dt-orphan-resume").addEventListener("click", async (e) => {
  if (!confirm("Resume management of open positions? The bot will monitor their target/stop.")) return;
  e.target.disabled = true;
  try { await postCmd("/daily-trader/start"); await refreshAll(); }
  catch (err) { alert("Resume failed: " + err.message); }
  finally { e.target.disabled = false; }
});

document.getElementById("dt-orphan-close-all").addEventListener("click", async (e) => {
  if (!confirm("Close ALL open bot positions now? Each will be sold at market price.")) return;
  e.target.disabled = true;
  e.target.textContent = "Closing…";
  try {
    const data = await fetchJson("/daily-trader/positions");
    const rows = (data.items || []).filter(p =>
      p.status === "holding" || p.status === "pending_entry"
    );
    for (const p of rows) {
      try {
        await fetchJson("/daily-trader/close", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pos_id: p.pos_id, reason: "manual_close_all" }),
        });
      } catch (err) {
        console.warn("close failed for", p.pos_id, err);
      }
    }
    await refreshAll();
  } catch (err) {
    alert("Close All failed: " + err.message);
  } finally {
    e.target.disabled = false;
    e.target.textContent = "Close All";
  }
});

document.getElementById("dt-save-config").addEventListener("click", async (e) => {
  const patch = readConfigForm();
  e.target.disabled = true;
  e.target.textContent = "Saving…";
  try {
    const res = await fetchJson("/daily-trader/config", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (res.ok === false) throw new Error(res.msg);
    e.target.textContent = "Saved ✓";
    await loadConfig();
  } catch (err) {
    alert("Save failed: " + err.message);
    e.target.textContent = "Save Config";
  } finally {
    setTimeout(() => { e.target.disabled = false; e.target.textContent = "Save Config"; }, 1500);
  }
});

document.getElementById("dt-refresh-positions").addEventListener("click", loadStatus);
document.getElementById("dt-refresh-signals").addEventListener("click", loadSignals);
document.getElementById("dt-refresh-report").addEventListener("click", loadReport);
document.getElementById("dt-refresh-events").addEventListener("click", loadEvents);

// ── Init ───────────────────────────────────────────────────────────

(async () => {
  await loadConfig();
  await refreshAll();
  setInterval(loadStatus,  5000);   // live status every 5 s
  setInterval(loadSignals, 15000);  // signals every 15 s
  setInterval(loadEvents,  10000);  // events every 10 s
})();

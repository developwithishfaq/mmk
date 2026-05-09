let __halted = false;

async function fetchJson(url, options = {}) {
  if (__halted) {
    throw new Error("halted");
  }
  const opts = { credentials: "include", ...options };
  const response = await fetch(url, opts);
  if (response.status === 401) {
    __halted = true;
    window.location.replace("/ui/login.html");
    throw new Error("not-authenticated");
  }
  if (response.status === 503) {
    __halted = true;
    throw new Error("not-connected");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || JSON.stringify(data) || "Request failed");
  }
  return data;
}

async function requireSession() {
  try {
    await fetchJson("/session/info");
    return true;
  } catch {
    return false;
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

function fmtNum(v, digits = 2) {
  if (v == null || v === "" || Number.isNaN(Number(v))) return "-";
  return Number(v).toFixed(digits);
}

function fmtTs(ts) {
  if (!ts) return "-";
  try {
    return new Date(ts * 1000).toLocaleTimeString();
  } catch {
    return "-";
  }
}

function strategyCard(s) {
  const rt = s.runtime || {};
  const logHtml = (s.event_log || [])
    .slice(-10)
    .reverse()
    .map((e) => `<div class="event-line">${esc(e.ts)} — ${esc(e.message)}</div>`)
    .join("");
  const histHtml = (s.trade_history || [])
    .slice(-8)
    .reverse()
    .map((h) => {
      const px = h.price == null ? "-" : fmtNum(h.price, 3);
      const note = h.note ? ` · ${esc(h.note)}` : "";
      return `<div class="event-line">${esc(h.ts)} · <strong>${esc((h.side || "").toUpperCase())}</strong> · Qty ${esc(String(h.qty || 0))} · Px ${px} · <em>${esc(h.status || "-")}</em>${note}</div>`;
    })
    .join("");
  return `
    <article class="strategy-card card" data-id="${esc(s.id)}">
      <div class="strategy-card-head">
        <strong>${esc(s.symbol)}</strong>
        <div class="pills">
          <span class="pill ${s.enabled ? "pill-on" : "pill-off"}">${s.enabled ? "ON" : "OFF"}</span>
          ${s.dry_run ? '<span class="pill pill-dry">DRY-RUN</span>' : ""}
        </div>
      </div>
      <div class="strategy-meta muted">
        State: <strong>${esc(rt.state)}</strong> ·
        Qty: ${esc(String(rt.total_qty || 0))} ·
        Avg: ${fmtNum(rt.avg_entry_price)} ·
        Stop: ${rt.stop_loss_price != null ? fmtNum(rt.stop_loss_price) : "-"}<br/>
        Order: ${esc(s.order_type)} · Risk: ${esc(String(s.risk_per_trade))} · Bal: ${esc(String(s.account_balance))}<br/>
        Last signal: <em>${esc(s.last_signal || "-")}</em> ·
        Last action: <em>${esc(s.last_action || "-")}</em> @ ${fmtTs(s.last_action_ts)}<br/>
        Last tick: ${fmtTs(s.last_tick_ts)} ·
        Exit cooldown: ${esc(String(s.exit_cooldown_sec))}s
      </div>
      <div class="strategy-actions">
        <button type="button" data-toggle="${esc(s.id)}">${s.enabled ? "Disable" : "Enable"}</button>
        <button type="button" data-dry="${esc(s.id)}">${s.dry_run ? "Disable Dry-run" : "Enable Dry-run"}</button>
        <button type="button" data-reset="${esc(s.id)}">Reset state</button>
        <button type="button" class="delete-strat" data-delete="${esc(s.id)}">Delete</button>
      </div>
      <details>
        <summary class="muted">Test tick (force evaluate)</summary>
        <form class="test-tick-form" data-test-form="${esc(s.id)}">
          <label>last <input type="number" step="any" name="last" required /></label>
          <label>low <input type="number" step="any" name="low" required /></label>
          <label>high <input type="number" step="any" name="high" required /></label>
          <label>prev_close <input type="number" step="any" name="prev_close" required /></label>
          <label class="checkbox-row" style="flex-direction:row;align-items:center;gap:4px;">
            <input type="checkbox" name="place" /> place real order
          </label>
          <button type="submit">Run test</button>
        </form>
      </details>
      <details>
        <summary class="muted">Buy/Sell history</summary>
        <div class="event-log">${histHtml || '<span class="muted">No buy/sell history yet.</span>'}</div>
      </details>
      <div class="event-log">${logHtml || '<span class="muted">No events yet.</span>'}</div>
    </article>`;
}

function renderStrategies(payload) {
  const root = document.getElementById("at-strategies-list");
  const items = payload.strategies || [];
  if (!items.length) {
    root.innerHTML = '<p class="muted">No strategies yet. Add one above.</p>';
    return;
  }
  root.innerHTML = items.map(strategyCard).join("");
  wireCardActions(items);
}

function wireCardActions(items) {
  const root = document.getElementById("at-strategies-list");
  root.querySelectorAll("[data-toggle]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-toggle");
      const row = items.find((x) => x.id === id);
      if (!row) return;
      try {
        await fetchJson(`/auto-trade/strategies/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !row.enabled }),
        });
        await loadStrategies();
      } catch (e) {
        alert(e.message);
      }
    });
  });

  root.querySelectorAll("[data-dry]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-dry");
      const row = items.find((x) => x.id === id);
      if (!row) return;
      try {
        await fetchJson(`/auto-trade/strategies/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dry_run: !row.dry_run }),
        });
        await loadStrategies();
      } catch (e) {
        alert(e.message);
      }
    });
  });

  root.querySelectorAll("[data-reset]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-reset");
      if (!confirm("Reset position state to FLAT? This won't cancel any live broker orders.")) return;
      try {
        await fetchJson(`/auto-trade/strategies/${id}/reset`, { method: "POST" });
        await loadStrategies();
      } catch (e) {
        alert(e.message);
      }
    });
  });

  root.querySelectorAll("[data-delete]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this strategy?")) return;
      const id = btn.getAttribute("data-delete");
      try {
        await fetchJson(`/auto-trade/strategies/${id}`, { method: "DELETE" });
        await loadStrategies();
      } catch (e) {
        alert(e.message);
      }
    });
  });

  root.querySelectorAll("[data-test-form]").forEach((form) => {
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const id = form.getAttribute("data-test-form");
      const fd = new FormData(form);
      const body = {
        last: parseFloat(fd.get("last")),
        low: parseFloat(fd.get("low")),
        high: parseFloat(fd.get("high")),
        prev_close: parseFloat(fd.get("prev_close")),
        place: fd.get("place") === "on",
        market_open: true,
      };
      try {
        const res = await fetchJson(`/auto-trade/strategies/${id}/test-tick`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const lines = [];
        lines.push(`Intents: ${res.intents.length}`);
        for (const it of res.intents) {
          lines.push(`  → ${JSON.stringify(it.action)}${it.dry_run ? " (dry)" : ""}`);
        }
        for (const p of res.placed || []) {
          lines.push(`  placed ${p.ord_hash || ""} ${p.error ? "ERR: " + p.error : ""}`);
        }
        alert(lines.join("\n") || "No action.");
        await loadStrategies();
      } catch (e) {
        alert(e.message);
      }
    });
  });
}

async function loadStrategies() {
  try {
    const data = await fetchJson("/auto-trade/strategies");
    renderStrategies(data);
  } catch (e) {
    document.getElementById("at-strategies-list").innerHTML =
      `<p class="muted">Failed to load: ${esc(e.message)}</p>`;
  }
}

document.getElementById("add-strategy-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = document.getElementById("at-form-msg");
  const symbol = document.getElementById("at-symbol").value.trim().toUpperCase();
  const body = {
    symbol,
    account_balance: parseFloat(document.getElementById("at-balance").value),
    risk_per_trade: parseFloat(document.getElementById("at-risk").value),
    enabled: document.getElementById("at-enabled").checked,
    dry_run: document.getElementById("at-dry").checked,
    order_type: document.getElementById("at-order-type").value,
    min_tick_interval_sec: 0,
    exit_cooldown_sec: parseFloat(document.getElementById("at-cooldown").value) || 10,
    warmup_ticks: parseInt(document.getElementById("at-warmup").value, 10) || 10,
    max_pyramids: parseInt(document.getElementById("at-pyramids").value, 10) || 2,
    pin: document.getElementById("at-pin").value.trim(),
  };
  try {
    msg.textContent = "Saving…";
    await fetchJson("/auto-trade/strategies", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    msg.textContent = "Added.";
    ev.target.reset();
    document.getElementById("at-balance").value = "1000";
    document.getElementById("at-risk").value = "0.02";
    document.getElementById("at-cooldown").value = "10";
    document.getElementById("at-warmup").value = "10";
    document.getElementById("at-pyramids").value = "2";
    document.getElementById("at-dry").checked = true;
    await loadStrategies();
  } catch (e) {
    msg.textContent = e.message;
  }
});

document.getElementById("at-refresh").addEventListener("click", loadStrategies);

async function initAutoTrade() {
  const ok = await requireSession();
  if (!ok) return;
  await loadStrategies();
}

initAutoTrade();

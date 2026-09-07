// NQ / XAUUSD Signal Dashboard — frontend logic. No framework, just fetch + DOM.

const $ = (sel) => document.querySelector(sel);
const fmt = (n, d = 2) => (n === null || n === undefined || Number.isNaN(n)) ? "—" : Number(n).toFixed(d);

// ------------------------------------------------------------ feed status

async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    const el = $("#feed-badges");
    el.innerHTML = "";
    for (const [symbol, s] of Object.entries(data)) {
      const badge = document.createElement("div");
      let cls = "proxy", label = "no data";
      if (s.error) { cls = "error"; label = "no feed"; }
      else if (s.is_live) { cls = "live"; label = `live · ${s.provider}`; }
      else { cls = "delayed"; label = `delayed · ${s.provider}`; }
      badge.className = `feed-badge ${cls}`;
      badge.innerHTML = `<span class="dot"></span><b>${symbol}</b><span>${label}</span>` +
        (s.last_close !== undefined ? `<span class="px">${fmt(s.last_close)}</span>` : "");
      el.appendChild(badge);
    }
  } catch (e) {
    console.error("status refresh failed", e);
  }
}

// ------------------------------------------------------------ signals table

function ageLabel(isoTimestamp) {
  const mins = (Date.now() - new Date(isoTimestamp).getTime()) / 60000;
  if (mins < 1) return "just now";
  if (mins < 60) return `${Math.floor(mins)}m`;
  return `${(mins / 60).toFixed(1)}h`;
}

async function refreshSignals() {
  const symbol = $("#filter-symbol").value;
  const url = symbol ? `/api/signals?symbol=${encodeURIComponent(symbol)}` : "/api/signals";
  try {
    const res = await fetch(url);
    const rows = await res.json();
    const tbody = $("#signals-tbody");
    tbody.innerHTML = "";

    // Show valid + noise-risk as "active"; stale/invalid are logged but de-emphasized.
    const visible = rows.filter(r => r.status !== "invalid");

    if (visible.length === 0) {
      tbody.innerHTML = `<tr><td colspan="10" class="empty-row">No signals yet — the scheduler polls every minute. Placeholder strategy fires on a 20-SMA crossover, so this can be quiet for a while.</td></tr>`;
      return;
    }

    for (const r of visible) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><span class="status-chip ${r.status}" title="${escapeHtml(r.status_reason || "")}"><span class="dot"></span>${r.status}</span></td>
        <td>${r.symbol}</td>
        <td>${sideBadge(r.side)}</td>
        <td>${r.strategy_name}</td>
        <td>${fmt(r.entry)}</td>
        <td>${fmt(r.stop)}</td>
        <td>${fmt(r.target)}</td>
        <td>${fmt(r.rr, 2)}</td>
        <td>${ageLabel(r.timestamp)}</td>
        <td class="reason-cell">${escapeHtml(r.reason || "")}</td>
      `;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error("signals refresh failed", e);
  }
}

const ICON_UP = '<svg width="9" height="9" viewBox="0 0 10 10"><polygon points="5,0 10,10 0,10"/></svg>';
const ICON_DOWN = '<svg width="9" height="9" viewBox="0 0 10 10"><polygon points="0,0 10,0 5,10"/></svg>';

function sideBadge(side) {
  const icon = side === "long" ? ICON_UP : ICON_DOWN;
  return `<span class="side-badge ${side}">${icon}${side === "long" ? "LONG" : "SHORT"}</span>`;
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ------------------------------------------------------------ strategies

async function refreshStrategies() {
  try {
    const res = await fetch("/api/strategies");
    const strategies = await res.json();
    const list = $("#strategies-list");
    list.innerHTML = "";

    const btSelect = $("#bt-strategy");
    btSelect.innerHTML = "";

    for (const s of strategies) {
      const row = document.createElement("div");
      row.className = "strategy-row";
      row.innerHTML = `
        <div>
          <div class="name">${s.name}</div>
          <div class="desc">${escapeHtml(s.description)}</div>
        </div>
        <button class="toggle ${s.enabled ? "on" : ""}" data-name="${s.name}" aria-pressed="${s.enabled}"></button>
      `;
      list.appendChild(row);

      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.name;
      btSelect.appendChild(opt);
    }

    list.querySelectorAll(".toggle").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const name = btn.dataset.name;
        const res = await fetch(`/api/strategies/${encodeURIComponent(name)}/toggle`, { method: "POST" });
        const data = await res.json();
        btn.classList.toggle("on", data.enabled);
        btn.setAttribute("aria-pressed", data.enabled);
      });
    });
  } catch (e) {
    console.error("strategies refresh failed", e);
  }
}

// ------------------------------------------------------------ backtest

let equityChart = null;
let equitySeries = null;

function ensureChart() {
  if (equityChart) return;
  equityChart = LightweightCharts.createChart($("#equity-chart"), {
    layout: { background: { color: "transparent" }, textColor: "#c3c2b7" },
    grid: {
      vertLines: { color: "#2c2c2a" },
      horzLines: { color: "#2c2c2a" },
    },
    rightPriceScale: { borderColor: "#2c2c2a" },
    timeScale: { borderColor: "#2c2c2a" },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    height: 260,
  });
  equitySeries = equityChart.addAreaSeries({
    lineColor: "#3987e5",
    topColor: "rgba(57,135,229,0.28)",
    bottomColor: "rgba(57,135,229,0.02)",
    lineWidth: 2,
  });
  new ResizeObserver(() => {
    equityChart.applyOptions({ width: $("#equity-chart").clientWidth });
  }).observe($("#equity-chart"));
}

function metricTile(label, value, cls = "") {
  return `<div class="metric-tile"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`;
}

function renderMetrics(m) {
  const pf = m.profit_factor;
  const pfStr = pf === null ? "—" : (pf === Infinity ? "∞" : fmt(pf, 2));
  const html = [
    metricTile("Total trades", m.total_trades),
    metricTile("Win rate", `${fmt(m.win_rate * 100, 1)}%`),
    metricTile("Avg RR (wins)", fmt(m.avg_rr, 2)),
    metricTile("Profit factor", pfStr, pf > 1 ? "pos" : (pf !== null ? "neg" : "")),
    metricTile("Expectancy (R)", fmt(m.expectancy_r, 3), m.expectancy_r > 0 ? "pos" : "neg"),
    metricTile("Max drawdown (R)", fmt(m.max_drawdown_r, 2), "neg"),
    metricTile("Sharpe", m.sharpe === null ? "—" : fmt(m.sharpe, 2)),
    metricTile("Total (R)", fmt(m.total_r, 2), m.total_r > 0 ? "pos" : "neg"),
  ].join("");
  $("#bt-metrics").innerHTML = html;
}

$("#backtest-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("#bt-run");
  const statusEl = $("#bt-status");
  btn.disabled = true;
  statusEl.className = "bt-status";
  statusEl.textContent = "Running backtest…";
  $("#bt-results").hidden = true;

  const payload = {
    strategy: $("#bt-strategy").value,
    symbol: $("#bt-symbol").value,
    timeframe: $("#bt-timeframe").value,
    count: parseInt($("#bt-count").value, 10) || 1500,
  };

  try {
    const res = await fetch("/api/backtest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "backtest failed");

    statusEl.textContent = `${data.bars_used} bars from ${data.provider}${data.is_live ? "" : " (delayed/historical)"}.`;
    renderMetrics(data.metrics);

    ensureChart();
    const points = data.equity_curve.map(p => ({
      time: Math.floor(new Date(p.timestamp).getTime() / 1000),
      value: p.equity,
    }));
    equitySeries.setData(points);
    equityChart.timeScale().fitContent();

    $("#bt-results").hidden = false;
  } catch (err) {
    statusEl.className = "bt-status error";
    statusEl.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------ wiring

$("#filter-symbol").addEventListener("change", refreshSignals);
$("#refresh-signals").addEventListener("click", refreshSignals);

async function init() {
  await Promise.all([refreshStatus(), refreshStrategies(), refreshSignals()]);
  setInterval(refreshStatus, 15000);
  setInterval(refreshSignals, 20000);
}

init();

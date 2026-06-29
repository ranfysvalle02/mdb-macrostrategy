"use strict";

const $ = (id) => document.getElementById(id);
const PG = "#4f9bd9";
const MONGO = "#00ed64";

Chart.defaults.color = "#8b949e";
Chart.defaults.borderColor = "#2a313c";
Chart.defaults.font.family = "ui-monospace, SFMono-Regular, Menlo, monospace";

const charts = {};

async function checkHealth() {
  const setBadge = (id, ok) => {
    const el = $(id);
    el.dataset.state = ok ? "up" : "down";
    el.textContent = el.textContent.split(" ")[0] + (ok ? " up" : " down");
  };
  try {
    const r = await fetch("/api/health");
    const h = await r.json();
    setBadge("health-pg", h.postgres);
    setBadge("health-mongo", h.mongodb);
    return h.postgres && h.mongodb;
  } catch (e) {
    setBadge("health-pg", false);
    setBadge("health-mongo", false);
    return false;
  }
}

function params() {
  return {
    rows: +$("rows").value,
    ops: +$("ops").value,
    workers: +$("workers").value,
    doc_kb: +$("doc_kb").value,
    seed: +$("seed").value,
    trials: +$("trials").value,
    pg_strategy: $("pg_strategy").value,
    pg_autovacuum: $("pg_autovacuum").checked,
  };
}

function setProgress(frac, phase) {
  $("progress").style.width = (frac * 100).toFixed(1) + "%";
  if (phase) $("phase").textContent = phase;
}

function overallFraction(phase, frac) {
  // PG phases occupy the first half of the bar, Mongo the second.
  if (phase.startsWith("mongo")) return 0.5 + frac * 0.5;
  if (phase.startsWith("pg")) return frac * 0.5;
  return frac;
}

function num(v, digits = 0) {
  return Number(v).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function renderTable(pg, mongo) {
  const tbody = $("results-table").querySelector("tbody");
  const pgT = pg.extra && pg.extra.trials;
  const mgT = mongo.extra && mongo.extra.trials;
  // Append " ±stdev" to a cell when multi-trial variance is available.
  const sd = (agg, fmt) => (agg ? ` \u00B1${fmt(agg.stdev)}` : "");
  const n = (pgT && pgT.n) || (mgT && mgT.n) || 1;
  const rows = [
    ["Engine", pg.label, mongo.label, null],
    ["Throughput (ops/s)",
      num(pg.throughput_ops_s) + sd(pgT && pgT.throughput_ops_s, num),
      num(mongo.throughput_ops_s) + sd(mgT && mgT.throughput_ops_s, num), "max"],
    ["Latency p50 (ms)", pg.latency_ms.p50.toFixed(3), mongo.latency_ms.p50.toFixed(3), "min"],
    ["Latency p95 (ms)", pg.latency_ms.p95.toFixed(3), mongo.latency_ms.p95.toFixed(3), "min"],
    ["Latency p99 (ms)",
      pg.latency_ms.p99.toFixed(3) + sd(pgT && pgT.latency_p99_ms, (v) => v.toFixed(3)),
      mongo.latency_ms.p99.toFixed(3) + sd(mgT && mgT.latency_p99_ms, (v) => v.toFixed(3)), "min"],
    ["Size before (MB)", pg.size_before_mb.toFixed(2), mongo.size_before_mb.toFixed(2), null],
    ["Size after (MB)", pg.size_after_mb.toFixed(2), mongo.size_after_mb.toFixed(2), null],
    ["Size growth (MB)",
      pg.size_growth_mb.toFixed(2) + sd(pgT && pgT.size_growth_mb, (v) => v.toFixed(2)),
      mongo.size_growth_mb.toFixed(2) + sd(mgT && mgT.size_growth_mb, (v) => v.toFixed(2)), "min"],
    ["Trials", n, n, null],
    ["Measured ops", num(pg.ops), num(mongo.ops), null],
    ["PG dead tuples", pg.extra.n_dead_tup ?? "-", "-", null],
    ["PG HOT updates", pg.extra.n_tup_hot_upd ?? "-", "-", null],
  ];
  tbody.innerHTML = "";
  for (const [metric, a, b, better] of rows) {
    const tr = document.createElement("tr");
    let pgWin = "", mgWin = "";
    if (better && a !== "-" && b !== "-") {
      const av = parseFloat(String(a).replace(/,/g, ""));
      const bv = parseFloat(String(b).replace(/,/g, ""));
      if (av !== bv) {
        const pgBetter = better === "max" ? av > bv : av < bv;
        pgWin = pgBetter ? "win" : "";
        mgWin = pgBetter ? "" : "win";
      }
    }
    tr.innerHTML = `<td>${metric}</td><td class="pg ${pgWin}">${a}</td><td class="mongo ${mgWin}">${b}</td>`;
    tbody.appendChild(tr);
  }
}

function bar(canvasId, title, pgVal, mongoVal, unit) {
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart($(canvasId), {
    type: "bar",
    data: {
      labels: ["PostgreSQL", "MongoDB"],
      datasets: [{
        data: [pgVal, mongoVal],
        backgroundColor: [PG, MONGO],
        borderRadius: 6,
        maxBarThickness: 90,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        title: { display: true, text: title, color: "#e6edf3", font: { size: 14 } },
        tooltip: { callbacks: { label: (c) => ` ${num(c.parsed.y, 2)} ${unit}` } },
      },
      scales: { y: { beginAtZero: true, ticks: { callback: (v) => num(v) } } },
    },
  });
}

function renderCharts(pg, mongo) {
  bar("chart-throughput", "Throughput (ops/sec, higher is better)", pg.throughput_ops_s, mongo.throughput_ops_s, "ops/s");
  bar("chart-latency", "Latency p99 (ms, lower is better)", pg.latency_ms.p99, mongo.latency_ms.p99, "ms");
  bar("chart-size", "Storage growth (MB, lower is better)", pg.size_growth_mb, mongo.size_growth_mb, "MB");
}

function run() {
  const btn = $("run");
  btn.disabled = true;
  $("status").textContent = "running...";
  $("phase").textContent = "";
  setProgress(0);

  const qs = new URLSearchParams(
    Object.entries(params()).map(([k, v]) => [k, String(v)])
  ).toString();
  const es = new EventSource(`/api/benchmark/stream?${qs}`);

  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "progress") {
      setProgress(overallFraction(msg.phase, msg.frac), msg.phase);
    } else if (msg.type === "done") {
      setProgress(1, "done");
      $("results-card").hidden = false;
      renderCharts(msg.postgres, msg.mongodb);
      renderTable(msg.postgres, msg.mongodb);
      $("status").textContent = "complete";
      btn.disabled = false;
      es.close();
    } else if (msg.type === "error") {
      $("status").textContent = "error: " + msg.message;
      $("phase").textContent = "";
      btn.disabled = false;
      es.close();
    }
  };

  es.onerror = () => {
    $("status").textContent = "connection lost";
    btn.disabled = false;
    es.close();
  };
}

$("run").addEventListener("click", run);
checkHealth();
setInterval(checkHealth, 15000);

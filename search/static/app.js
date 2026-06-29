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
    mode: $("mode").value,
    corpus_size: +$("corpus_size").value,
    dim: +$("dim").value,
    k: +$("k").value,
    n_queries: +$("n_queries").value,
    seed: +$("seed").value,
    trials: +$("trials").value,
    pg_hnsw_ef_search: +$("pg_hnsw_ef_search").value,
    mongo_num_candidates: +$("mongo_num_candidates").value,
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
  const sd = (agg, fmt) => (agg ? ` \u00B1${fmt(agg.stdev)}` : "");
  const n = (pgT && pgT.n) || (mgT && mgT.n) || 1;
  const mbOrNA = (v) => (v ? v.toFixed(3) : "n/a*");
  const rows = [
    ["Engine", pg.label, mongo.label, null],
    ["Quality@k",
      pg.recall_at_k.toFixed(4) + sd(pgT && pgT.recall_at_k, (v) => v.toFixed(4)),
      mongo.recall_at_k.toFixed(4) + sd(mgT && mgT.recall_at_k, (v) => v.toFixed(4)), "max"],
    ["Query throughput (qps)", num(pg.qps), num(mongo.qps), "max"],
    ["Latency p50 (ms)", pg.latency_ms.p50.toFixed(3), mongo.latency_ms.p50.toFixed(3), "min"],
    ["Latency p95 (ms)", pg.latency_ms.p95.toFixed(3), mongo.latency_ms.p95.toFixed(3), "min"],
    ["Latency p99 (ms)",
      pg.latency_ms.p99.toFixed(3) + sd(pgT && pgT.latency_p99_ms, (v) => v.toFixed(3)),
      mongo.latency_ms.p99.toFixed(3) + sd(mgT && mgT.latency_p99_ms, (v) => v.toFixed(3)), "min"],
    ["Index build (s)",
      pg.index_build_s.toFixed(3) + sd(pgT && pgT.index_build_s, (v) => v.toFixed(3)),
      mongo.index_build_s.toFixed(3) + sd(mgT && mgT.index_build_s, (v) => v.toFixed(3)), "min"],
    ["Index size (MB)", mbOrNA(pg.index_size_mb), mbOrNA(mongo.index_size_mb), null],
    ["Quality kind", pg.extra.recall_kind || "-", mongo.extra.recall_kind || "-", null],
    ["Trials", n, n, null],
  ];
  tbody.innerHTML = "";
  for (const [metric, a, b, better] of rows) {
    const tr = document.createElement("tr");
    let pgWin = "", mgWin = "";
    if (better && a !== "n/a*" && b !== "n/a*") {
      const av = parseFloat(String(a).replace(/,/g, ""));
      const bv = parseFloat(String(b).replace(/,/g, ""));
      if (Number.isFinite(av) && Number.isFinite(bv) && av !== bv) {
        const pgBetter = better === "max" ? av > bv : av < bv;
        pgWin = pgBetter ? "win" : "";
        mgWin = pgBetter ? "" : "win";
      }
    }
    tr.innerHTML = `<td>${metric}</td><td class="pg ${pgWin}">${a}</td><td class="mongo ${mgWin}">${b}</td>`;
    tbody.appendChild(tr);
  }
}

function bar(canvasId, title, pgVal, mongoVal, unit, digits = 2) {
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
        tooltip: { callbacks: { label: (c) => ` ${num(c.parsed.y, digits)} ${unit}` } },
      },
      scales: { y: { beginAtZero: true, ticks: { callback: (v) => num(v, digits) } } },
    },
  });
}

function renderCharts(pg, mongo) {
  bar("chart-recall", "Quality@k (higher is better)", pg.recall_at_k, mongo.recall_at_k, "", 4);
  bar("chart-latency", "Latency p99 (ms, lower is better)", pg.latency_ms.p99, mongo.latency_ms.p99, "ms", 3);
  bar("chart-build", "Index build (s, lower is better)", pg.index_build_s, mongo.index_build_s, "s", 3);
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
  const es = new EventSource(`/api/search-benchmark/stream?${qs}`);

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

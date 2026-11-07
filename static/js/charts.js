/* CloudInsight AI — Chart.js dashboards */
(function () {
  if (!window.Chart) return;
  Chart.defaults.font.family = "Manrope, sans-serif";
  Chart.defaults.color = "#5B6B85";
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.animation.duration = 900;

  const C = { blue: "#2563EB", purple: "#7C3AED", cyan: "#06B6D4", navy: "#0F172A",
              rose: "#E11D48", amber: "#D97706", track: "#E8EEF7" };

  const centerText = {
    id: "centerText",
    afterDraw(chart) {
      const o = chart.config.options.centerText; if (!o) return;
      const meta = chart.getDatasetMeta(0); if (!meta.data[0]) return;
      const { x, y } = meta.data[0], ctx = chart.ctx;
      ctx.save(); ctx.textAlign = "center";
      ctx.fillStyle = C.navy; ctx.font = "700 30px Sora"; ctx.fillText(o.value, x, y);
      ctx.fillStyle = "#5B6B85"; ctx.font = "700 10px Manrope"; ctx.fillText(o.label, x, y + 20);
      ctx.restore();
    }
  };
  Chart.register(centerText);

  function scoreColor(s) { return s >= 85 ? C.cyan : s >= 70 ? C.blue : s >= 55 ? C.purple : C.rose; }

  const RC = window.REPORT_CHART, DC = window.DASH_CHART;

  if (RC) {
    const cmp = document.getElementById("costCompareChart");
    if (cmp) new Chart(cmp, {
      type: "bar",
      data: { labels: RC.cost.labels, datasets: [
        { label: "Storage /mo (est.)", data: RC.cost.storage, backgroundColor: C.blue, borderRadius: 6 },
        { label: "Compute /mo (est.)", data: RC.cost.compute, backgroundColor: C.purple, borderRadius: 6 },
        { label: "Network /mo (est.)", data: RC.cost.network, backgroundColor: C.cyan, borderRadius: 6 }]},
      options: { responsive: true, maintainAspectRatio: false,
        scales: { x: { stacked: true }, y: { stacked: true, ticks: { callback: (v) => "$" + v } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: $${c.parsed.y.toFixed(2)}` } } } }
    });

    const gauge = document.getElementById("scoreGauge");
    if (gauge) new Chart(gauge, {
      type: "doughnut",
      data: { datasets: [{ data: [RC.security.score, 100 - RC.security.score],
        backgroundColor: [scoreColor(RC.security.score), C.track], borderWidth: 0, cutout: "76%" }] },
      options: { responsive: true, maintainAspectRatio: false,
        centerText: { value: RC.security.score, label: "of 100" }, plugins: { legend: { display: false } } }
    });

    const dist = document.getElementById("riskDist");
    if (dist) new Chart(dist, {
      type: "doughnut",
      data: { labels: ["High", "Medium", "Low"],
        datasets: [{ data: [RC.security.high, RC.security.medium, RC.security.low],
          backgroundColor: [C.rose, C.amber, C.cyan], borderWidth: 3, borderColor: "#fff", cutout: "58%" }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } }
    });

    const weight = { gap: 3, review: 2, ok: 1 };
    const bar = document.getElementById("domainBar");
    if (bar) new Chart(bar, {
      type: "bar",
      data: { labels: RC.domains.map((d) => d.domain),
        datasets: [{ label: "Finding weight", data: RC.domains.map((d) => weight[d.status]),
          backgroundColor: RC.domains.map((d) => d.status === "gap" ? C.rose : d.status === "review" ? C.amber : C.cyan), borderRadius: 5 }]},
      options: { indexAxis: "y", responsive: true, maintainAspectRatio: false,
        scales: { x: { max: 3, ticks: { stepSize: 1 } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: (c) => ({ 3: "Gap identified", 2: "Review recommended", 1: "Controlled" })[c.parsed.x] } } } }
    });

    radarChart(document.getElementById("radarChart"), RC.radar, RC.provider);
  }

  if (DC) {
    const cost = document.getElementById("dashCost");
    if (cost) new Chart(cost, {
      type: "bar",
      data: { labels: DC.cost.labels, datasets: [{ label: "Estimated monthly cost",
        data: DC.cost.monthly, borderRadius: 7,
        backgroundColor: DC.cost.labels.map((l) => l === DC.provider ? C.blue : "#C7D4EC") }]},
      options: { responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => "$" + c.parsed.y.toFixed(2) + " /mo (estimate)" } } },
        scales: { y: { ticks: { callback: (v) => "$" + v } } } }
    });

    const risk = document.getElementById("dashRisk");
    if (risk) new Chart(risk, {
      type: "doughnut",
      data: { labels: ["High", "Medium", "Low"],
        datasets: [{ data: [DC.security.high, DC.security.medium, DC.security.low],
          backgroundColor: [C.rose, C.amber, C.cyan], borderWidth: 3, borderColor: "#fff", cutout: "58%" }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } }
    });

    const stor = document.getElementById("dashStorage");
    if (stor) new Chart(stor, {
      type: "doughnut",
      data: { labels: ["Raw data", "Redundancy overhead (est.)", "Backup copies (est.)"],
        datasets: [{ data: [DC.storage.raw, DC.storage.redundancy, DC.storage.backup],
          backgroundColor: [C.blue, C.purple, C.cyan], borderWidth: 3, borderColor: "#fff", cutout: "58%" }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom" } } }
    });

    radarChart(document.getElementById("dashRadar"), DC.radar, DC.provider);
  }

  /* Provider suitability ranking */
  if (RC && RC.suit) {
    const sEl = document.getElementById("suitabilityChart");
    if (sEl) new Chart(sEl, {
      type: "bar",
      data: { labels: RC.suit.labels, datasets: [{ label: "Overall suitability (0–100)",
        data: RC.suit.overall, borderRadius: 7,
        backgroundColor: RC.suit.labels.map((l) => l === RC.suit.winner ? C.blue : "#C7D4EC") }]},
      options: { indexAxis: "y", responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => c.parsed.x + " / 100 (configured estimate)" } } },
        scales: { x: { min: 0, max: 100 } } }
    });
  }

  function radarChart(canvas, scores, selected) {
    if (!canvas) return;
    const axes = ["security", "scalability", "availability", "compliance", "cost_efficiency"];
    const avg = axes.map((a) => Math.round(Object.values(scores).reduce((s, p) => s + p[a], 0) / Object.keys(scores).length));
    new Chart(canvas, {
      type: "radar",
      data: { labels: ["Security", "Scalability", "Availability", "Compliance", "Cost efficiency"],
        datasets: [
          { label: selected, data: axes.map((a) => scores[selected][a]),
            borderColor: C.blue, backgroundColor: "rgba(37,99,235,.14)", pointBackgroundColor: C.blue },
          { label: "Provider average", data: avg, borderColor: C.purple,
            backgroundColor: "rgba(124,58,237,.08)", pointBackgroundColor: C.purple, borderDash: [5, 4] }]},
      options: { responsive: true, maintainAspectRatio: false,
        scales: { r: { min: 0, max: 100, ticks: { stepSize: 25, backdropColor: "transparent" },
          grid: { color: "#E4EAF5" }, angleLines: { color: "#E4EAF5" }, pointLabels: { font: { weight: 700, size: 11 } } } } }
    });
  }
    /* Historical trend chart */
  if (window.TREND_CHART && window.TREND_CHART.labels.length > 1) {
    const tEl = document.getElementById("trendChart");
    if (tEl) new Chart(tEl, {
      type: "line",
      data: { labels: window.TREND_CHART.labels, datasets: [
        { label: "Estimated monthly cost ($)", data: window.TREND_CHART.cost,
          borderColor: C.blue, backgroundColor: "rgba(37,99,235,.10)",
          yAxisID: "y", tension: 0.35, fill: true, pointRadius: 4 },
        { label: "Security score (/100)", data: window.TREND_CHART.score,
          borderColor: C.cyan, backgroundColor: "rgba(6,182,212,.10)",
          yAxisID: "y1", tension: 0.35, fill: true, pointRadius: 4 }]},
      options: { responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom" } },
        scales: {
          y:  { position: "left",  ticks: { callback: (v) => "$" + v }, grid: { color: "#E4EAF5" } },
          y1: { position: "right", min: 0, max: 100, grid: { drawOnChartArea: false } } } }
    });
  }
})();
/**
 * Fetch portfolio snapshots from the API and render Chart.js charts.
 *
 * Expects two canvas elements on the page:
 *   - #portfolioChart  (line chart — portfolio value over time)
 *   - #pnlChart        (bar chart — daily PnL, green/red colouring)
 */
async function loadCharts() {
  const resp = await fetch("/api/portfolio");
  const data = await resp.json();

  if (data.length === 0) return;

  const labels = data.map((d) => d.timestamp.split("T")[0]);
  const values = data.map((d) => d.total_value);
  const dailyPnl = data.map((d) => d.daily_pnl);

  // Portfolio value line chart
  new Chart(document.getElementById("portfolioChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Portfolio Value ($)",
          data: values,
          borderColor: "#10B981",
          backgroundColor: "rgba(16, 185, 129, 0.1)",
          fill: true,
          tension: 0.3,
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#D1D5DB" } } },
      scales: {
        x: { ticks: { color: "#9CA3AF" }, grid: { color: "#374151" } },
        y: {
          beginAtZero: false,
          ticks: { color: "#9CA3AF" },
          grid: { color: "#374151" },
        },
      },
    },
  });

  // Daily PnL bar chart
  new Chart(document.getElementById("pnlChart"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Daily PnL ($)",
          data: dailyPnl,
          backgroundColor: dailyPnl.map((v) =>
            v >= 0 ? "#10B981" : "#EF4444"
          ),
        },
      ],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: "#D1D5DB" } } },
      scales: {
        x: { ticks: { color: "#9CA3AF" }, grid: { color: "#374151" } },
        y: { ticks: { color: "#9CA3AF" }, grid: { color: "#374151" } },
      },
    },
  });
}

document.addEventListener("DOMContentLoaded", loadCharts);

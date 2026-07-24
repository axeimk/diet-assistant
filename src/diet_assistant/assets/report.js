(() => {
  "use strict";

  const dataElement = document.getElementById("report-data");
  if (!dataElement || typeof Chart === "undefined") {
    return;
  }

  const report = JSON.parse(dataElement.textContent || "{}");
  const darkMode = window.matchMedia("(prefers-color-scheme: dark)");
  const colors = darkMode.matches
    ? {
        text: "#eef7f1",
        muted: "#a8b8ae",
        grid: "rgba(168, 184, 174, 0.18)",
        calorie: "#6fcba7",
        range: "rgba(111, 203, 167, 0.15)",
        target: "#e7b36b",
        weight: "#83b4ed",
        average: "#c09aea",
        exercise: "#e49577",
      }
    : {
        text: "#17231d",
        muted: "#637169",
        grid: "rgba(99, 113, 105, 0.16)",
        calorie: "#227a5a",
        range: "rgba(34, 122, 90, 0.13)",
        target: "#8b5d24",
        weight: "#3569a8",
        average: "#7449a5",
        exercise: "#b35b3d",
      };

  Chart.defaults.color = colors.muted;
  Chart.defaults.font.family =
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", sans-serif';
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 9;

  const baseOptions = (unit, tooltipAfterBody) => ({
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: "index",
      intersect: false,
    },
    animation: {
      duration: 350,
    },
    plugins: {
      legend: {
        position: "bottom",
        align: "start",
        labels: {
          padding: 18,
        },
      },
      tooltip: {
        callbacks: {
          label(context) {
            const value = context.parsed.y;
            if (value === null || value === undefined) {
              return `${context.dataset.label}: 記録なし`;
            }
            return `${context.dataset.label}: ${value.toLocaleString("ja-JP")} ${unit}`;
          },
          afterBody: tooltipAfterBody || (() => []),
        },
      },
    },
    scales: {
      x: {
        grid: {
          display: false,
        },
        ticks: {
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 8,
        },
      },
      y: {
        beginAtZero: unit !== "kg",
        grid: {
          color: colors.grid,
        },
        title: {
          display: true,
          text: unit,
        },
      },
    },
  });

  const lineDataset = (label, values, color, extra = {}) => ({
    label,
    data: values,
    borderColor: color,
    backgroundColor: color,
    borderWidth: 2,
    pointRadius: 2.5,
    pointHoverRadius: 5,
    tension: 0.2,
    spanGaps: false,
    ...extra,
  });

  const buildLineChart = (id, labels, datasets, unit, tooltipAfterBody) => {
    const canvas = document.getElementById(id);
    if (!canvas) {
      return;
    }
    new Chart(canvas, {
      type: "line",
      data: { labels, datasets },
      options: baseOptions(unit, tooltipAfterBody),
    });
  };

  const buildBarChart = (id, labels, values, unit, tooltipAfterBody) => {
    const canvas = document.getElementById(id);
    if (!canvas) {
      return;
    }
    new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "運動時間",
            data: values,
            backgroundColor: colors.exercise,
            borderRadius: 5,
            maxBarThickness: 30,
          },
        ],
      },
      options: baseOptions(unit, tooltipAfterBody),
    });
  };

  const shortDate = (value) => value.slice(5).replace("-", "/");
  const trend = report.trend || [];

  if (report.kind === "daily") {
    const labels = trend.map((point) => shortDate(point.date));
    buildLineChart(
      "calorie-chart",
      labels,
      [
        lineDataset(
          "推定下限",
          trend.map((point) => point.calories_min),
          colors.calorie,
          {
            borderWidth: 1,
            borderDash: [3, 4],
            pointRadius: 0,
          },
        ),
        lineDataset(
          "推定上限",
          trend.map((point) => point.calories_max),
          colors.calorie,
          {
            borderWidth: 1,
            borderDash: [3, 4],
            pointRadius: 0,
            fill: "-1",
            backgroundColor: colors.range,
          },
        ),
        lineDataset(
          "代表値",
          trend.map((point) => point.calories),
          colors.calorie,
        ),
        lineDataset(
          "目標",
          trend.map((point) => point.target_calories),
          colors.target,
          {
            borderDash: [7, 5],
            pointRadius: 0,
            borderWidth: 1.5,
          },
        ),
      ],
      "kcal",
    );
    buildLineChart(
      "weight-chart",
      labels,
      [
        lineDataset(
          "実測値",
          trend.map((point) => point.weight),
          colors.weight,
          { showLine: false, pointRadius: 4 },
        ),
        lineDataset(
          "7日移動平均",
          trend.map((point) => point.weight_moving_average),
          colors.average,
          { pointRadius: 1.5, borderWidth: 2.5 },
        ),
      ],
      "kg",
    );
    buildBarChart(
      "exercise-chart",
      labels,
      trend.map((point) => point.exercise_minutes),
      "分",
    );
  }

  if (report.kind === "weekly") {
    const labels = trend.map(
      (point) => `${shortDate(point.period_start)}–${shortDate(point.period_end)}`,
    );
    const coverage = (context) => {
      if (!context.length) {
        return [];
      }
      const point = trend[context[0].dataIndex];
      return [`記録日数: ${point.recorded_meal_days}/7日`];
    };
    buildLineChart(
      "calorie-chart",
      labels,
      [
        lineDataset(
          "週平均",
          trend.map((point) => point.average_calories),
          colors.calorie,
          { pointRadius: 4 },
        ),
      ],
      "kcal/日",
      coverage,
    );
    buildLineChart(
      "weight-chart",
      labels,
      [
        lineDataset(
          "週平均",
          trend.map((point) => point.average_weight),
          colors.weight,
          { pointRadius: 4 },
        ),
      ],
      "kg",
      (context) => {
        if (!context.length) {
          return [];
        }
        const point = trend[context[0].dataIndex];
        return [`測定数: ${point.weight_measurements}回`];
      },
    );
    buildBarChart(
      "exercise-chart",
      labels,
      trend.map((point) => point.exercise_minutes),
      "分",
      (context) => {
        if (!context.length) {
          return [];
        }
        const point = trend[context[0].dataIndex];
        return [`記録日数: ${point.recorded_exercise_days}/7日`];
      },
    );
  }

  if (typeof darkMode.addEventListener === "function") {
    darkMode.addEventListener("change", () => window.location.reload());
  }
})();

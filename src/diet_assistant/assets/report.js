(() => {
  "use strict";

  const dataElement = document.getElementById("report-data");
  if (!dataElement || typeof Chart === "undefined") {
    return;
  }

  const report = JSON.parse(dataElement.textContent || "{}");
  const darkMode = window.matchMedia("(prefers-color-scheme: dark)");

  // 実測 = 青、目標や移動平均などの参照系列 = 橙。両モードともCVD検証済み。
  const palette = darkMode.matches
    ? {
        surface: "#141413",
        ink: "#f0eee6",
        secondary: "#b4b0a4",
        muted: "#8a8880",
        grid: "#302f2b",
        axis: "#4a4945",
        measured: "#3987e5",
        measuredWash: "rgba(57, 135, 229, 0.14)",
        reference: "#d95926",
      }
    : {
        surface: "#faf9f6",
        ink: "#1b1a16",
        secondary: "#57544c",
        muted: "#8a867b",
        grid: "#e6e3da",
        axis: "#c9c5b8",
        measured: "#2a78d6",
        measuredWash: "rgba(42, 120, 214, 0.10)",
        reference: "#eb6834",
      };

  Chart.defaults.color = palette.muted;
  Chart.defaults.font.family =
    '-apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", sans-serif';
  Chart.defaults.font.size = 11;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.boxHeight = 8;

  const baseOptions = ({ unit, showLegend, tooltipAfterBody }) => ({
    responsive: true,
    maintainAspectRatio: false,
    layout: { padding: { top: 4, right: 2, bottom: 0, left: 0 } },
    interaction: { mode: "index", intersect: false },
    animation: { duration: 0 },
    plugins: {
      legend: {
        display: showLegend,
        position: "bottom",
        align: "start",
        labels: {
          padding: 16,
          color: palette.secondary,
          // 推定範囲の帯は装飾であって系列ではないので凡例に出さない。
          // 1点も無い系列も、あるかのように見えるので出さない（目標未設定のときの「目標」など）。
          filter: (item, data) => {
            const dataset = data.datasets[item.datasetIndex];
            return !dataset.chrome && hasValue(dataset.data);
          },
        },
      },
      tooltip: {
        backgroundColor: palette.ink,
        titleColor: palette.surface,
        bodyColor: palette.surface,
        cornerRadius: 2,
        padding: 8,
        displayColors: true,
        boxWidth: 8,
        boxHeight: 8,
        usePointStyle: true,
        filter: (item) => !item.dataset.chrome && hasValue(item.dataset.data),
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
        border: { color: palette.axis },
        grid: { display: false },
        ticks: {
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 8,
          padding: 6,
        },
      },
      y: {
        beginAtZero: unit !== "kg",
        border: { display: false },
        grid: { color: palette.grid, drawTicks: false },
        ticks: { padding: 8, maxTicksLimit: 6 },
      },
    },
  });

  const lineDataset = (label, values, color, extra = {}) => ({
    label,
    data: values,
    borderColor: color,
    backgroundColor: color,
    borderWidth: 2,
    borderCapStyle: "round",
    borderJoinStyle: "round",
    pointRadius: 0,
    pointHoverRadius: 5,
    pointHoverBorderWidth: 2,
    pointHoverBorderColor: palette.surface,
    tension: 0.15,
    spanGaps: false,
    ...extra,
  });

  const dotDataset = (label, values, color, extra = {}) =>
    lineDataset(label, values, color, {
      showLine: false,
      pointRadius: 4,
      pointBorderWidth: 2,
      pointBorderColor: palette.surface,
      pointHoverRadius: 6,
      ...extra,
    });

  const hasValue = (values) => values.some((value) => value !== null && value !== undefined);

  // 記録が1件もない期間に空の格子だけを描いても何も伝わらないので、文で置き換える。
  const showEmptyPlot = (canvas) => {
    const frame = canvas.parentElement;
    if (!frame) {
      return;
    }
    const note = document.createElement("p");
    note.className = "plot__empty";
    note.textContent = "この期間に記録がありません。";
    frame.replaceChildren(note);
    frame.classList.add("plot__frame--empty");
  };

  const buildLineChart = (id, labels, datasets, options) => {
    const canvas = document.getElementById(id);
    if (!canvas) {
      return;
    }
    if (!datasets.some((dataset) => hasValue(dataset.data))) {
      showEmptyPlot(canvas);
      return;
    }
    new Chart(canvas, {
      type: "line",
      data: { labels, datasets },
      options: baseOptions(options),
    });
  };

  const buildBarChart = (id, labels, values, options) => {
    const canvas = document.getElementById(id);
    if (!canvas) {
      return;
    }
    if (!hasValue(values)) {
      showEmptyPlot(canvas);
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
            backgroundColor: palette.measured,
            borderRadius: { topLeft: 4, topRight: 4 },
            borderSkipped: "bottom",
            maxBarThickness: 18,
          },
        ],
      },
      options: baseOptions(options),
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
        // 推定範囲は下限を無地の線にして上限から塗り、帯だけを見せる。
        lineDataset(
          "推定下限",
          trend.map((point) => point.calories_min),
          "transparent",
          { borderWidth: 0, pointHoverRadius: 0, chrome: true },
        ),
        lineDataset(
          "推定上限",
          trend.map((point) => point.calories_max),
          "transparent",
          {
            borderWidth: 0,
            pointHoverRadius: 0,
            fill: "-1",
            backgroundColor: palette.measuredWash,
            chrome: true,
          },
        ),
        lineDataset(
          "摂取カロリー",
          trend.map((point) => point.calories),
          palette.measured,
          { pointRadius: 2 },
        ),
        lineDataset(
          "目標",
          trend.map((point) => point.target_calories),
          palette.reference,
          // 目標が単日しか存在しない期間でも見えるよう、線に加えて小さな点を打つ。
          { borderWidth: 1.5, borderDash: [5, 4], pointRadius: 1.5 },
        ),
      ],
      {
        unit: "kcal",
        showLegend: true,
        tooltipAfterBody: (context) => {
          if (!context.length) {
            return [];
          }
          const point = trend[context[0].dataIndex];
          if (point.calories_min === null || point.calories_max === null) {
            return [];
          }
          const range = `${point.calories_min.toLocaleString("ja-JP")}–${point.calories_max.toLocaleString("ja-JP")}`;
          return [`推定範囲: ${range} kcal`];
        },
      },
    );
    buildLineChart(
      "weight-chart",
      labels,
      [
        dotDataset(
          "実測値",
          trend.map((point) => point.weight),
          palette.measured,
        ),
        lineDataset(
          "7日移動平均",
          trend.map((point) => point.weight_moving_average),
          palette.reference,
        ),
      ],
      { unit: "kg", showLegend: true },
    );
    buildBarChart(
      "exercise-chart",
      labels,
      trend.map((point) => point.exercise_minutes),
      { unit: "分", showLegend: false },
    );
  }

  if (report.kind === "weekly") {
    const labels = trend.map((point) => shortDate(point.period_end));
    const mealCoverage = (context) => {
      if (!context.length) {
        return [];
      }
      return [`記録日数: ${trend[context[0].dataIndex].recorded_meal_days}/7日`];
    };
    buildLineChart(
      "calorie-chart",
      labels,
      [
        lineDataset(
          "週平均",
          trend.map((point) => point.average_calories),
          palette.measured,
          { pointRadius: 3 },
        ),
      ],
      { unit: "kcal/日", showLegend: false, tooltipAfterBody: mealCoverage },
    );
    buildLineChart(
      "weight-chart",
      labels,
      [
        lineDataset(
          "週平均",
          trend.map((point) => point.average_weight),
          palette.measured,
          { pointRadius: 3 },
        ),
      ],
      {
        unit: "kg",
        showLegend: false,
        tooltipAfterBody: (context) =>
          context.length
            ? [`測定数: ${trend[context[0].dataIndex].weight_measurements}回`]
            : [],
      },
    );
    buildBarChart(
      "exercise-chart",
      labels,
      trend.map((point) => point.exercise_minutes),
      {
        unit: "分",
        showLegend: false,
        tooltipAfterBody: (context) =>
          context.length
            ? [`記録日数: ${trend[context[0].dataIndex].recorded_exercise_days}/7日`]
            : [],
      },
    );
  }

  if (typeof darkMode.addEventListener === "function") {
    darkMode.addEventListener("change", () => window.location.reload());
  }
})();

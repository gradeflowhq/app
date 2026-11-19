// A bar chart of how many students fall in each band of totals, at the chosen bar width, in the theme's colours.

import { bounds, ticks } from "../lib/histogram.js";
import { counted } from "../lib/words.js";
import { loadScript } from "../services/vendor.js";

const color = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const barLabel = ([start, end]) => (start === end ? counted(start, "mark") : `${start}–${end} marks`);

/** Spans each bar across its band, less a gap; Chart.js would size bars on a linear axis from the space between them. */
const bandWidths = {
  id: "bandWidths",
  afterDatasetsUpdate(chart) {
    const scale = chart.scales.x;
    const bars = chart.getDatasetMeta(0).data;
    const spans = chart.data.datasets[0].data.map(({ start, end }) =>
      start === end
        ? [scale.getPixelForValue(start - 0.5), scale.getPixelForValue(end + 0.5)]
        : [scale.getPixelForValue(start), scale.getPixelForValue(end)],
    );
    const gap = 0.1 * Math.max(...spans.map(([left, right]) => right - left));
    spans.forEach(([left, right], i) => {
      bars[i].x = (left + right) / 2;
      bars[i].width = right - left - gap;
    });
  },
};

export const histogram = () => {
  let chart = null; // kept out of Alpine's state: Chart.js overflows the stack when it updates through a proxy
  return {
    barWidth: "auto",
    async init() {
      const canvas = this.$el.querySelector("canvas");
      const { max_marks: maxMarks, bars: barsByWidth } = JSON.parse(
        this.$el.querySelector("script[type='application/json']").textContent,
      );
      // An empty band has no bar rather than a hairline.
      const chartPoints = (width) =>
        barsByWidth[width].map(([start, end, students]) => ({ x: (start + end) / 2, y: students || null, start, end }));
      const [min, max] = bounds(barsByWidth[this.barWidth], maxMarks);
      await loadScript("chart-4.5.1.umd.min.js");
      if (!canvas.isConnected) return;
      Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
      Chart.defaults.font.size = 14;
      Chart.defaults.color = color("--color-muted");
      chart = new Chart(canvas, {
        type: "bar",
        data: {
          datasets: [
            {
              data: chartPoints(this.barWidth),
              backgroundColor: color("--color-chart"),
              hoverBackgroundColor: color("--color-primary"), // Chart.js can't shade oklch, and would show black
              borderRadius: 4,
            },
          ],
        },
        plugins: [bandWidths],
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              displayColors: false,
              callbacks: {
                title: ([item]) => barLabel(barsByWidth[this.barWidth][item.dataIndex]),
                label: (item) => counted(item.raw.y, "student"),
              },
            },
          },
          scales: {
            x: {
              type: "linear",
              min,
              max,
              offset: false,
              grid: { display: false },
              ticks: { maxRotation: 0, includeBounds: false },
              afterBuildTicks: (axis) => {
                axis.ticks = ticks(
                  axis.ticks.map((tick) => tick.value),
                  maxMarks,
                ).map((value) => ({ value }));
              },
            },
            y: {
              beginAtZero: true,
              ticks: { precision: 0, maxTicksLimit: 5 },
              grid: { color: color("--color-base-300") },
              border: { display: false },
              title: { display: true, text: "Students" },
            },
          },
        },
      });
      this.$watch("barWidth", (width) => {
        [chart.options.scales.x.min, chart.options.scales.x.max] = bounds(barsByWidth[width], maxMarks);
        chart.data.datasets[0].data = chartPoints(width);
        chart.update();
      });
    },
    destroy() {
      chart?.destroy();
    },
  };
};

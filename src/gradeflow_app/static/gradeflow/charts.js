// A bar chart of how many students fall in each band of totals, at the chosen bar width, in the theme's colours.

import { loadScript } from "./vendor.js";
import { counted } from "./words.js";

const color = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** The marks axis' bounds: 0 to the maximum marks, with half a mark to spare either side when each bar is one mark. */
export const bounds = ([[start, end]], maxMarks) => (start === end ? [-0.5, maxMarks + 0.5] : [0, maxMarks]);

/** The round ticks from 0, ending at the maximum marks in place of any round tick too close to it. */
export const ticks = (values, maxMarks) => {
  const step = values.length > 1 ? values[1] - values[0] : maxMarks;
  return [...values.filter((value) => value >= 0 && maxMarks - value >= step / 2), maxMarks];
};

const barLabel = ([start, end]) => (start === end ? counted(start, "mark") : `${start}–${end} marks`);

export const histogram = () => {
  let chart = null; // kept out of Alpine's state: Chart.js overflows the stack when it updates through a proxy
  return {
    barWidth: "auto",
    async init() {
      const canvas = this.$el.querySelector("canvas");
      const { max_marks: maxMarks, bars: barsByWidth } = JSON.parse(this.$el.querySelector("script[type='application/json']").textContent);
      // An empty band has no bar rather than a hairline.
      const chartPoints = (width) => barsByWidth[width].map(([start, end, students]) => ({ x: (start + end) / 2, y: students || null }));
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
              barThickness: "flex",
              categoryPercentage: 1,
              barPercentage: 0.9,
            },
          ],
        },
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
                axis.ticks = ticks(axis.ticks.map((tick) => tick.value), maxMarks).map((value) => ({ value }));
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

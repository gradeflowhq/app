// The Students grid: sorted, searched, filtered and paged in the browser by moving and hiding the rows the page has.
// Search waits for the IDs to decrypt. A row opens the student's drawer; a question's cell, the drawer at that question.

import { revealIn } from "../behaviours/student-ids.js";
import { matches, naturalOrder } from "../lib/words.js";

// A live refresh swaps the grid for a new one, which opens sorted, searched and paged as the old one was.
let savedState = null;

export const studentsGrid = () => {
  // Outside Alpine's reactive state. `rows` keeps the page's order; `ids` holds each row's decrypted ID in lowercase.
  let body, headings, rows, pageSize, rowsByFilter;
  let ids = new Map();

  return {
    isSearchable: false,
    query: "",
    filter: "all",
    sortColumn: -1,
    isDescending: false,
    page: 0,
    matchCount: 0,

    init() {
      const table = this.$root.querySelector("table");
      body = table.tBodies[0];
      headings = [...table.tHead.rows[0].cells];
      rows = [...body.rows];
      const rowsWith = (selector) => new Set(rows.filter((row) => row.querySelector(selector)));
      rowsByFilter = { adjusted: rowsWith("[data-adjusted]"), needs_marking: rowsWith('[data-state="needs_marking"]') };
      pageSize = Number(this.$root.dataset.pageSize);
      this.$root.addEventListener("htmx:config:request", (event) => {
        const cell = event.detail.ctx.sourceEvent?.target.closest("td");
        const question = cell && headings[cell.cellIndex].dataset.question;
        if (question) event.detail.ctx.request.body.set("question", question);
      });
      if (savedState?.path === location.pathname) Object.assign(this, savedState.state);
      // `?show=` opens the grid filtered, and follows the filter.
      const shown = new URLSearchParams(location.search).get("show");
      if (shown in rowsByFilter) this.filter = shown;
      const showFirstPage = () => {
        this.page = 0;
        this.render();
      };
      this.$watch("filter", (filter) => {
        const url = new URL(location.href);
        if (filter === "all") url.searchParams.delete("show");
        else url.searchParams.set("show", filter);
        history.replaceState(history.state, "", url);
        showFirstPage();
      });
      this.$watch("query", showFirstPage);
      this.render();
      revealIn(table).then(() => {
        ids = new Map(rows.map((row) => [row, row.cells[0].textContent.trim().toLowerCase()]));
        for (const id of body.querySelectorAll('[data-student-id-state="shown"]'))
          id.parentElement.title = id.textContent;
        this.isSearchable = !table.querySelector('[data-student-id-state="encrypted"]');
        if (this.query || this.sortColumn === 0) this.render();
      });
    },

    destroy() {
      const { query, filter, sortColumn, isDescending, page } = this;
      savedState = { path: location.pathname, state: { query, filter, sortColumn, isDescending, page } };
    },

    // A heading sorts up, then down when clicked again.
    sort(event) {
      const heading = event.target.closest("button")?.closest("th");
      if (!heading) return;
      this.isDescending = heading.cellIndex === this.sortColumn && !this.isDescending;
      this.sortColumn = heading.cellIndex;
      this.page = 0;
      this.render();
    },

    previousPage() {
      this.page -= 1;
      this.render();
    },

    nextPage() {
      this.page += 1;
      this.render();
    },

    get pageCount() {
      return Math.ceil(this.matchCount / pageSize);
    },

    get isFirstPage() {
      return this.page === 0;
    },

    get isLastPage() {
      return this.page >= this.pageCount - 1;
    },

    get shownRange() {
      const start = this.page * pageSize;
      return `${start + 1}–${Math.min(start + pageSize, this.matchCount)} of ${this.matchCount}`;
    },

    render() {
      const query = this.isSearchable ? this.query.trim() : "";
      const matching = rows.filter(
        (row) =>
          (this.filter === "all" || rowsByFilter[this.filter].has(row)) && (!query || matches(ids.get(row), query)),
      );
      const column = this.sortColumn;
      if (column >= 0) {
        const direction = this.isDescending ? -1 : 1;
        const key = (row) => (column === 0 ? (ids.get(row) ?? "") : Number(row.cells[column].dataset.order));
        const compare = column === 0 ? naturalOrder : (a, b) => a - b;
        matching.sort((a, b) => direction * compare(key(a), key(b)));
      }
      this.matchCount = matching.length;
      this.page = Math.min(this.page, Math.max(this.pageCount - 1, 0));
      const shown = new Set(matching.slice(this.page * pageSize, (this.page + 1) * pageSize));
      for (const row of rows) row.hidden = !shown.has(row);
      body.append(...matching);
      for (const heading of headings)
        heading.ariaSort = heading.cellIndex === column ? (this.isDescending ? "descending" : "ascending") : null;
    },
  };
};

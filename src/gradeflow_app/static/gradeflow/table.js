// Sorting and searching a table of students in the browser; searching only once their IDs are decrypted.

import { revealIn } from "./ids.js";
import { loadScript } from "./vendor.js";

// A live refresh swaps the table for a new one; the new one opens sorted, searched and paged as the old one was.
let savedTableState = null;

export const studentTable = () => ({
  table: null,
  async init() {
    const element = this.$root.querySelector("table");
    await Promise.all([revealIn(element), loadScript("simple-datatables-10.3.0/simple-datatables.js")]);
    if (!element.isConnected) return;
    this.table = new simpleDatatables.DataTable(element, {
      perPage: 50,
      perPageSelect: false,
      searchable: !element.querySelector('[data-student-id-state="encrypted"]'),
      fixedHeight: false,
      labels: { searchLabel: "", searchTitle: "Search by student ID", placeholder: "Search by student ID", noRows: "No students", noResults: "No students match", info: "{start}–{end} of {rows}" },
    });
    // The table renders its rows anew on every search, sort and page; htmx has to see the new rows.
    const processRows = () => htmx.process(this.table.wrapperDOM);
    for (const event of ["datatable.init", "datatable.update"]) this.table.on(event, processRows);
    this.restore();
    processRows();
  },
  restore() {
    if (savedTableState?.path !== location.pathname) return;
    const { sort, search, page } = savedTableState;
    if (sort) this.table.columns.sort(sort.column, sort.dir);
    const input = this.table.wrapperDOM.querySelector(".datatable-input");
    if (search && input) {
      input.value = search;
      this.table.search(search);
    }
    if (page > 1) this.table.page(page);
  },
  destroy() {
    if (this.table) {
      const search = this.table.wrapperDOM.querySelector(".datatable-input")?.value ?? "";
      savedTableState = { path: location.pathname, sort: this.table.columns._state.sort, search, page: this.table._currentPage };
    }
    this.table?.destroy();
  },
});

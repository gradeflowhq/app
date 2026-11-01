// Sorting and searching a table of students in the browser; searching only once their IDs are decrypted.

import { revealIn } from "./ids.js";
import { script } from "./vendor.js";

// A live refresh swaps the table for a new one; the new one opens sorted, searched and paged as the old one was.
let kept = null;

export const studentTable = () => ({
  table: null,
  async init() {
    await this.build();
    // The table keeps its own copy of the rows, so IDs shown later need a fresh one.
    this.unlocked = () => this.rebuild();
    document.addEventListener("gradeflow:unlocked", this.unlocked);
  },
  async rebuild() {
    this.table?.destroy();
    await this.build();
  },
  async build() {
    const element = this.$root.querySelector("table");
    await Promise.all([revealIn(element), script("simple-datatables-10.3.0/simple-datatables.js")]);
    if (!element.isConnected) return;
    this.table = new simpleDatatables.DataTable(element, {
      perPage: 50,
      perPageSelect: false,
      searchable: !element.querySelector('[data-sid-state="locked"]'),
      fixedHeight: false,
      labels: { searchLabel: "", searchTitle: "Search by student ID", placeholder: "Search by student ID", noRows: "No students", noResults: "No students match", info: "{start}–{end} of {rows}" },
    });
    // The table renders its rows anew on every search, sort and page; htmx has to see the new rows.
    const process = () => htmx.process(this.table.wrapperDOM);
    for (const event of ["datatable.init", "datatable.update"]) this.table.on(event, process);
    this.restore();
    process();
  },
  restore() {
    if (kept?.path !== location.pathname) return;
    const { sort, search, page } = kept;
    if (sort) this.table.columns.sort(sort.column, sort.dir);
    const input = this.table.wrapperDOM.querySelector(".datatable-input");
    if (search && input) {
      input.value = search;
      this.table.search(search);
    }
    if (page > 1) this.table.page(page);
  },
  destroy() {
    document.removeEventListener("gradeflow:unlocked", this.unlocked);
    if (this.table) {
      const search = this.table.wrapperDOM.querySelector(".datatable-input")?.value ?? "";
      kept = { path: location.pathname, sort: this.table.columns._state.sort, search, page: this.table._currentPage };
    }
    this.table?.destroy();
  },
});

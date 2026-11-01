// A list of choices in order, where one may repeat, such as what each value of an answer is: a select per item,
// numbered; Add copies the last and Remove drops one, keeping at least one.

const rows = (root) => [...root.querySelectorAll("[data-row]")];

function renumber(root) {
  rows(root).forEach((row, index) => {
    const name = `${root.dataset.item} ${index + 1}`;
    row.querySelector("[data-number]").textContent = name;
    row.querySelector("select").setAttribute("aria-label", name);
  });
  root.querySelector("select").dispatchEvent(new Event("change", { bubbles: true }));
}

export const orderedChoices = () => ({
  add() {
    const last = rows(this.$root).at(-1);
    last.after(last.cloneNode(true));
    renumber(this.$root);
  },
  remove(event) {
    // The root first: once its row is gone, the button no longer has one.
    const root = this.$root;
    if (rows(root).length > 1) event.target.closest("[data-row]").remove();
    renumber(root);
  },
});

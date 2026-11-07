// A table row with `data-href` follows its link when clicked anywhere.

export function start() {
  document.addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-href]");
    if (row && !event.target.closest("a")) row.querySelector("a").click();
  });
}

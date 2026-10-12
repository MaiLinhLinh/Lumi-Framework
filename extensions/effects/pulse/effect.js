/** Brief pulse that applies to the Runtime-validated presentation target. */
export function run({ target }) {
  target.classList.add("lumi-pulse");
  return () => target.classList.remove("lumi-pulse");
}

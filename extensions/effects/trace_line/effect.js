/** Trace a line already rendered inside a chart target, without inventing data. */
export function run({ target }) {
  const line = target.querySelector("polyline[data-anchor-id]");
  if (!line || typeof line.getTotalLength !== "function") {
    target.classList.add("lumi-trace-line-fallback");
    return () => target.classList.remove("lumi-trace-line-fallback");
  }
  const length = line.getTotalLength();
  const previousDasharray = line.style.strokeDasharray;
  const previousDashoffset = line.style.strokeDashoffset;
  const previousTransition = line.style.transition;
  line.style.strokeDasharray = `${length}`;
  line.style.strokeDashoffset = `${length}`;
  line.getBoundingClientRect();
  line.style.transition = "stroke-dashoffset 900ms cubic-bezier(.22, .8, .3, 1)";
  line.style.strokeDashoffset = "0";
  return () => {
    line.style.strokeDasharray = previousDasharray;
    line.style.strokeDashoffset = previousDashoffset;
    line.style.transition = previousTransition;
  };
}

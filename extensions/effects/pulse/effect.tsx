import { defineEffect, useLayoutEffect } from "../../../frontend/src/presentation/effect";
export const effect = defineEffect({ component: ({ context: { target } }) => {
  useLayoutEffect(() => {
    // Flush the previous instance's removed class before starting it again.
    target.getBoundingClientRect();
    target.classList.add("lumi-pulse");
    return () => target.classList.remove("lumi-pulse");
  }, [target]);
  return null;
} });

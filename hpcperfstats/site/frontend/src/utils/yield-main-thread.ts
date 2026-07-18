/**
 * Yield to the browser event loop so click handlers, navigation, and paint can
 * run before heavy synchronous work (e.g. Bokeh JSON clone + embed_item).
 */
export function yieldToMainThread(): Promise<void> {
  const sched = (
    globalThis as {
      scheduler?: { yield?: () => Promise<void> };
    }
  ).scheduler;
  if (typeof sched?.yield === "function") {
    return sched.yield();
  }
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(() => {
        setTimeout(resolve, 0);
      });
      return;
    }
    setTimeout(resolve, 0);
  });
}

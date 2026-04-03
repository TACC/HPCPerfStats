import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { useRouteFocusMain } from "./useRouteFocusMain";

describe("useRouteFocusMain", () => {
  beforeEach(() => {
    document.body.innerHTML = `
      <main id="main-content" tabindex="-1"></main>
    `;
  });

  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
  });

  it("does not focus on first pathname run", () => {
    const focusSpy = vi.spyOn(HTMLElement.prototype, "focus");
    renderHook(
      (p) => {
        useRouteFocusMain(p);
      },
      { initialProps: "/a" },
    );
    expect(focusSpy).not.toHaveBeenCalled();
  });

  it("focuses main when pathname changes and there is no h1", async () => {
    const main = document.getElementById("main-content");
    const focusSpy = vi.spyOn(main, "focus");
    const { rerender } = renderHook(
      (p) => {
        useRouteFocusMain(p);
      },
      { initialProps: "/first" },
    );
    rerender("/second");
    await waitFor(() => {
      expect(focusSpy).toHaveBeenCalled();
    });
  });

  it("focuses h1 when pathname changes", async () => {
    document.body.innerHTML = `
      <main id="main-content" tabindex="-1"><h1>Title</h1></main>
    `;
    const h1 = document.querySelector("h1");
    const focusSpy = vi.spyOn(h1, "focus");
    const { rerender } = renderHook(
      (p) => {
        useRouteFocusMain(p);
      },
      { initialProps: "/first" },
    );
    rerender("/second");
    await waitFor(() => {
      expect(focusSpy).toHaveBeenCalled();
    });
  });
});

import { describe, expect, it } from "vitest";
import { isPortaledOverlayTarget } from "./is-portaled-overlay-target";

describe("isPortaledOverlayTarget", () => {
  it("returns false for null", () => {
    expect(isPortaledOverlayTarget(null)).toBe(false);
  });

  it("returns false for a node outside any overlay", () => {
    const outside = document.createElement("div");
    document.body.appendChild(outside);
    expect(isPortaledOverlayTarget(outside)).toBe(false);
    outside.remove();
  });

  it("returns true for an element inside select-content", () => {
    const root = document.createElement("div");
    root.setAttribute("data-slot", "select-content");
    const option = document.createElement("div");
    option.setAttribute("role", "option");
    root.appendChild(option);
    document.body.appendChild(root);
    expect(isPortaledOverlayTarget(option)).toBe(true);
    root.remove();
  });

  it("returns true for a text node inside popover-content", () => {
    const root = document.createElement("div");
    root.setAttribute("data-slot", "popover-content");
    const text = document.createTextNode("help text");
    root.appendChild(text);
    document.body.appendChild(root);
    expect(isPortaledOverlayTarget(text)).toBe(true);
    root.remove();
  });

  it("returns true for dropdown-menu-content and sub-content", () => {
    for (const slot of ["dropdown-menu-content", "dropdown-menu-sub-content"]) {
      const root = document.createElement("div");
      root.setAttribute("data-slot", slot);
      const item = document.createElement("div");
      root.appendChild(item);
      document.body.appendChild(root);
      expect(isPortaledOverlayTarget(item)).toBe(true);
      root.remove();
    }
  });
});

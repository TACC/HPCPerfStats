import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useFocusTrap } from "./useFocusTrap";

describe("useFocusTrap", () => {
  it("does not preventDefault Tab when panel has no focusables", () => {
    const containerRef = { current: document.createElement("div") };
    renderHook(() => useFocusTrap(containerRef, true));

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      bubbles: true,
      cancelable: true,
    });
    const preventDefault = vi.spyOn(event, "preventDefault");
    document.dispatchEvent(event);

    expect(preventDefault).not.toHaveBeenCalled();
  });

  it("does not preventDefault Tab when focus is outside the panel", () => {
    const container = document.createElement("div");
    const button = document.createElement("button");
    button.textContent = "Inside";
    container.appendChild(button);
    document.body.appendChild(container);

    const outside = document.createElement("button");
    outside.textContent = "Outside";
    document.body.appendChild(outside);
    outside.focus();

    const containerRef = { current: container };
    renderHook(() => useFocusTrap(containerRef, true));

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      bubbles: true,
      cancelable: true,
    });
    const preventDefault = vi.spyOn(event, "preventDefault");
    document.dispatchEvent(event);

    expect(preventDefault).not.toHaveBeenCalled();
    container.remove();
    outside.remove();
  });

  it("removes trap listener when active becomes false", () => {
    const container = document.createElement("div");
    const button = document.createElement("button");
    button.textContent = "Inside";
    container.appendChild(button);
    document.body.appendChild(container);

    const containerRef = { current: container };
    const { rerender } = renderHook(
      ({ active }) => useFocusTrap(containerRef, active),
      { initialProps: { active: true } },
    );

    button.focus();
    rerender({ active: false });

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      bubbles: true,
      cancelable: true,
    });
    const preventDefault = vi.spyOn(event, "preventDefault");
    document.dispatchEvent(event);

    expect(preventDefault).not.toHaveBeenCalled();
    container.remove();
  });
});

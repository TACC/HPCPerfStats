import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useFocusTrap } from "./useFocusTrap";

describe("useFocusTrap", () => {
  it("registers keydown listener when active", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    const removeSpy = vi.spyOn(document, "removeEventListener");
    const ref = { current: document.createElement("div") };
    const b1 = document.createElement("button");
    ref.current.appendChild(b1);

    const { unmount } = renderHook(() => useFocusTrap(ref, true));

    expect(addSpy).toHaveBeenCalledWith("keydown", expect.any(Function), true);
    unmount();
    expect(removeSpy).toHaveBeenCalledWith("keydown", expect.any(Function), true);

    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it("does not trap Tab when container has no focusables", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    const ref = { current: document.createElement("div") };
    const preventDefault = vi.fn();

    renderHook(() => useFocusTrap(ref, true));

    const handler = addSpy.mock.calls.find(
      (call) => call[0] === "keydown" && call[2] === true,
    )?.[1] as EventListener | undefined;
    expect(handler).toBeDefined();

    handler?.({
      key: "Tab",
      preventDefault,
    } as unknown as KeyboardEvent);

    expect(preventDefault).not.toHaveBeenCalled();

    addSpy.mockRestore();
  });
});

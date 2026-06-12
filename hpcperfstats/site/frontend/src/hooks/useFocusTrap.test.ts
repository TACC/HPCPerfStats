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

  it("does not register when inactive", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    const ref = { current: document.createElement("div") };

    const { unmount } = renderHook(() => useFocusTrap(ref, false));

    expect(addSpy).not.toHaveBeenCalledWith("keydown", expect.any(Function), true);
    unmount();
    addSpy.mockRestore();
  });
});

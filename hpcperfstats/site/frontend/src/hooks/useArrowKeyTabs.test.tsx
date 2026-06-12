import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { useArrowKeyTabs } from "./useArrowKeyTabs";

function TabFixture({ initial = "tab-a" }) {
  const [active, setActive] = useState(initial);
  const ids = ["tab-a", "tab-b", "tab-c"];
  const handleKeyDown = useArrowKeyTabs(ids, active, setActive);

  return (
    <div role="tablist">
      {ids.map((id) => (
        <button
          key={id}
          type="button"
          id={id}
          role="tab"
          aria-selected={active === id}
          tabIndex={active === id ? 0 : -1}
          onClick={() => setActive(id)}
          onKeyDown={(e) => handleKeyDown(e, id)}
        >
          {id}
        </button>
      ))}
    </div>
  );
}

describe("useArrowKeyTabs", () => {
  it("moves selection with arrow keys and updates focus target", () => {
    render(<TabFixture />);
    const first = screen.getByRole("tab", { name: "tab-a" });
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "tab-b" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "tab-b" }), { key: "End" });
    expect(screen.getByRole("tab", { name: "tab-c" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "tab-c" }), { key: "Home" });
    expect(screen.getByRole("tab", { name: "tab-a" })).toHaveAttribute("aria-selected", "true");
  });
});

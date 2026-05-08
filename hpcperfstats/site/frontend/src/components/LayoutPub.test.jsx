import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import LayoutPub from "./LayoutPub.jsx";

describe("LayoutPub", () => {
  it("renders cluster name, branding, and login CTA to login_prompt with next=/machine/", () => {
    render(
      <MemoryRouter initialEntries={["/cluster-dashboard"]}>
        <LayoutPub machineName="cluster.test">
          <Routes>
            <Route path="cluster-dashboard" element={<div>page body</div>} />
          </Routes>
        </LayoutPub>
      </MemoryRouter>,
    );

    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByText("HPCPerfStats")).toBeInTheDocument();
    expect(screen.getByText("cluster.test")).toBeInTheDocument();
    expect(screen.getByText("page body")).toBeInTheDocument();

    const login = screen.getByRole("link", {
      name: /login to see individual job data/i,
    });
    expect(login).toHaveAttribute("href", "/login_prompt?next=%2Fmachine%2F");
  });
});

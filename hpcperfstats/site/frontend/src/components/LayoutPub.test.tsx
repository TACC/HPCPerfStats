import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LayoutPub from "./LayoutPub";

describe("LayoutPub", () => {
  it("renders cluster name, branding, and login CTA to login_prompt with next=/machine/", () => {
    render(
      <>
        <LayoutPub machineName="cluster.test">
          <div>page body</div>
        </LayoutPub>
      </>,
    );

    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByText("HPCPerfStats")).toBeInTheDocument();
    expect(screen.getByText("cluster.test")).toHaveClass("site-header-cluster");
    expect(screen.getByText("a job-level resource usage monitoring tool")).toHaveClass(
      "text-muted-foreground",
    );
    expect(screen.getByText("page body")).toBeInTheDocument();

    const login = screen.getByRole("link", {
      name: /login to see individual job data/i,
    });
    expect(login).toHaveAttribute("href", "/login_prompt?next=%2Fmachine%2F");
  });
});

import { render, screen } from "@testing-library/react";
import LoadingMessage from "./LoadingMessage";

describe("LoadingMessage", () => {
  it("renders the default message", () => {
    render(<LoadingMessage />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("renders a custom message", () => {
    render(<LoadingMessage message="Fetching data" />);
    expect(screen.getByText("Fetching data")).toBeInTheDocument();
  });
});


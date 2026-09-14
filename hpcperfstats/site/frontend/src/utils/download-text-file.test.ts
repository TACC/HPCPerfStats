import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadTextFile } from "./download-text-file";

describe("downloadTextFile", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates an object URL and clicks a download anchor", () => {
    const click = vi.fn();
    const createObjectURL = vi.fn(() => "blob:rmq-test");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    const createElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
      const el = createElement(tag);
      if (tag === "a") {
        Object.defineProperty(el, "click", { value: click });
      }
      return el;
    });

    downloadTextFile("rabbitmq_host_buckets.txt", "10_m_or_less = []\n");

    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:rmq-test");
  });
});

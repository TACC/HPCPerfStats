import "@testing-library/jest-dom/vitest";
import { toHaveNoViolations } from "jest-axe";
import { afterEach, beforeEach, expect, vi } from "vitest";
import { attachRouterMocks, resetNextNavigationMock } from "./test-utils/next-navigation-state";

vi.mock("next/navigation", async () => {
  const React = await import("react");
  const { nextNavigationMock, subscribeNextNavigation } = await import(
    "./test-utils/next-navigation-state"
  );
  return {
    useRouter: () => nextNavigationMock.router,
    usePathname: () =>
      React.useSyncExternalStore(
        subscribeNextNavigation,
        () => nextNavigationMock.pathname,
        () => nextNavigationMock.pathname,
      ),
    useSearchParams: () =>
      React.useSyncExternalStore(
        subscribeNextNavigation,
        () => nextNavigationMock.searchParams,
        () => nextNavigationMock.searchParams,
      ),
    useParams: () =>
      React.useSyncExternalStore(
        subscribeNextNavigation,
        () => nextNavigationMock.params,
        () => nextNavigationMock.params,
      ),
    redirect: vi.fn(),
  };
});

beforeEach(() => {
  attachRouterMocks();
});

afterEach(() => {
  resetNextNavigationMock();
});

expect.extend(toHaveNoViolations as unknown as Parameters<typeof expect.extend>[0]);

// jsdom: scrollIntoView is missing or throws; JobList uses it after tab switches.
HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};

// jsdom does not implement canvas; some test paths touch getContext.
Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  value: function getContext() {
    return {
      canvas: this,
      measureText: () => ({ width: 0 }),
      clearRect: () => {},
      fillRect: () => {},
      beginPath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      stroke: () => {},
      save: () => {},
      restore: () => {},
      setTransform: () => {},
      scale: () => {},
      translate: () => {},
      arc: () => {},
      fill: () => {},
      closePath: () => {},
      createLinearGradient: () => ({ addColorStop: () => {} }),
      createPattern: () => null,
      createRadialGradient: () => ({ addColorStop: () => {} }),
      getImageData: () => ({ data: new Uint8ClampedArray(0) }),
      putImageData: () => {},
      drawImage: () => {},
    };
  },
});


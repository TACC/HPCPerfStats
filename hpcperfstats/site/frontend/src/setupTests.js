import "@testing-library/jest-dom/vitest";
import { toHaveNoViolations } from "jest-axe";
import { expect } from "vitest";

expect.extend(toHaveNoViolations);

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


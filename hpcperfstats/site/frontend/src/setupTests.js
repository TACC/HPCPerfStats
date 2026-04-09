import "@testing-library/jest-dom/vitest";
import { toHaveNoViolations } from "jest-axe";
import { expect } from "vitest";

expect.extend(toHaveNoViolations);

// jsdom: scrollIntoView is missing or throws; JobList uses it after tab switches.
HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};


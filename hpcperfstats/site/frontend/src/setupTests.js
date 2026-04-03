import "@testing-library/jest-dom/vitest";

// jsdom: scrollIntoView is missing or throws; JobList uses it after tab switches.
HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};


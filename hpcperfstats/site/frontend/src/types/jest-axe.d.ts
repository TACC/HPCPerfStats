declare module "jest-axe" {
  export function axe(
    element: Element | Document,
    options?: Record<string, unknown>,
  ): Promise<{ violations: unknown[] }>;
  export function toHaveNoViolations(results: { violations: unknown[] }): {
    message: () => string;
    pass: boolean;
  };
}

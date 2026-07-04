import { useSyncExternalStore, type ReactNode } from "react";
import { nextNavigationMock, subscribeNextNavigation } from "./next-navigation-state";

function navigationSnapshot(): string {
  return `${nextNavigationMock.pathname}?${nextNavigationMock.searchParams.toString()}`;
}

/** Re-render children when tests call mocked router.push/replace (Next has no MemoryRouter). */
export function WithNavigationSync({ children }: { children: ReactNode }) {
  useSyncExternalStore(subscribeNextNavigation, navigationSnapshot);
  return children;
}

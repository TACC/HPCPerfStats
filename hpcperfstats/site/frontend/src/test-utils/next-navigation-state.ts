import { vi } from "vitest";

type RouteParams = Record<string, string | string[] | undefined>;
type NavigationListener = () => void;

const navigationListeners = new Set<NavigationListener>();

export function subscribeNextNavigation(listener: NavigationListener): () => void {
  navigationListeners.add(listener);
  return () => navigationListeners.delete(listener);
}

function notifyNextNavigation(): void {
  for (const listener of navigationListeners) listener();
}

function applyNavigationHref(href: string): void {
  const url = new URL(href, "http://hpcperfstats.test");
  nextNavigationMock.pathname = url.pathname;
  nextNavigationMock.searchParams = new URLSearchParams(url.searchParams);
  notifyNextNavigation();
}

export function attachRouterMocks(): void {
  nextNavigationMock.router.push.mockImplementation((href: string) => {
    applyNavigationHref(href);
  });
  nextNavigationMock.router.replace.mockImplementation((href: string) => {
    applyNavigationHref(href);
  });
}

export const nextNavigationMock = {
  pathname: "/machine/jobs/",
  searchParams: new URLSearchParams(),
  params: {} as RouteParams,
  router: {
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  },
};

attachRouterMocks();

export function resetNextNavigationMock(
  overrides: {
    pathname?: string;
    searchParams?: URLSearchParams;
    params?: RouteParams;
  } = {},
): void {
  nextNavigationMock.pathname = overrides.pathname ?? "/machine/jobs/";
  nextNavigationMock.searchParams = overrides.searchParams ?? new URLSearchParams();
  nextNavigationMock.params = overrides.params ?? {};
  nextNavigationMock.router.push.mockClear();
  nextNavigationMock.router.replace.mockClear();
  attachRouterMocks();
}

/** Map legacy react-router-style paths used in tests onto Next navigation mock state. */
export function configureNextNavigationFromPath(path: string): void {
  const url = new URL(path, "http://hpcperfstats.test");
  let pathname = url.pathname;
  if (!pathname.startsWith("/machine") && !pathname.startsWith("/pub")) {
    pathname = pathname.endsWith("/") ? `/machine${pathname}` : `/machine${pathname}/`;
  }

  const params: RouteParams = {};
  const hostMatch = pathname.match(/\/host\/([^/]+)\/plot/);
  if (hostMatch) params.host = decodeURIComponent(hostMatch[1]);
  const yearMatch = pathname.match(/\/year\/([^/]+)/);
  if (yearMatch) params.year = yearMatch[1];
  const typeMatch = pathname.match(/\/job\/([^/]+)\/([^/]+)/);
  if (typeMatch) {
    params.jid = typeMatch[1];
    params.typeName = typeMatch[2];
  } else {
    const jobMatch = pathname.match(/\/job\/([^/]+)/);
    if (jobMatch) params.pk = jobMatch[1];
  }

  resetNextNavigationMock({
    pathname,
    searchParams: url.searchParams,
    params,
  });
  notifyNextNavigation();
}

export function lastRouterPushUrl(base = "http://hpcperfstats.test"): URL {
  const calls = nextNavigationMock.router.push.mock.calls;
  if (!calls.length) {
    throw new Error("router.push was not called");
  }
  return new URL(String(calls[calls.length - 1][0]), base);
}

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import type { SessionInfo } from "@/api/generated/models/sessionInfo";
import { SessionContext } from "@/session-context";
import { configureNextNavigationFromPath } from "./next-navigation-state";
import { WithNavigationSync } from "./with-navigation-sync";

export type SessionFixture = Partial<SessionInfo>;

export type RenderWithProvidersOptions = {
  session?: SessionFixture | null;
  initialPath?: string;
  withNavigationSync?: boolean;
  queryClient?: QueryClient;
};

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

function ProvidersWrapper({
  children,
  client,
  session,
  withNavigationSync,
}: {
  children: ReactNode;
  client: QueryClient;
  session: SessionFixture | null | undefined;
  withNavigationSync: boolean;
}) {
  let tree = children;
  if (withNavigationSync) {
    tree = <WithNavigationSync>{tree}</WithNavigationSync>;
  }
  if (session !== null && session !== undefined) {
    tree = (
      <SessionContext.Provider value={session as SessionInfo}>{tree}</SessionContext.Provider>
    );
  }
  return <QueryClientProvider client={client}>{tree}</QueryClientProvider>;
}

export function renderWithProviders(
  ui: ReactElement,
  {
    session,
    initialPath,
    withNavigationSync = false,
    queryClient,
  }: RenderWithProvidersOptions = {},
  options?: Omit<RenderOptions, "wrapper">,
) {
  if (initialPath) {
    configureNextNavigationFromPath(initialPath);
  }
  const client = queryClient ?? createTestQueryClient();
  return render(ui, {
    wrapper: ({ children }) => (
      <ProvidersWrapper
        client={client}
        session={session}
        withNavigationSync={withNavigationSync}
      >
        {children}
      </ProvidersWrapper>
    ),
    ...options,
  });
}

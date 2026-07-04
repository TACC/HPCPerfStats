import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { getUserApiKeyRetrieveQueryKey } from "@/api/generated/session/session";
import { orvalOkEnvelope } from "@/api/orval-response";
import { useUserApiKey } from "./use-user-api-key";

vi.mock("@/api/generated/session/session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/generated/session/session")>();
  return {
    ...actual,
    useUserApiKeyRetrieve: vi.fn(() => ({
      data: { username: "alice", raw_key: "secret-key", key_prefix: "secret" },
      error: null,
      isLoading: false,
      refetch: vi.fn(),
    })),
    useUserApiKeyRotateCreate: vi.fn(() => ({
      mutateAsync: vi.fn(),
      isPending: false,
      error: null,
    })),
  };
});

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("useUserApiKey", () => {
  it("clearRawKeyFromCache strips raw_key while keeping prefix", () => {
    const client = new QueryClient();
    client.setQueryData(
      getUserApiKeyRetrieveQueryKey(),
      orvalOkEnvelope({
        username: "alice",
        raw_key: "secret-key",
        key_prefix: "secret",
      }),
    );

    const { result } = renderHook(() => useUserApiKey(), {
      wrapper: wrapper(client),
    });

    act(() => {
      result.current.clearRawKeyFromCache();
    });

    const cached = client.getQueryData(getUserApiKeyRetrieveQueryKey()) as {
      data: { raw_key: string | null; key_prefix: string };
    };
    expect(cached.data.raw_key).toBeNull();
    expect(cached.data.key_prefix).toBe("secret");
  });
});

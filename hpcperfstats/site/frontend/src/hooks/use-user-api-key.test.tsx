import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import {
  getUserApiKeyRetrieveQueryKey,
  useUserApiKeyRetrieve,
  useUserApiKeyRotateCreate,
} from "@/api/generated/session/session";
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

  it("keeps the rotated raw key visible after the GET returns raw_key=null", async () => {
    let retrieveData: Record<string, unknown> = {
      username: "alice",
      raw_key: null,
      key_prefix: "oldprefix",
    };
    vi.mocked(useUserApiKeyRetrieve).mockImplementation(
      () =>
        ({
          data: retrieveData,
          error: null,
          isLoading: false,
          refetch: vi.fn(),
        }) as unknown as ReturnType<typeof useUserApiKeyRetrieve>,
    );
    let onSuccess: ((r: unknown) => void) | undefined;
    vi.mocked(useUserApiKeyRotateCreate).mockImplementation(((opts: {
      mutation?: { onSuccess?: (r: unknown) => void };
    }) => {
      onSuccess = opts?.mutation?.onSuccess;
      return { mutateAsync: vi.fn(), isPending: false, error: null };
    }) as unknown as typeof useUserApiKeyRotateCreate);

    const client = new QueryClient();
    const { result, rerender } = renderHook(() => useUserApiKey(), {
      wrapper: wrapper(client),
    });
    expect(result.current.data?.raw_key).toBeNull();

    act(() => {
      onSuccess?.(
        orvalOkEnvelope({ username: "alice", raw_key: "brand-new-key", key_prefix: "brandnew" }),
      );
    });
    expect(result.current.data?.raw_key).toBe("brand-new-key");

    // Simulate a refetch (focus / explicit): server no longer returns raw_key.
    retrieveData = { username: "alice", raw_key: null, key_prefix: "brandnew" };
    rerender();
    expect(result.current.data?.raw_key).toBe("brand-new-key");
    expect(result.current.data?.key_prefix).toBe("brandnew");

    act(() => {
      result.current.clearRawKeyFromCache();
    });
    expect(result.current.data?.raw_key).toBeNull();
  });

  it("keeps a first-mint raw key from GET after a later GET returns null", () => {
    let retrieveData: Record<string, unknown> = {
      username: "alice",
      raw_key: "first-key",
      key_prefix: "firstkey",
    };
    vi.mocked(useUserApiKeyRetrieve).mockImplementation(
      () =>
        ({
          data: retrieveData,
          error: null,
          isLoading: false,
          refetch: vi.fn(),
        }) as unknown as ReturnType<typeof useUserApiKeyRetrieve>,
    );
    const client = new QueryClient();
    const { result, rerender } = renderHook(() => useUserApiKey(), {
      wrapper: wrapper(client),
    });
    expect(result.current.data?.raw_key).toBe("first-key");
    retrieveData = { username: "alice", raw_key: null, key_prefix: "firstkey" };
    rerender();
    expect(result.current.data?.raw_key).toBe("first-key");
  });
});

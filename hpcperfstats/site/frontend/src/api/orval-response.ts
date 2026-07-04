/** Orval 8 fetch mutator wraps JSON as `{ status, data, headers }`. */

export function orvalOkEnvelope<T>(data: T) {
  return { status: 200 as const, data, headers: new Headers() };
}

export function isOrvalFetchEnvelope(
  value: unknown,
): value is { status: number; data: unknown; headers?: Headers } {
  return (
    value !== null &&
    typeof value === "object" &&
    "status" in value &&
    "data" in value &&
    typeof (value as { status: unknown }).status === "number"
  );
}

export function orvalResponseData<T>(
  response: { status: number; data: unknown } | undefined | null,
): T | undefined {
  if (!response || response.status !== 200) return undefined;
  return response.data as T;
}

/** React Query `select`: unwrap a 200 Orval envelope to its body. */
export function selectOrvalData<T extends { status: number; data: unknown }>(
  response: T,
): Extract<T, { status: 200 }> extends { data: infer D } ? D : never {
  if (response.status !== 200) {
    return undefined as Extract<T, { status: 200 }> extends { data: infer D } ? D : never;
  }
  return (response as Extract<T, { status: 200 }>).data as Extract<
    T,
    { status: 200 }
  > extends { data: infer D }
    ? D
    : never;
}

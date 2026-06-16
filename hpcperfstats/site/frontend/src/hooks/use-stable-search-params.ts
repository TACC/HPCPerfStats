import { useMemo } from "react";
import { useSearchParams } from "next/navigation";

/** Stable query-string key for memo/effect deps (Next `searchParams` object identity changes every render). */
export function useStableSearchParamsKey(): string {
  const searchParams = useSearchParams();
  return searchParams.toString();
}

/** Clone of current query params that only changes when the serialized query string changes. */
export function useStableURLSearchParams(): URLSearchParams {
  const searchParamsKey = useStableSearchParamsKey();
  return useMemo(() => new URLSearchParams(searchParamsKey), [searchParamsKey]);
}

import { useCallback, useState } from "react";

export function useAsyncFetch<T, Args extends unknown[] = unknown[]>(
  fetcher: (...args: Args) => Promise<T>,
  initialValue: T | null = null,
) {
  const [data, setData] = useState<T | null>(initialValue);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const run = useCallback(
    async (...args: Args) => {
      setLoading(true);
      setError(null);
      try {
        const nextData = await fetcher(...args);
        setData(nextData);
        return nextData;
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Request failed";
        setError(message);
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [fetcher],
  );

  return { data, error, loading, setData, setError, run };
}

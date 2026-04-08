import { useCallback, useState } from "react";

export function useAsyncFetch(fetcher, initialValue = null) {
  const [data, setData] = useState(initialValue);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const run = useCallback(
    async (...args) => {
      setLoading(true);
      setError(null);
      try {
        const nextData = await fetcher(...args);
        setData(nextData);
        return nextData;
      } catch (err) {
        setError(err?.message || "Request failed");
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [fetcher],
  );

  return { data, error, loading, setData, setError, run };
}

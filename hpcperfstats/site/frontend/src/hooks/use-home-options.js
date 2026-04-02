import { useEffect, useState } from "react";
import { api } from "../api";

/** Loads `/home/` JSON for search UIs (year/date lists, metrics, queues, states). */
export function useHomeOptions() {
  const [options, setOptions] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .getHomeOptions()
      .then(setOptions)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return { options, error, loading };
}

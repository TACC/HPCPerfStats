import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import BokehEmbed from "../components/BokehEmbed";

export default function HostDetail() {
  const { host } = useParams();
  const [searchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!host) return;
    let end_time__gte = searchParams.get("end_time__gte") || "";
    const end_time__lte = searchParams.get("end_time__lte") || "now()";
    if (!end_time__gte) {
      const d = new Date();
      d.setDate(d.getDate() - 1);
      end_time__gte = d.toISOString().slice(0, 19);
    }
    setLoading(true);
    api
      .getHostPlot({ host, end_time__gte, end_time__lte })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [host, searchParams]);

  if (loading) return <div className="container">Loading…</div>;
  if (error) return <div className="container text-danger">Error: {error}</div>;
  if (!data) return null;

  const { host: hostName, plot_item } = data;

  return (
    <div className="container-fluid">
      <h2>Host: {hostName}</h2>
      <p className="text-muted">
        Time range: {data.end_time__gte} — {data.end_time__lte}
      </p>
      {plot_item ? (
        <div className="graphs">
          <BokehEmbed item={plot_item} id="host-bokeh" plotName="Host plot" />
        </div>
      ) : (
        <p>No plot data for this host and time range.</p>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehEmbed from "../components/BokehEmbed";
import LoadingMessage from "../components/LoadingMessage";
import { formatDateTime } from "../utils/formatDateTime";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function HostDetail() {
  const { host } = useParams();
  const [searchParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useDocumentTitle(host ? `Host ${host}` : "Host plot");

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

  if (loading) return <LoadingMessage message="Loading host plot…" />;
  if (error) return <BannerErrorMessage message={error} />;
  if (!data) return null;

  const { host: hostName, plot_item, plot_unavailable_reason } = data;

  return (
    <div className="container-fluid">
      <h1 className="h2">Host: {hostName}</h1>
      <p className="text-muted">
        Time range: {formatDateTime(data.end_time__gte)} — {data.end_time__lte === "now()" ? "Now" : formatDateTime(data.end_time__lte)}
      </p>
      <div className="graphs">
        <BokehEmbed
          item={plot_item}
          id="host-bokeh"
          plotName="Host plot"
          unavailableReason={plot_unavailable_reason}
        />
      </div>
    </div>
  );
}

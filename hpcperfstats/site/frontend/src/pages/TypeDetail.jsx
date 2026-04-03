import { useEffect, useState } from "react";
import { formatDecimalStandard } from "../utils/formatDecimal";
import { useParams } from "react-router-dom";
import { api } from "../api";
import BannerErrorMessage from "../components/BannerErrorMessage";
import BokehEmbed from "../components/BokehEmbed";
import LoadingMessage from "../components/LoadingMessage";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function TypeDetail() {
  const { jid, typeName } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useDocumentTitle(
    jid && typeName ? `Job ${jid} / ${typeName}` : "Type detail",
  );

  useEffect(() => {
    if (!jid || !typeName) return;
    api
      .getTypeDetail(jid, typeName)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [jid, typeName]);

  if (loading) return <LoadingMessage message="Loading type detail…" />;
  if (error) return <BannerErrorMessage message={error} />;
  if (!data) return null;

  const {
    type_name,
    jobid,
    tscript,
    tdiv,
    tplot_item,
    tplot_unavailable_reason,
    stats_data = [],
    schema = [],
  } = data;

  return (
    <div className="container-fluid">
      <h1 className="h2">
        Job {jobid} / Type {type_name}
      </h1>
      <h2 className="h5">Rates Aggregated over devices</h2>
      <div className="graphs">
        <BokehEmbed
          item={tplot_item}
          script={tscript}
          div={tdiv}
          id="type-bokeh"
          plotName="Type detail"
          unavailableReason={tplot_unavailable_reason}
        />
      </div>
      {stats_data.length > 0 && (
        <>
          <h2 className="h5">Counts Aggregated over devices and hosts</h2>
          <div className="table-responsive">
            <table className="table table-sm table-bordered">
              <thead>
                <tr>
                  <th>record</th>
                  {schema.map((key) => (
                    <th key={key}>{key}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stats_data.map(([time, values], i) => (
                  <tr key={i}>
                    <th>{time}</th>
                    {values.map((v, j) => (
                      <th key={j}>
                        {typeof v === "number" ? formatDecimalStandard(v) : v}
                      </th>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import BokehEmbed from "../components/BokehEmbed";

export default function TypeDetail() {
  const { jid, typeName } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!jid || !typeName) return;
    api
      .getTypeDetail(jid, typeName)
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [jid, typeName]);

  if (loading) return <div className="container">Loading…</div>;
  if (error) return <div className="container text-danger">Error: {error}</div>;
  if (!data) return null;

  const { type_name, jobid, tscript, tdiv, stats_data = [], schema = [] } = data;

  return (
    <div className="container-fluid">
      <h2>Job {jobid} / Type {type_name}</h2>
      <h4>Rates Aggregated over devices</h4>
      <div className="graphs">
        <BokehEmbed script={tscript} div={tdiv} id="type-bokeh" />
      </div>
      {stats_data.length > 0 && (
        <>
          <h4>Counts Aggregated over devices and hosts</h4>
          <div className="table-responsive">
            <table className="table table-condensed table-bordered">
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
                        {typeof v === "number" ? v.toExponential(2) : v}
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

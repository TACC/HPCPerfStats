import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import LoadingMessage from "./LoadingMessage";

const ALLOWED_PARAMS = [
  "jid",
  "host",
  "username",
  "account__icontains",
  "state",
  "queue",
  "end_time__gte",
  "end_time__lte",
  "runtime__gte",
  "runtime__lte",
  "nhosts__gte",
  "nhosts__lte",
  "node_hrs__gte",
  "node_hrs__lte",
];

export default function ExtendedSearch({ onClose }) {
  const navigate = useNavigate();
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

  const handleSubmit = (e) => {
    e.preventDefault();
    const form = e.target;
    const params = {};
    for (const el of form.elements) {
      if (!el.name || !el.value) continue;
      if (el.name.startsWith("metrics_")) {
        params[el.name] = el.value;
        continue;
      }
      if (ALLOWED_PARAMS.includes(el.name)) params[el.name] = el.value;
    }
    if (params.jid) {
      navigate(`/job/${params.jid}`);
      return;
    }
    if (params.host && params.end_time__gte) {
      const qs = new URLSearchParams({
        end_time__gte: params.end_time__gte,
        end_time__lte: params.end_time__lte || "now()",
      }).toString();
      navigate(`/host/${encodeURIComponent(params.host)}/plot?${qs}`);
      return;
    }
    const qs = new URLSearchParams(params).toString();
    navigate(`/jobs?${qs}`);
  };

  if (loading) return <LoadingMessage message="Loading search options…" />;
  if (error) return <div className="text-danger" style={{ padding: "0.5rem 0" }}>Error: {error}</div>;

  const { metrics = [], queues = [], states = [] } = options || {};

  return (
    <div className="extended-search-panel">
      {onClose && (
        <div className="extended-search-header">
          <span className="extended-search-title">Extended search</span>
          <button
            type="button"
            className="btn btn-sm btn-default"
            onClick={onClose}
            aria-label="Close extended search"
          >
            Close
          </button>
        </div>
      )}
      <form id="extended-search-form" onSubmit={handleSubmit}>
        <p className="text-muted small">Search fields are combined.</p>
        <div className="row">
          <div className="col-md-1">
            <label>Start Date</label>
          </div>
          <div className="col-md-2">
            <input
              type="date"
              className="form-control input-sm"
              name="end_time__gte"
            />
          </div>
          <div className="col-md-1">
            <label>End Date</label>
          </div>
          <div className="col-md-2">
            <input
              type="date"
              className="form-control input-sm"
              name="end_time__lte"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label htmlFor="ext-host">Host</label>
          </div>
          <div className="col-md-2">
            <input
              type="text"
              className="form-control input-sm"
              name="host"
              id="ext-host"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label htmlFor="ext-username">Username</label>
          </div>
          <div className="col-md-2">
            <input
              type="text"
              className="form-control input-sm"
              name="username"
              id="ext-username"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label htmlFor="ext-account">Account</label>
          </div>
          <div className="col-md-2">
            <input
              type="text"
              className="form-control input-sm"
              name="account__icontains"
              id="ext-account"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label htmlFor="ext-state">State</label>
          </div>
          <div className="col-md-2">
            <select className="form-control" id="ext-state" name="state">
              <option value="">--</option>
              {states.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label htmlFor="ext-queue">Queue</label>
          </div>
          <div className="col-md-2">
            <select className="form-control" id="ext-queue" name="queue">
              <option value="">--</option>
              {queues.map((q) => (
                <option key={q}>{q}</option>
              ))}
            </select>
          </div>
        </div>
        <h5>Search on Resources</h5>
        <div className="row">
          <div className="col-md-2">
            <label>Runtime</label>
          </div>
          <div className="col-md-1">
            <input
              type="text"
              className="form-control input-sm"
              name="runtime__gte"
              placeholder="min seconds"
            />
          </div>
          <div className="col-md-1">
            <input
              type="text"
              className="form-control input-sm"
              name="runtime__lte"
              placeholder="max seconds"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label>Nodes</label>
          </div>
          <div className="col-md-1">
            <input
              type="text"
              className="form-control input-sm"
              name="nhosts__gte"
              placeholder="min nodes"
            />
          </div>
          <div className="col-md-1">
            <input
              type="text"
              className="form-control input-sm"
              name="nhosts__lte"
              placeholder="max nodes"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label>Node-hrs</label>
          </div>
          <div className="col-md-1">
            <input
              type="text"
              className="form-control input-sm"
              name="node_hrs__gte"
              placeholder="min nodes-hrs"
            />
          </div>
          <div className="col-md-1">
            <input
              type="text"
              className="form-control input-sm"
              name="node_hrs__lte"
              placeholder="max node-hrs"
            />
          </div>
        </div>
        <h5>Search on Derived Metrics</h5>
        {metrics.map((m) => (
          <div className="row" key={m.metric}>
            <div className="col-md-2">
              <label>{m.metric}</label>
            </div>
            <div className="col-md-1">
              <input
                type="text"
                className="form-control input-sm"
                name={`metrics_${m.metric}__gte`}
                placeholder={`Min ${m.units}`}
              />
            </div>
            <div className="col-md-1">
              <input
                type="text"
                className="form-control input-sm"
                name={`metrics_${m.metric}__lte`}
                placeholder={`Max ${m.units}`}
              />
            </div>
          </div>
        ))}
        <button type="submit" className="btn btn-default">
          Search
        </button>
      </form>
    </div>
  );
}

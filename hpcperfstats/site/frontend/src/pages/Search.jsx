import { useEffect, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { api } from "../api";

export default function Search() {
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
    const allowed = [
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
    for (const el of form.elements) {
      if (!el.name || !el.value) continue;
      if (el.name.startsWith("metrics_")) {
        params[el.name] = el.value;
        continue;
      }
      if (allowed.includes(el.name)) params[el.name] = el.value;
    }
    if (params.jid) {
      navigate(`/job/${params.jid}`);
      return;
    }
    const qs = new URLSearchParams(params).toString();
    navigate(`/jobs?${qs}`);
  };

  if (loading) return <div className="container">Loading…</div>;
  if (error) return <div className="container text-danger">Error: {error}</div>;

  const { date_list = [], metrics = [], queues = [], states = [] } = options || {};

  return (
    <div className="row">
      <div className="col-md-offset-3 col-md-4">
        <center>
          <font size="8" color="#bf0a30">
            HPCPerfStats
          </font>
          <p>a job-level resource usage monitoring tool</p>
        </center>
      </div>

      {error && <p style={{ color: "red" }}>Requested search failed.</p>}
      <hr />

      <h4>Search fields are combined</h4>
      <form id="search" onSubmit={handleSubmit}>
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
            <label htmlFor="host">Host</label>
          </div>
          <div className="col-md-2">
            <input
              type="text"
              className="form-control input-sm"
              name="host"
              id="host"
            />
          </div>
        </div>

        <div className="row">
          <div className="col-md-2">
            <label htmlFor="username">Username</label>
          </div>
          <div className="col-md-2">
            <input
              type="text"
              className="form-control input-sm"
              name="username"
              id="username"
            />
          </div>
        </div>
        <div className="row">
          <div className="col-md-2">
            <label htmlFor="account">Account</label>
          </div>
          <div className="col-md-2">
            <input
              type="text"
              className="form-control input-sm"
              name="account__icontains"
              id="account"
            />
          </div>
        </div>

        <div className="row">
          <div className="col-md-2">
            <label htmlFor="state">State</label>
          </div>
          <div className="col-md-2">
            <select className="form-control" id="state" name="state">
              <option value="">--</option>
              {states.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="row">
          <div className="col-md-2">
            <label htmlFor="queue">Queue</label>
          </div>
          <div className="col-md-2">
            <select className="form-control" id="queue" name="queue">
              <option value="">--</option>
              {queues.map((q) => (
                <option key={q}>{q}</option>
              ))}
            </select>
          </div>
        </div>

        <h4>Search on Resources</h4>
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

        <h4>Search on Derived Metrics</h4>
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

      <hr />
      <div className="container-fluid">
        <h4>List all jobs for a given date</h4>
        {date_list.length > 0 ? (
          <nav className="navbar navbar-default">
            {date_list.map(([month, dates]) => (
              <ul className="pagination" key={month}>
                <li>
                  <Link to={`/date/${month}`}>{month}</Link>
                </li>
                {dates.map(([dateStr, day]) => (
                  <li key={dateStr}>
                    <Link to={`/date/${dateStr}`}>{day}</Link>
                  </li>
                ))}
              </ul>
            ))}
          </nav>
        ) : (
          <p>No job data available</p>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import LoadingMessage from "../components/LoadingMessage";

export default function Search() {
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

  if (loading) return <LoadingMessage message="Loading…" />;
  if (error) return <div className="container text-danger">Error: {error}</div>;

  const { date_list = [] } = options || {};

  return (
    <div className="row">
      <div className="col-md-offset-3 col-md-4">
        <center>
          <font size="8" color="#bf0a30">
            HPCPerfStats
          </font>
          <p>a job-level resource usage monitoring tool</p>
          <p className="text-muted small">
            Use <strong>Extended search</strong> in the top bar for date, host, queue, metrics, and more.
          </p>
        </center>
      </div>

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

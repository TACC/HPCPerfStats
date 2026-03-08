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

  const { year_list = [], date_list = [] } = options || {};

  return (
    <div className="row">
      <hr />
      <div className="container-fluid">
        <h4>List all jobs for a given year</h4>
        {year_list.length > 0 ? (
          <nav aria-label="Year list" className="mb-4">
            <ul className="pagination pagination-sm flex-wrap">
              {year_list.map((year) => (
                <li className="page-item" key={year}>
                  <Link className="page-link" to={`/year/${year}`}>{year}</Link>
                </li>
              ))}
            </ul>
          </nav>
        ) : (
          <p className="text-muted mb-4">No job data available.</p>
        )}
        <h4>List all jobs for a given date</h4>
        {date_list.length > 0 ? (
          <nav aria-label="Date list">
            {date_list.map(([month, dates]) => (
              <ul className="pagination" key={month}>
                <li className="page-item">
                  <Link className="page-link" to={`/date/${month}`}>{month}</Link>
                </li>
                {dates.map(([dateStr, day]) => (
                  <li className="page-item" key={dateStr}>
                    <Link className="page-link" to={`/date/${dateStr}`}>{day}</Link>
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

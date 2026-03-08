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
      <div className="container-fluid search-home">
        <section className="search-home-section">
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
        </section>

        <section className="search-home-section search-date-list-section">
          <h4>List all jobs for a given date</h4>
          {date_list.length > 0 ? (
            <details className="search-date-list-details" open>
              <summary className="search-date-list-summary">
                Show dates by month
              </summary>
              <nav aria-label="Date list" className="search-date-list-nav">
                {date_list.map(([month, dates]) => (
                  <div className="search-date-list-month" key={month}>
                    <ul className="pagination pagination-sm flex-wrap">
                      <li className="page-item">
                        <Link className="page-link" to={`/date/${month}`}>{month}</Link>
                      </li>
                      {dates.map(([dateStr, day]) => (
                        <li className="page-item" key={dateStr}>
                          <Link className="page-link" to={`/date/${dateStr}`}>{day}</Link>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </nav>
            </details>
          ) : (
            <p className="text-muted">No job data available</p>
          )}
        </section>
      </div>
    </div>
  );
}

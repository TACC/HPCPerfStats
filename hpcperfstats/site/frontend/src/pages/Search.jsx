import { Link, useNavigate } from "react-router-dom";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { useHomeOptions } from "../hooks/use-home-options";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function Search() {
  const navigate = useNavigate();
  const { options, error, loading } = useHomeOptions();

  useDocumentTitle(loading ? "Loading search" : "Search jobs");

  if (loading) return <LoadingMessage message="Loading…" />;
  if (error) return <BannerErrorMessage message={error} />;

  const { year_list = [], date_list = [] } = options || {};

  return (
    <div className="row">
      <hr />
      <div className="container-fluid search-home">
        <h1 className="h4 mb-3">Search jobs</h1>
        <section className="search-home-section">
          <h2 className="h5">List all jobs for a given year</h2>
          {year_list.length > 0 ? (
            year_list.length > 12 ? (
              <div className="search-year-compact mb-4">
                <label htmlFor="search-year-jump" className="form-label small mb-1">
                  Jump to year
                </label>
                <div className="d-flex flex-wrap gap-2 align-items-center">
                  <select
                    id="search-year-jump"
                    className="form-select form-select-sm"
                    style={{ maxWidth: "12rem" }}
                    defaultValue=""
                    onChange={(e) => {
                      const y = e.target.value;
                      if (y) navigate(`/year/${y}`);
                    }}
                  >
                    <option value="" disabled>
                      Select year…
                    </option>
                    {year_list.map((year) => (
                      <option key={year} value={year}>
                        {year}
                      </option>
                    ))}
                  </select>
                  <nav aria-label="Year list" className="search-year-inline-nav">
                    <ul className="pagination pagination-sm flex-wrap mb-0">
                      {year_list.slice(0, 8).map((year) => (
                        <li className="page-item" key={year}>
                          <Link className="page-link" to={`/year/${year}`}>
                            {year}
                          </Link>
                        </li>
                      ))}
                    </ul>
                  </nav>
                </div>
                <p className="text-muted small mb-0 mt-1">
                  Use the menu for any year; shortcuts show recent years.
                </p>
              </div>
            ) : (
              <nav aria-label="Year list" className="mb-4">
                <ul className="pagination pagination-sm flex-wrap">
                  {year_list.map((year) => (
                    <li className="page-item" key={year}>
                      <Link className="page-link" to={`/year/${year}`}>
                        {year}
                      </Link>
                    </li>
                  ))}
                </ul>
              </nav>
            )
          ) : (
            <p className="text-muted mb-4">No job data available.</p>
          )}
        </section>

        <section className="search-home-section search-date-list-section">
          <h2 className="h5">List all jobs for a given date</h2>
          {date_list.length > 0 ? (
            <details className="search-date-list-details" open>
              <summary className="search-date-list-summary">
                Show dates by month
              </summary>
              <div className="search-month-jump mb-2">
                <label htmlFor="search-month-jump-select" className="form-label small mb-1">
                  Jump to month
                </label>
                <select
                  id="search-month-jump-select"
                  className="form-select form-select-sm"
                  style={{ maxWidth: "16rem" }}
                  defaultValue=""
                  onChange={(e) => {
                    const month = e.target.value;
                    if (!month) return;
                    const el = document.getElementById(
                      `search-month-${month.replace(/[^a-zA-Z0-9_-]/g, "-")}`,
                    );
                    el?.scrollIntoView({ block: "start" });
                    e.target.selectedIndex = 0;
                  }}
                >
                  <option value="" disabled>
                    Select month…
                  </option>
                  {date_list.map(([month]) => (
                    <option key={month} value={month}>
                      {month}
                    </option>
                  ))}
                </select>
              </div>
              <nav aria-label="Date list" className="search-date-list-nav">
                {date_list.map(([month, dates]) => (
                  <div
                    className="search-date-list-month"
                    key={month}
                    id={`search-month-${month.replace(/[^a-zA-Z0-9_-]/g, "-")}`}
                  >
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

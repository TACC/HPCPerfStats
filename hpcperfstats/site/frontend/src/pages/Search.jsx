import { useId, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { useHomeOptions } from "../hooks/use-home-options";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function Search() {
  const navigate = useNavigate();
  const { options, error, loading } = useHomeOptions();
  const [browseTab, setBrowseTab] = useState("year");
  const tabYearId = useId();
  const tabCalendarId = useId();
  const panelYearId = useId();
  const panelCalendarId = useId();

  useDocumentTitle(loading ? "Loading browse" : "Browse jobs by time");

  if (loading) return <LoadingMessage message="Loading…" />;
  if (error) return <BannerErrorMessage message={error} />;

  const { year_list = [], date_list = [] } = options || {};

  const yearBrowsePrimary =
    year_list.length > 0 ? (
      year_list.length > 12 ? (
        <>
          <nav aria-label="Recent years" className="mb-3">
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
          <details className="search-quick-jump border rounded px-3 py-2 mb-3">
            <summary className="fw-medium small">Quick jump — all years</summary>
            <div className="mt-2 pb-1">
              <label htmlFor="search-year-jump" className="form-label small mb-1">
                Jump to year
              </label>
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
              <p className="text-muted small mb-0 mt-2">
                Shortcuts above show recent years; use the menu for any year.
              </p>
            </div>
          </details>
        </>
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
    );

  const calendarBrowsePrimary =
    date_list.length > 0 ? (
      <>
        <details className="search-quick-jump border rounded px-3 py-2 mb-3">
          <summary className="fw-medium small">Quick jump — month on page</summary>
          <div className="mt-2 pb-1">
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
        </details>
        <nav aria-label="Date list" className="search-date-list-nav">
          {date_list.map(([month, dates]) => (
            <div
              className="search-date-list-month"
              key={month}
              id={`search-month-${month.replace(/[^a-zA-Z0-9_-]/g, "-")}`}
            >
              <ul className="pagination pagination-sm flex-wrap">
                <li className="page-item">
                  <Link className="page-link" to={`/date/${month}`}>
                    {month}
                  </Link>
                </li>
                {dates.map(([dateStr, day]) => (
                  <li className="page-item" key={dateStr}>
                    <Link className="page-link" to={`/date/${dateStr}`}>
                      {day}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </>
    ) : (
      <p className="text-muted">No job data available</p>
    );

  return (
    <div className="search-home">
      <h1 className="h2 mb-3">Browse jobs by time</h1>
      <p className="text-muted small mb-3">
        Open a year or calendar day to see job lists. If you already know a job ID, use{" "}
        <strong>Find Job</strong> in the header; for richer filters, use{" "}
        <strong>Extended search</strong>.
      </p>

      <div className="search-browse-tabs mb-3">
        <ul className="nav nav-tabs" role="tablist">
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${browseTab === "year" ? "active" : ""}`}
              id={tabYearId}
              role="tab"
              aria-selected={browseTab === "year"}
              aria-controls={panelYearId}
              tabIndex={browseTab === "year" ? 0 : -1}
              onClick={() => setBrowseTab("year")}
            >
              By year
            </button>
          </li>
          <li className="nav-item" role="presentation">
            <button
              type="button"
              className={`nav-link ${browseTab === "calendar" ? "active" : ""}`}
              id={tabCalendarId}
              role="tab"
              aria-selected={browseTab === "calendar"}
              aria-controls={panelCalendarId}
              tabIndex={browseTab === "calendar" ? 0 : -1}
              onClick={() => setBrowseTab("calendar")}
            >
              By calendar
            </button>
          </li>
        </ul>
      </div>

      <section
        id={panelYearId}
        role="tabpanel"
        aria-labelledby={tabYearId}
        className="search-home-section"
        hidden={browseTab !== "year"}
      >
        <h2 className="visually-hidden">Browse by year</h2>
        {yearBrowsePrimary}
      </section>

      <section
        id={panelCalendarId}
        role="tabpanel"
        aria-labelledby={tabCalendarId}
        className="search-home-section search-date-list-section"
        hidden={browseTab !== "calendar"}
      >
        <h2 className="visually-hidden">Browse by calendar date</h2>
        {calendarBrowsePrimary}
      </section>
    </div>
  );
}

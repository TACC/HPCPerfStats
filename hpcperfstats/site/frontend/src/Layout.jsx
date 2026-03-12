import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import ExtendedSearch from "./components/ExtendedSearch";

export default function Layout({ session, children }) {
  const navigate = useNavigate();
  const [extendedSearchOpen, setExtendedSearchOpen] = useState(false);

  return (
    <div className="container-fluid">
      <nav className="navbar navbar-expand-lg navbar-light bg-light" role="navigation">
        <div className="container-fluid">
          <Link to="/" className="navbar-brand navbar-header-logo">
            <img
              src="/media/logo.png"
              alt="TACC"
              className="navbar-logo-img"
            />
          </Link>
          <div className="navbar-brand flex-grow-1 text-center">
            <div style={{ fontSize: "1.1em", fontWeight: 600, color: "black" }}>
              HPCPerfStats
            </div>
            <div className="text-muted small">a job-level resource usage monitoring tool</div>
            {session?.machine_name && (
              <div className="navbar-brand-cluster">{session.machine_name}</div>
            )}
          </div>
          <div className="navbar-actions ms-auto">
            <div className="navbar-actions-row">
              {session?.is_staff && (
                <>
                  <Link
                    to="/job_monitor"
                    className="btn btn-outline-secondary btn-sm me-2"
                  >
                    Job Monitor
                  </Link>
                  <Link
                    to="/admin_monitor"
                    className="btn btn-outline-secondary btn-sm"
                  >
                    HPCPerfStats Monitor
                  </Link>
                </>
              )}
              <a href="/logout/" className="btn btn-outline-secondary btn-sm">Logout</a>
            </div>
            <div className="navbar-actions-row">
              <button
                type="button"
                className="btn btn-outline-secondary btn-sm"
                onClick={() => setExtendedSearchOpen((o) => !o)}
                aria-expanded={extendedSearchOpen}
                aria-controls="extended-search-collapse"
              >
                {extendedSearchOpen ? "Hide extended search" : "Extended search"}
              </button>
              <form
                role="search"
                onSubmit={(e) => {
                  e.preventDefault();
                  const jid = e.target.jid?.value?.trim();
                  if (jid) navigate(`/job/${jid}`);
                }}
              >
                <div className="form-group">
                  <input
                    type="text"
                    className="form-control form-control-sm"
                    name="jid"
                    placeholder="Job ID"
                  />
                </div>
                <button type="submit" className="btn btn-outline-secondary btn-sm">
                  Find Job
                </button>
              </form>
            </div>
          </div>
        </div>
      </nav>
      {extendedSearchOpen && (
        <div
          id="extended-search-collapse"
          className="extended-search-collapse"
          role="region"
          aria-label="Extended search"
        >
          <ExtendedSearch onClose={() => setExtendedSearchOpen(false)} />
        </div>
      )}
      <main className="mt-4">
        {children}
      </main>
    </div>
  );
}

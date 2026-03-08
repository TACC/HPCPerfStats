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
          <Link to="/machine/" className="navbar-brand navbar-header-logo">
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
          <div className="d-flex flex-column align-items-end gap-2 ms-auto">
          <a href="/logout/" className="btn btn-outline-secondary btn-sm">Logout</a>
          <div className="d-flex align-items-center gap-2">
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
              style={{ display: "flex", alignItems: "center", gap: "6px" }}
            >
              <div className="form-group" style={{ marginBottom: 0 }}>
                <input
                  type="text"
                  className="form-control"
                  name="jid"
                  placeholder="Job ID"
                />
              </div>
              <button type="submit" className="btn btn-outline-secondary">
                find job
              </button>
            </form>
          </div>
        </div>
        </div>
      </nav>
      {session?.is_staff && (
        <div style={{ textAlign: "right", marginBottom: "8px" }}>
          <Link to="/admin_monitor">Admin Monitor</Link>
        </div>
      )}
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
      {children}
    </div>
  );
}

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import ExtendedSearch from "./components/ExtendedSearch";

export default function Layout({ session, children }) {
  const navigate = useNavigate();
  const [extendedSearchOpen, setExtendedSearchOpen] = useState(false);

  return (
    <div className="container-fluid">
      <nav className="navbar navbar-default" role="navigation">
        <div className="navbar-header">
          <a href="https://www.tacc.utexas.edu">
            <img
              src="/media/logo.png"
              alt="TACC"
              style={{ maxWidth: "50%" }}
            />
          </a>
        </div>
        <center>
          {session?.machine_name || "HPCPerfStats"}
          {session?.is_staff && (
            <>
              {" "}
              <Link to="/admin_monitor">Admin Monitor</Link>
            </>
          )}
        </center>
        <div className="navbar-form navbar-right" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <button
            type="button"
            className="btn btn-default btn-sm"
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
            <button type="submit" className="btn btn-default">
              find job
            </button>
          </form>
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
      {children}
    </div>
  );
}

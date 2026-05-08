import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useRouteFocusMain } from "../utils/useRouteFocusMain";

const PUB_LOGIN_PROMPT_HREF = `/login_prompt?next=${encodeURIComponent("/machine/")}`;

export default function LayoutPub({ machineName, children }) {
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  useRouteFocusMain(location.pathname);

  return (
    <div className="container-fluid">
      <a href="#main-content" className="visually-hidden visually-hidden-focusable">
        Skip to main content
      </a>
      <nav
        className="navbar navbar-expand-lg navbar-light bg-light"
        aria-label="Primary"
      >
        <div className="container-fluid">
          <Link to="/" className="navbar-brand navbar-header-logo">
            <img
              src="/media/logo.png"
              alt="TACC — HPCPerfStats home"
              className="navbar-logo-img"
            />
          </Link>
          <button
            type="button"
            className="navbar-toggler"
            onClick={() => setNavOpen((o) => !o)}
            aria-expanded={navOpen}
            aria-controls="navbar-main-pub"
            aria-label="Toggle navigation"
          >
            <span className="navbar-toggler-icon" />
          </button>
          <div
            id="navbar-main-pub"
            className={`collapse navbar-collapse ${navOpen ? "show" : ""}`}
          >
            <div className="navbar-brand flex-grow-1 text-center navbar-brand-center">
              <div style={{ fontSize: "1.1em", fontWeight: 600, color: "black" }}>
                HPCPerfStats
              </div>
              <div className="text-muted small">a job-level resource usage monitoring tool</div>
              {machineName ? (
                <div className="navbar-brand-cluster">{machineName}</div>
              ) : null}
            </div>
            <div className="navbar-actions ms-auto">
              <div className="navbar-actions-row navbar-actions-row-priority">
                <a href={PUB_LOGIN_PROMPT_HREF} className="btn btn-outline-secondary btn-sm">
                  Login to see individual job data
                </a>
              </div>
            </div>
          </div>
        </div>
      </nav>
      <main id="main-content" className="mt-4" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}

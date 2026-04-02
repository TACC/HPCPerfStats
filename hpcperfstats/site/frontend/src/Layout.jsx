import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import ExtendedSearch from "./components/ExtendedSearch";

export default function Layout({ session, onSessionChange, children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [extendedSearchOpen, setExtendedSearchOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [staffMessage, setStaffMessage] = useState("");
  const [isDroppingStaff, setIsDroppingStaff] = useState(false);
  const [isInvalidatingCache, setIsInvalidatingCache] = useState(false);
  const [staffActionValue, setStaffActionValue] = useState("");

  async function handleDropStaffForSession() {
    if (isDroppingStaff) return;
    setIsDroppingStaff(true);
    setStaffMessage("");
    try {
      const response = await api.dropStaffForSession();
      const refreshedSession = await api.getSession();
      if (typeof onSessionChange === "function") {
        onSessionChange(refreshedSession);
      }
      setStaffMessage(
        response?.message ||
          "Staff access removed for this session. Log out and log back in to restore staff access."
      );
    } catch (error) {
      setStaffMessage(error?.message || "Unable to remove staff access for this session.");
    } finally {
      setIsDroppingStaff(false);
    }
  }

  async function handleInvalidateCacheForPage() {
    if (isInvalidatingCache) return;
    setIsInvalidatingCache(true);
    setStaffMessage("");
    try {
      const response = await api.invalidateCacheForPage(location.pathname);
      const deletedCount = Number(response?.deleted_keys || 0);
      setStaffMessage(
        `Invalidated ${deletedCount} cache key${deletedCount === 1 ? "" : "s"} for ${location.pathname}.`
      );
    } catch (error) {
      setStaffMessage(error?.message || "Unable to invalidate cache for this page.");
    } finally {
      setIsInvalidatingCache(false);
    }
  }

  async function handleStaffActionChange(event) {
    const action = event.target.value;
    setStaffActionValue(action);
    if (!action) {
      return;
    }

    if (action === "job_monitor") {
      navigate("/job_monitor");
      setStaffActionValue("");
      return;
    }
    if (action === "admin_monitor") {
      navigate("/admin_monitor");
      setStaffActionValue("");
      return;
    }
    if (action === "drop_staff") {
      await handleDropStaffForSession();
      setStaffActionValue("");
      return;
    }
    if (action === "invalidate_cache") {
      await handleInvalidateCacheForPage();
      setStaffActionValue("");
      return;
    }

    setStaffActionValue("");
  }

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
          <button
            type="button"
            className="navbar-toggler"
            onClick={() => setNavOpen((o) => !o)}
            aria-expanded={navOpen}
            aria-controls="navbar-main"
            aria-label="Toggle navigation"
          >
            <span className="navbar-toggler-icon" />
          </button>
          <div
            id="navbar-main"
            className={`collapse navbar-collapse ${navOpen ? "show" : ""}`}
          >
            <div className="navbar-brand flex-grow-1 text-center navbar-brand-center">
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
                  <select
                    id="staff-actions"
                    className="form-select form-select-sm me-2"
                    value={staffActionValue}
                    onChange={handleStaffActionChange}
                    aria-label="Staff actions"
                    disabled={isDroppingStaff || isInvalidatingCache}
                  >
                    <option value="">Staff Actions</option>
                    <option value="job_monitor">Job Failure Monitor</option>
                    <option value="admin_monitor">HPCPerfStats Monitor</option>
                    <option value="drop_staff">Disable Staff Permissions</option>
                    <option value="invalidate_cache">Invalidate Cache For Page</option>
                  </select>
                )}
                <a href="/logout/" className="btn btn-outline-secondary btn-sm">Logout</a>
              </div>
              {staffMessage && (
                <div
                  id="staff-message"
                  className="alert alert-info py-1 px-2 mb-0 navbar-staff-message"
                  role="alert"
                >
                  {staffMessage}
                </div>
              )}
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

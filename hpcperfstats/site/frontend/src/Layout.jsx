import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "./api";
import LoadingMessage from "./components/LoadingMessage";
import { ExtendedSearchLayoutContext } from "./context/extended-search-layout-context";
import { useFocusTrap } from "./hooks/useFocusTrap";

const ExtendedSearch = lazy(() => import("./components/ExtendedSearch"));
import { useRouteFocusMain } from "./utils/useRouteFocusMain";

export default function Layout({ session, onSessionChange, children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  useRouteFocusMain(location.pathname);
  useEffect(() => {
    setMoreMenuOpen(false);
    setNavOpen(false);
    setExtendedSearchOpen(false);
    setFindJobError("");
  }, [location.pathname]);
  const [extendedSearchOpen, setExtendedSearchOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [findJobError, setFindJobError] = useState("");
  const [staffMessage, setStaffMessage] = useState("");
  const [isDroppingStaff, setIsDroppingStaff] = useState(false);
  const [isInvalidatingCache, setIsInvalidatingCache] = useState(false);
  const [staffMenuOpen, setStaffMenuOpen] = useState(false);
  const staffMenuRef = useRef(null);
  const staffMenuPanelRef = useRef(null);
  const extendedSearchPanelRef = useRef(null);
  const extendedSearchToggleRef = useRef(null);
  useFocusTrap(extendedSearchPanelRef, extendedSearchOpen);
  useFocusTrap(staffMenuPanelRef, staffMenuOpen);

  const closeExtendedSearch = useCallback(() => {
    setExtendedSearchOpen(false);
    window.requestAnimationFrame(() => {
      extendedSearchToggleRef.current?.focus();
    });
  }, []);

  const openExtendedSearch = useCallback(() => {
    setExtendedSearchOpen(true);
    setNavOpen(false);
  }, []);

  const extendedSearchLayoutValue = useMemo(
    () => ({ openExtendedSearch }),
    [openExtendedSearch],
  );

  function handleExtendedSearchBackdropClick(event) {
    if (event.target === event.currentTarget) {
      closeExtendedSearch();
    }
  }

  useEffect(() => {
    if (!staffMenuOpen) return;
    function handlePointerDown(event) {
      if (staffMenuRef.current && !staffMenuRef.current.contains(event.target)) {
        setStaffMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [staffMenuOpen]);

  useEffect(() => {
    if (!staffMenuOpen) return;
    function onKeyDown(e) {
      if (e.key === "Escape") {
        setStaffMenuOpen(false);
        document.getElementById("staff-actions-menu-button")?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [staffMenuOpen]);

  useEffect(() => {
    if (!extendedSearchOpen) return;
    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeExtendedSearch();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [extendedSearchOpen, closeExtendedSearch]);

  useEffect(() => {
    if (!extendedSearchOpen) return;
    const id = window.requestAnimationFrame(() => {
      const jidInput = document.getElementById("ext-jid");
      if (jidInput instanceof HTMLElement) {
        jidInput.focus();
        return;
      }
      const title = document.getElementById("extended-search-dialog-title");
      if (title instanceof HTMLElement) {
        title.focus();
      }
    });
    return () => window.cancelAnimationFrame(id);
  }, [extendedSearchOpen]);

  async function handleDropStaffForSession() {
    if (isDroppingStaff) return;
    if (
      !window.confirm(
        "Remove staff permissions for this browser session? You can restore them by signing out and signing in again.",
      )
    ) {
      return;
    }
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
          "Staff access removed for this session. Log out and log back in to restore staff access.",
      );
    } catch (error) {
      setStaffMessage(error?.message || "Unable to remove staff access for this session.");
    } finally {
      setIsDroppingStaff(false);
      setStaffMenuOpen(false);
    }
  }

  async function handleInvalidateCacheForPage() {
    if (isInvalidatingCache) return;
    const pagePathForCache =
      typeof window !== "undefined" && window.location.pathname
        ? window.location.pathname
        : location.pathname;
    if (
      !window.confirm(
        `Invalidate cached data for the current page path (${pagePathForCache})?`,
      )
    ) {
      return;
    }
    setIsInvalidatingCache(true);
    setStaffMessage("");
    try {
      const response = await api.invalidateCacheForPage(pagePathForCache);
      const deletedCount = Number(response?.deleted_keys || 0);
      setStaffMessage(
        `Invalidated ${deletedCount} cache key${deletedCount === 1 ? "" : "s"} for ${pagePathForCache}.`,
      );
    } catch (error) {
      setStaffMessage(error?.message || "Unable to invalidate cache for this page.");
    } finally {
      setIsInvalidatingCache(false);
      setStaffMenuOpen(false);
    }
  }

  const staffMenuBusy = isDroppingStaff || isInvalidatingCache;

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
            aria-controls="navbar-main"
            aria-label="Toggle navigation"
          >
            <span className="navbar-toggler-icon" />
          </button>
          <div
            id="navbar-main"
            className={`collapse navbar-collapse ${navOpen ? "show" : ""}`}
          >
            <Link
              to="/"
              className="navbar-brand flex-grow-1 text-center navbar-brand-center text-decoration-none"
            >
              <div style={{ fontSize: "1.1em", fontWeight: 600, color: "black" }}>
                HPCPerfStats
              </div>
              <div className="text-muted small">a job-level resource usage monitoring tool</div>
              {session?.machine_name && (
                <div className="navbar-brand-cluster">{session.machine_name}</div>
              )}
            </Link>
            <div className="navbar-actions ms-auto">
              <div className="navbar-actions-row navbar-actions-row-priority">
                <button
                  ref={extendedSearchToggleRef}
                  type="button"
                  className="btn btn-outline-secondary btn-sm"
                  onClick={() =>
                    setExtendedSearchOpen((o) => {
                      const next = !o;
                      if (!next) {
                        window.requestAnimationFrame(() => {
                          extendedSearchToggleRef.current?.focus();
                        });
                      }
                      return next;
                    })
                  }
                  aria-expanded={extendedSearchOpen}
                  aria-controls="extended-search-collapse"
                >
                  {extendedSearchOpen ? "Hide extended search" : "Extended search"}
                </button>
                <form
                  role="search"
                  aria-label="Find job by ID"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const jid = e.target.jid?.value?.trim();
                    if (!jid) {
                      setFindJobError("Enter a job ID.");
                      return;
                    }
                    setFindJobError("");
                    navigate(`/job/${jid}`);
                  }}
                >
                  <div className="form-group">
                    <label htmlFor="navbar-jid-search" className="visually-hidden">
                      Job ID search
                    </label>
                    <input
                      id="navbar-jid-search"
                      type="text"
                      className={`form-control form-control-sm${findJobError ? " is-invalid" : ""}`}
                      name="jid"
                      placeholder="Job ID"
                      title="Quick open by job ID (use Extended search for filters)"
                      autoComplete="off"
                      aria-invalid={findJobError ? true : undefined}
                      aria-describedby={findJobError ? "navbar-jid-error" : undefined}
                    />
                    {findJobError ? (
                      <div id="navbar-jid-error" className="invalid-feedback d-block small">
                        {findJobError}
                      </div>
                    ) : null}
                  </div>
                  <button type="submit" className="btn btn-outline-secondary btn-sm">
                    Find Job
                  </button>
                </form>
              </div>
              <div className="d-none d-lg-flex navbar-actions-row">
                {session?.is_staff && (
                  <div className="dropdown" ref={staffMenuRef}>
                    <button
                      type="button"
                      className="btn btn-outline-secondary btn-sm dropdown-toggle"
                      id="staff-actions-menu-button"
                      aria-expanded={staffMenuOpen}
                      aria-haspopup="true"
                      aria-label="Staff actions"
                      disabled={staffMenuBusy}
                      onClick={() => setStaffMenuOpen((o) => !o)}
                    >
                      Staff actions
                    </button>
                    {staffMenuOpen ? (
                      <ul
                        ref={staffMenuPanelRef}
                        className="dropdown-menu dropdown-menu-end show"
                        role="menu"
                        aria-labelledby="staff-actions-menu-button"
                        style={{ position: "absolute", zIndex: 1080 }}
                      >
                        <li>
                          <NavLink
                            to="/job_monitor"
                            className="dropdown-item"
                            role="menuitem"
                            onClick={() => {
                              setStaffMenuOpen(false);
                              setMoreMenuOpen(false);
                            }}
                          >
                            Job Failure Monitor
                          </NavLink>
                        </li>
                        <li>
                          <NavLink
                            to="/admin_monitor"
                            className="dropdown-item"
                            role="menuitem"
                            onClick={() => {
                              setStaffMenuOpen(false);
                              setMoreMenuOpen(false);
                            }}
                          >
                            HPCPerfStats Monitor
                          </NavLink>
                        </li>
                        <li>
                          <hr className="dropdown-divider" />
                        </li>
                        <li>
                          <button
                            type="button"
                            className="dropdown-item text-danger"
                            role="menuitem"
                            disabled={staffMenuBusy}
                            onClick={() => void handleDropStaffForSession()}
                          >
                            Disable Staff Permissions
                          </button>
                        </li>
                        <li>
                          <button
                            type="button"
                            className="dropdown-item"
                            role="menuitem"
                            disabled={staffMenuBusy}
                            onClick={() => void handleInvalidateCacheForPage()}
                          >
                            Invalidate Cache For Page
                          </button>
                        </li>
                      </ul>
                    ) : null}
                  </div>
                )}
                <NavLink
                  to="/api-key"
                  className={({ isActive }) =>
                    `btn btn-outline-secondary btn-sm${isActive ? " active" : ""}`
                  }
                >
                  API key
                </NavLink>
                <a href="/logout/" className="btn btn-outline-secondary btn-sm">
                  Logout
                </a>
              </div>
              <div className="d-lg-none w-100 mt-1">
                <button
                  type="button"
                  className="btn btn-outline-secondary btn-sm w-100"
                  aria-expanded={moreMenuOpen}
                  aria-controls="navbar-more-menu"
                  onClick={() => setMoreMenuOpen((o) => !o)}
                >
                  {moreMenuOpen ? "Hide account menu" : "Account and tools"}
                </button>
                {moreMenuOpen ? (
                  <div
                    id="navbar-more-menu"
                    className="d-flex flex-column gap-1 align-items-stretch mt-2"
                    role="group"
                    aria-label="Account and staff tools"
                  >
                    {session?.is_staff ? (
                      <>
                        <NavLink
                          to="/job_monitor"
                          className="btn btn-outline-secondary btn-sm text-start"
                          onClick={() => setMoreMenuOpen(false)}
                        >
                          Job Failure Monitor
                        </NavLink>
                        <NavLink
                          to="/admin_monitor"
                          className="btn btn-outline-secondary btn-sm text-start"
                          onClick={() => setMoreMenuOpen(false)}
                        >
                          HPCPerfStats Monitor
                        </NavLink>
                        <button
                          type="button"
                          className="btn btn-outline-danger btn-sm text-start"
                          disabled={staffMenuBusy}
                          onClick={() => void handleDropStaffForSession()}
                        >
                          Disable Staff Permissions
                        </button>
                        <button
                          type="button"
                          className="btn btn-outline-secondary btn-sm text-start"
                          disabled={staffMenuBusy}
                          onClick={() => void handleInvalidateCacheForPage()}
                        >
                          Invalidate Cache For Page
                        </button>
                      </>
                    ) : null}
                    <Link
                      to="/api-key"
                      className="btn btn-outline-secondary btn-sm"
                      onClick={() => setMoreMenuOpen(false)}
                    >
                      API key
                    </Link>
                    <a
                      href="/logout/"
                      className="btn btn-outline-secondary btn-sm"
                      onClick={() => setMoreMenuOpen(false)}
                    >
                      Logout
                    </a>
                  </div>
                ) : null}
              </div>
              {staffMessage && (
                <div
                  id="staff-message"
                  className="alert alert-info py-1 px-2 mb-0 navbar-staff-message"
                  role="status"
                  aria-live="polite"
                >
                  {staffMessage}
                </div>
              )}
            </div>
          </div>
        </div>
      </nav>
      {extendedSearchOpen ? (
        <div
          className="extended-search-backdrop"
          role="presentation"
          onClick={handleExtendedSearchBackdropClick}
          onKeyDown={(e) => {
            if (e.key === "Escape") closeExtendedSearch();
          }}
        >
          <div
            ref={extendedSearchPanelRef}
            id="extended-search-collapse"
            className="extended-search-collapse"
            role="dialog"
            aria-modal="true"
            aria-labelledby="extended-search-dialog-title"
            onClick={(e) => e.stopPropagation()}
          >
            <Suspense fallback={<LoadingMessage message="Loading search…" />}>
              <ExtendedSearch onClose={closeExtendedSearch} />
            </Suspense>
          </div>
        </div>
      ) : null}
      <main id="main-content" className="mt-4" tabIndex={-1}>
        <ExtendedSearchLayoutContext.Provider value={extendedSearchLayoutValue}>
          {children}
        </ExtendedSearchLayoutContext.Provider>
      </main>
    </div>
  );
}

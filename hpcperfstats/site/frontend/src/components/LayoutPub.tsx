import { usePathname } from "next/navigation";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useRouteFocusMain } from "../utils/useRouteFocusMain";

const PUB_LOGIN_PROMPT_HREF = `/login_prompt?next=${encodeURIComponent("/machine/")}`;

type LayoutPubProps = {
  machineName?: string | null;
  children: ReactNode;
};

export default function LayoutPub({ machineName, children }: LayoutPubProps) {
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  useRouteFocusMain(pathname);
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  return (
    <div className="container-fluid">
      <a href="#main-content" className="visually-hidden visually-hidden-focusable">
        Skip to main content
      </a>
      <nav
        className="navbar navbar-expand-lg navbar-light bg-light navbar-pub-site"
        aria-label="Primary"
      >
        <div className="container-fluid">
          <Link href="/pub/cluster-dashboard" className="navbar-brand navbar-header-logo">
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
              <div className="navbar-pub-site-title">HPCPerfStats</div>
              <div className="text-muted small navbar-pub-site-subtitle">
                a job-level resource usage monitoring tool
              </div>
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

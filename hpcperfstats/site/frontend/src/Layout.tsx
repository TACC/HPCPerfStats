import { useRouter, usePathname } from "next/navigation";
import NavLink from "@/components/NavLink";
import Link from "next/link";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { Menu } from "lucide-react";
import { api } from "@/api";
import type { ApiErrorBody } from "@/types/view-models";
import LoadingMessage from "./components/LoadingMessage";
import { ExtendedSearchLayoutContext } from "./context/extended-search-layout-context";
import { useFocusTrap } from "./hooks/useFocusTrap";
import type { SessionData } from "./session-context";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const ExtendedSearch = lazy(() => import("./components/ExtendedSearch"));
import { useRouteFocusMain } from "./utils/useRouteFocusMain";

type LayoutProps = {
  session: SessionData | null;
  onSessionChange?: (nextSession: SessionData | null) => void;
  children: ReactNode;
};

function getApiBody(value: unknown): ApiErrorBody {
  return value && typeof value === "object" ? (value as ApiErrorBody) : {};
}

function getErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  const body = getApiBody(error);
  if (typeof body.message === "string" && body.message.trim()) return body.message;
  if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  return fallback;
}

export default function Layout({ session, onSessionChange, children }: LayoutProps) {
  const router = useRouter();
  const pathname = usePathname();
  const machineName =
    session && typeof session.machine_name === "string" ? session.machine_name : "";
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  useRouteFocusMain(pathname);
  useEffect(() => {
    setMoreMenuOpen(false);
    setNavOpen(false);
    setExtendedSearchOpen(false);
    setFindJobError("");
  }, [pathname]);
  const [extendedSearchOpen, setExtendedSearchOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [findJobError, setFindJobError] = useState("");
  const [staffMessage, setStaffMessage] = useState("");
  const [isDroppingStaff, setIsDroppingStaff] = useState(false);
  const [isInvalidatingCache, setIsInvalidatingCache] = useState(false);
  const extendedSearchPanelRef = useRef<HTMLDivElement | null>(null);
  const extendedSearchToggleRef = useRef<HTMLButtonElement | null>(null);
  useFocusTrap(extendedSearchPanelRef, extendedSearchOpen);

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

  function handleExtendedSearchBackdropClick(event: ReactMouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) {
      closeExtendedSearch();
    }
  }

  useEffect(() => {
    if (!extendedSearchOpen) return;
    function onKeyDown(e: KeyboardEvent) {
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

  async function handleDropStaffForSession(closeMenu?: () => void) {
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
        onSessionChange(
          refreshedSession && typeof refreshedSession === "object"
            ? (refreshedSession as SessionData)
            : null,
        );
      }
      const responseBody = getApiBody(response);
      setStaffMessage(
        responseBody.message ||
          "Staff access removed for this session. Log out and log back in to restore staff access.",
      );
    } catch (error: unknown) {
      setStaffMessage(
        getErrorMessage(error, "Unable to remove staff access for this session."),
      );
    } finally {
      setIsDroppingStaff(false);
      closeMenu?.();
    }
  }

  async function handleInvalidateCacheForPage(closeMenu?: () => void) {
    if (isInvalidatingCache) return;
    const pagePathForCache =
      typeof window !== "undefined" && window.location.pathname
        ? window.location.pathname
        : pathname;
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
      const responseBody =
        response && typeof response === "object"
          ? (response as Record<string, unknown>)
          : {};
      const deletedCount = Number(responseBody.deleted_keys || 0);
      setStaffMessage(
        `Invalidated ${deletedCount} cache key${deletedCount === 1 ? "" : "s"} for ${pagePathForCache}.`,
      );
    } catch (error: unknown) {
      setStaffMessage(getErrorMessage(error, "Unable to invalidate cache for this page."));
    } finally {
      setIsInvalidatingCache(false);
      closeMenu?.();
    }
  }

  const staffMenuBusy = isDroppingStaff || isInvalidatingCache;

  return (
    <div className="w-full px-4 lg:px-6">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-[12000] focus:m-2 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:ring-2 focus:ring-ring"
      >
        Skip to main content
      </a>
      <header className="site-header border-b bg-muted/40" role="navigation" aria-label="Primary">
        <div className="relative flex flex-wrap items-start gap-3 py-2 lg:pb-3">
          <Link
            href="/"
            className="site-header-logo flex shrink-0 items-center py-1 lg:min-h-[50px]"
          >
            <img
              src="/media/logo.png"
              alt="TACC — HPCPerfStats home"
              className="h-10 w-auto max-h-[42px] object-contain lg:h-full"
            />
          </Link>
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            className="ml-auto lg:hidden"
            onClick={() => setNavOpen((o) => !o)}
            aria-expanded={navOpen}
            aria-controls="navbar-main"
            aria-label="Toggle navigation"
          >
            <Menu className="size-4" />
          </Button>
          <div
            id="navbar-main"
            className={cn(
              "site-header-nav w-full basis-full lg:flex lg:flex-1 lg:items-start lg:justify-end",
              navOpen ? "flex flex-col gap-3 border-t pt-3" : "hidden lg:flex",
            )}
          >
            <Link
              href="/"
              className="site-header-brand mx-auto flex max-w-[min(48vw,680px)] flex-col items-center text-center no-underline lg:absolute lg:left-1/2 lg:-translate-x-1/2"
            >
              <div className="text-lg font-semibold text-foreground">HPCPerfStats</div>
              <div className="text-sm text-muted-foreground">
                a job-level resource usage monitoring tool
              </div>
              {machineName ? (
                <div className="site-header-cluster text-[0.95em] text-muted-foreground">
                  {machineName}
                </div>
              ) : null}
            </Link>
            <div className="site-header-actions flex w-full flex-col items-stretch gap-2 lg:max-w-[min(42vw,520px)] lg:items-end">
              <div className="flex flex-wrap items-center justify-start gap-2 lg:justify-end">
                <Button
                  ref={extendedSearchToggleRef}
                  type="button"
                  variant="outline"
                  size="sm"
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
                </Button>
                <form
                  role="search"
                  aria-label="Find job by ID"
                  className="flex min-h-[34px] flex-wrap items-center gap-1.5"
                  onSubmit={(e: FormEvent<HTMLFormElement>) => {
                    e.preventDefault();
                    const formData = new FormData(e.currentTarget);
                    const jid = String(formData.get("jid") ?? "").trim();
                    if (!jid) {
                      setFindJobError("Enter a job ID.");
                      return;
                    }
                    setFindJobError("");
                    router.push(`/machine/job/${jid}/`);
                  }}
                >
                  <label htmlFor="navbar-jid-search" className="sr-only">
                    Job ID search
                  </label>
                  <Input
                    id="navbar-jid-search"
                    type="text"
                    className="h-[34px] min-w-[120px] flex-1 sm:max-w-[160px]"
                    name="jid"
                    placeholder="Job ID"
                    title="Quick open by job ID (use Extended search for filters)"
                    autoComplete="off"
                    aria-invalid={findJobError ? true : undefined}
                    aria-describedby={findJobError ? "navbar-jid-error" : undefined}
                  />
                  {findJobError ? (
                    <p id="navbar-jid-error" className="w-full text-xs text-destructive">
                      {findJobError}
                    </p>
                  ) : null}
                  <Button type="submit" variant="outline" size="sm">
                    Find Job
                  </Button>
                </form>
              </div>
              <div className="max-lg:hidden flex flex-wrap items-center justify-end gap-2">
                {session?.is_staff ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          id="staff-actions-menu-button"
                          variant="outline"
                          size="sm"
                          disabled={staffMenuBusy}
                          aria-label="Staff actions"
                        />
                      }
                    >
                      Staff actions
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-56">
                      <DropdownMenuItem
                        onClick={() => {
                          setMoreMenuOpen(false);
                          router.push("/machine/job_monitor/");
                        }}
                      >
                        Job Failure Monitor
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onClick={() => {
                          setMoreMenuOpen(false);
                          router.push("/machine/admin_monitor/");
                        }}
                      >
                        HPCPerfStats Monitor
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        disabled={staffMenuBusy}
                        onClick={() => void handleDropStaffForSession()}
                      >
                        Disable Staff Permissions
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={staffMenuBusy}
                        onClick={() => void handleInvalidateCacheForPage()}
                      >
                        Invalidate Cache For Page
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : null}
                <NavLink to="/api-key" className="inline-flex">
                  <Button variant="outline" size="sm" type="button">
                    API key
                  </Button>
                </NavLink>
                <Button variant="outline" size="sm" render={<a href="/machine/logout/" />}>
                  Logout
                </Button>
              </div>
              <div className="w-full lg:hidden">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="w-full"
                  aria-expanded={moreMenuOpen}
                  aria-controls="navbar-more-menu"
                  onClick={() => setMoreMenuOpen((o) => !o)}
                >
                  {moreMenuOpen ? "Hide account menu" : "Account and tools"}
                </Button>
                {moreMenuOpen ? (
                  <div
                    id="navbar-more-menu"
                    className="mt-2 flex flex-col gap-1"
                    role="group"
                    aria-label="Account and staff tools"
                  >
                    {session?.is_staff ? (
                      <>
                        <NavLink to="/job_monitor" onClick={() => setMoreMenuOpen(false)}>
                          <Button variant="outline" size="sm" className="w-full justify-start">
                            Job Failure Monitor
                          </Button>
                        </NavLink>
                        <NavLink to="/admin_monitor" onClick={() => setMoreMenuOpen(false)}>
                          <Button variant="outline" size="sm" className="w-full justify-start">
                            HPCPerfStats Monitor
                          </Button>
                        </NavLink>
                        <Button
                          type="button"
                          variant="destructive"
                          size="sm"
                          className="w-full justify-start"
                          disabled={staffMenuBusy}
                          onClick={() => void handleDropStaffForSession()}
                        >
                          Disable Staff Permissions
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="w-full justify-start"
                          disabled={staffMenuBusy}
                          onClick={() => void handleInvalidateCacheForPage()}
                        >
                          Invalidate Cache For Page
                        </Button>
                      </>
                    ) : null}
                    <Link href="/machine/api-key/" onClick={() => setMoreMenuOpen(false)}>
                      <Button variant="outline" size="sm" className="w-full">
                        API key
                      </Button>
                    </Link>
                    <Button
                      variant="outline"
                      size="sm"
                      className="w-full"
                      render={<a href="/machine/logout/" onClick={() => setMoreMenuOpen(false)} />}
                    >
                      Logout
                    </Button>
                  </div>
                ) : null}
              </div>
              {staffMessage ? (
                <Alert id="staff-message" className="site-header-staff-message py-1" role="status">
                  <AlertDescription aria-live="polite">{staffMessage}</AlertDescription>
                </Alert>
              ) : null}
            </div>
          </div>
        </div>
      </header>
      {extendedSearchOpen ? (
        <div
          className="extended-search-backdrop"
          role="presentation"
          onClick={handleExtendedSearchBackdropClick}
          onKeyDown={(e: ReactKeyboardEvent<HTMLDivElement>) => {
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
      <main id="main-content" className="mt-4 outline-none" tabIndex={-1}>
        <ExtendedSearchLayoutContext.Provider value={extendedSearchLayoutValue}>
          {children}
        </ExtendedSearchLayoutContext.Provider>
      </main>
    </div>
  );
}

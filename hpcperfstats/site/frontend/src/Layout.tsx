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
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Menu } from "lucide-react";
import {
  getHomeRetrieveQueryKey,
} from "@/api/generated/home/home";
import {
  getSessionRetrieveQueryKey,
  getSessionRetrieveQueryOptions,
  useSessionDropStaffCreate,
} from "@/api/generated/session/session";
import { useCacheInvalidatePageCreate } from "@/api/generated/admin/admin";
import { getApiBody, getErrorMessage } from "@/api/get-error-message";
import { orvalResponseData } from "@/api/orval-response";
import type { SessionInfo } from "@/api/generated/models/sessionInfo";
import type { InvalidateCacheResponse } from "@/api/generated/models/invalidateCacheResponse";
import LoadingMessage from "./components/LoadingMessage";
import LayoutRouteChromeReset from "./components/LayoutRouteChromeReset";
import { ExtendedSearchLayoutContext } from "./context/extended-search-layout-context";
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
import { useFocusTrap } from "./hooks/useFocusTrap";
import { SITE_MACHINE_NAME } from "@/config/site-identity";

const ExtendedSearch = lazy(() => import("./components/ExtendedSearch"));
import { useRouteFocusMain } from "./utils/useRouteFocusMain";

type LayoutProps = {
  session: SessionData | null;
  onSessionChange?: (nextSession: SessionData | null) => void;
  children: ReactNode;
};

export default function Layout({ session, onSessionChange, children }: LayoutProps) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const dropStaffMutation = useSessionDropStaffCreate();
  const invalidateCacheMutation = useCacheInvalidatePageCreate();
  const machineName = (
    (session && typeof session.machine_name === "string" ? session.machine_name : "") ||
    SITE_MACHINE_NAME
  ).trim();
  const [moreMenuOpen, setMoreMenuOpen] = useState(false);
  const [extendedSearchOpen, setExtendedSearchOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [findJobError, setFindJobError] = useState("");
  const [staffMessage, setStaffMessage] = useState("");
  const [isDroppingStaff, setIsDroppingStaff] = useState(false);
  const [isInvalidatingCache, setIsInvalidatingCache] = useState(false);
  useRouteFocusMain(pathname);
  const resetRouteChrome = useCallback(() => {
    setMoreMenuOpen(false);
    setNavOpen(false);
    setExtendedSearchOpen(false);
    setFindJobError("");
  }, []);
  const extendedSearchToggleRef = useRef<HTMLButtonElement | null>(null);
  const extendedSearchPanelRef = useRef<HTMLDivElement | null>(null);
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

  useEffect(() => {
    if (!extendedSearchOpen) return;
    function onPointerDown(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (extendedSearchPanelRef.current?.contains(target)) return;
      if (extendedSearchToggleRef.current?.contains(target)) return;
      closeExtendedSearch();
    }
    document.addEventListener("pointerdown", onPointerDown, true);
    return () => document.removeEventListener("pointerdown", onPointerDown, true);
  }, [extendedSearchOpen, closeExtendedSearch]);

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
      const response = await dropStaffMutation.mutateAsync();
      await queryClient.invalidateQueries({ queryKey: getSessionRetrieveQueryKey() });
      const refreshedSession = await queryClient.fetchQuery(getSessionRetrieveQueryOptions());
      if (typeof onSessionChange === "function") {
        const sessionBody = orvalResponseData<SessionInfo>(refreshedSession);
        onSessionChange(
          sessionBody
            ? {
                logged_in: sessionBody.logged_in,
                username: sessionBody.username,
                is_staff: sessionBody.is_staff,
                machine_name: sessionBody.machine_name ?? SITE_MACHINE_NAME,
              }
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
      const response = await invalidateCacheMutation.mutateAsync({
        data: { page_path: pagePathForCache },
      });
      const deletedCount = Number(orvalResponseData<InvalidateCacheResponse>(response)?.deleted_keys || 0);
      setStaffMessage(
        `Invalidated ${deletedCount} cache key${deletedCount === 1 ? "" : "s"} for ${pagePathForCache}.`,
      );
      void queryClient.invalidateQueries({ queryKey: getHomeRetrieveQueryKey() });
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
      <Suspense fallback={null}>
        <LayoutRouteChromeReset onReset={resetRouteChrome} />
      </Suspense>
      <header className="relative border-b bg-muted/40" role="navigation" aria-label="Primary">
        <div className="relative flex flex-wrap items-start gap-3 py-2 lg:pb-3">
          <Link
            href="/machine/"
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
              "w-full basis-full lg:static lg:flex lg:flex-1 lg:items-start lg:justify-end",
              navOpen ? "flex flex-col gap-3 border-t pt-3" : "hidden lg:flex",
            )}
          >
            <Link
              href="/machine/"
              className="mx-auto flex max-w-[min(48vw,680px)] flex-col items-center break-words text-center no-underline max-lg:mb-2 lg:absolute lg:left-1/2 lg:-translate-x-1/2"
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
            <div className="flex w-full flex-col items-stretch gap-2 max-lg:w-full lg:max-w-[min(42vw,520px)] lg:items-end">
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
                <Alert id="staff-message" className="w-full break-words py-1 text-left" role="status">
                  <AlertDescription aria-live="polite">{staffMessage}</AlertDescription>
                </Alert>
              ) : null}
            </div>
          </div>
        </div>
      </header>
      {extendedSearchOpen ? (
        <div
          className="pointer-events-none fixed inset-0 z-[var(--z-modal-backdrop)] flex items-start justify-center overflow-y-auto bg-black/35 pt-2"
          role="presentation"
          data-testid="extended-search-backdrop"
        >
          <div
            ref={extendedSearchPanelRef}
            id="extended-search-collapse"
            className="pointer-events-auto relative z-[calc(var(--z-modal-backdrop)+1)] w-full max-w-full border-b border-border bg-muted px-6 py-4 shadow-lg"
            role="dialog"
            aria-modal="true"
            aria-labelledby="extended-search-dialog-title"
          >
            <Suspense fallback={<LoadingMessage message="Loading search…" />}>
              <ExtendedSearch onClose={closeExtendedSearch} />
            </Suspense>
          </div>
        </div>
      ) : null}
      <main
        id="main-content"
        className="mt-4 outline-none max-lg:pl-[max(0px,env(safe-area-inset-left))] max-lg:pr-[max(0px,env(safe-area-inset-right))]"
        tabIndex={-1}
      >
        <ExtendedSearchLayoutContext.Provider value={extendedSearchLayoutValue}>
          {children}
        </ExtendedSearchLayoutContext.Provider>
      </main>
    </div>
  );
}

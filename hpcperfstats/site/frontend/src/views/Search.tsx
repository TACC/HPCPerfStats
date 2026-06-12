import { useRouter } from "next/navigation";
import Link from "next/link";
import { useId, useMemo, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { useHomeOptions } from "../hooks/use-home-options";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { useArrowKeyTabs } from "../hooks/useArrowKeyTabs";

type BrowseTab = "calendar" | "year";
type DateTuple = [string, string];
type DateListEntry = [string, DateTuple[]];

type BrowseTabButtonProps = {
  isActive: boolean;
  id: string;
  panelId: string;
  onClick: (event: MouseEvent<HTMLButtonElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  children: ReactNode;
};

function isDateTuple(value: unknown): value is DateTuple {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === "string" &&
    typeof value[1] === "string"
  );
}

function isDateListEntry(value: unknown): value is DateListEntry {
  return (
    Array.isArray(value) &&
    value.length >= 2 &&
    typeof value[0] === "string" &&
    Array.isArray(value[1]) &&
    value[1].every(isDateTuple)
  );
}

function normalizeDateList(value: unknown): DateListEntry[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isDateListEntry);
}

function normalizeYearList(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.filter((year): year is number => typeof year === "number" && Number.isFinite(year));
}

function toMonthSlug(value: unknown): string {
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "-");
}

function BrowseTabButton({ isActive, id, panelId, onClick, onKeyDown, children }: BrowseTabButtonProps) {
  return (
    <button
      type="button"
      className={`nav-link ${isActive ? "active" : ""}`}
      id={id}
      role="tab"
      aria-selected={isActive}
      aria-controls={panelId}
      tabIndex={isActive ? 0 : -1}
      onClick={onClick}
      onKeyDown={onKeyDown}
    >
      {children}
    </button>
  );
}

export default function Search() {
  const router = useRouter();
  const { options, error, loading } = useHomeOptions();
  const [browseTab, setBrowseTab] = useState<BrowseTab>("calendar");
  const tabYearId = useId();
  const tabCalendarId = useId();
  const panelYearId = useId();
  const panelCalendarId = useId();
  const browseTabButtonIds = useMemo(
    () => [tabCalendarId, tabYearId],
    [tabCalendarId, tabYearId],
  );
  const activeBrowseTabButtonId = browseTab === "year" ? tabYearId : tabCalendarId;
  const handleBrowseTabKeyDown = useArrowKeyTabs(
    browseTabButtonIds,
    activeBrowseTabButtonId,
    (nextTabButtonId) => {
      setBrowseTab(nextTabButtonId === tabYearId ? "year" : "calendar");
    },
  );

  useDocumentTitle(loading ? "Loading browse" : "Browse jobs by time");

  if (loading) return <LoadingMessage message="Loading…" />;
  if (error) return <BannerErrorMessage message={error} />;

  const yearList = normalizeYearList(options?.year_list);
  const dateList = normalizeDateList(options?.date_list);

  const yearBrowsePrimary =
    yearList.length > 0 ? (
      yearList.length > 12 ? (
        <>
          <nav aria-label="Recent years" className="mb-3">
            <ul className="pagination pagination-sm flex-wrap mb-0">
              {yearList.slice(0, 8).map((year) => (
                <li className="page-item" key={year}>
                  <Link className="page-link" href={`/machine/year/${year}/`}>
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
                  if (y) router.push(`/machine/year/${y}/`);
                }}
              >
                <option value="" disabled>
                  Select year…
                </option>
                {yearList.map((year) => (
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
            {yearList.map((year) => (
              <li className="page-item" key={year}>
                <Link className="page-link" href={`/machine/year/${year}/`}>
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
    dateList.length > 0 ? (
      <>
        <div className="search-month-jump border rounded px-3 py-2 mb-3">
          <label htmlFor="search-month-jump-select" className="form-label small mb-1">
            Jump to month
          </label>
          <select
            id="search-month-jump-select"
            className="form-select form-select-sm search-month-jump-select"
            defaultValue=""
            onChange={(e) => {
              const month = e.target.value;
              if (!month) return;
              const el = document.getElementById(
                `search-month-${toMonthSlug(month)}`,
              );
              el?.scrollIntoView({ block: "start" });
              e.target.selectedIndex = 0;
            }}
          >
            <option value="" disabled>
              Select month…
            </option>
            {dateList.map(([month]) => (
              <option key={month} value={month}>
                {month}
              </option>
            ))}
          </select>
        </div>
        <div className="search-calendar-months">
          {dateList.map(([month, dates]) => {
            const monthSlug = toMonthSlug(month);
            return (
            <section
              className="search-calendar-month-card"
              key={month}
              id={`search-month-${monthSlug}`}
              aria-labelledby={`search-month-heading-${monthSlug}`}
            >
              <div className="search-calendar-month-header">
                <Link className="search-calendar-month-title"
                  id={`search-month-heading-${monthSlug}`}
                  href={`/machine/date/${month}/`}
                >
                  {month}
                </Link>
              </div>
              <ul className="search-calendar-day-grid" role="list">
                {dates.map(([dateStr, day]) => (
                  <li key={dateStr} className="search-calendar-day-cell" role="listitem">
                    <Link className="search-calendar-day-link"
                      href={`/machine/date/${dateStr}/`}
                      aria-label={`Open jobs for ${month}, day ${day}`}
                    >
                      {day}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
            );
          })}
        </div>
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
            <BrowseTabButton
              isActive={browseTab === "calendar"}
              id={tabCalendarId}
              panelId={panelCalendarId}
              onClick={() => setBrowseTab("calendar")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) =>
                handleBrowseTabKeyDown(e, tabCalendarId)
              }
            >
              Calendar
            </BrowseTabButton>
          </li>
          <li className="nav-item" role="presentation">
            <BrowseTabButton
              isActive={browseTab === "year"}
              id={tabYearId}
              panelId={panelYearId}
              onClick={() => setBrowseTab("year")}
              onKeyDown={(e: KeyboardEvent<HTMLButtonElement>) =>
                handleBrowseTabKeyDown(e, tabYearId)
              }
            >
              By year
            </BrowseTabButton>
          </li>
        </ul>
      </div>

      <section
        id={panelCalendarId}
        role="tabpanel"
        aria-labelledby={tabCalendarId}
        className="search-home-section search-calendar-section"
        hidden={browseTab !== "calendar"}
      >
        <h2 className="visually-hidden">Browse by calendar</h2>
        {calendarBrowsePrimary}
      </section>

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
    </div>
  );
}

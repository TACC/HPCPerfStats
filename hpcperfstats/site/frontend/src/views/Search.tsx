import { useRouter } from "next/navigation";
import Link from "next/link";
import { useId, useMemo, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { buttonVariants } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
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

const browseTabTriggerClass = (isActive: boolean) =>
  cn(
    "inline-flex items-center justify-center rounded-t-md border border-transparent px-3 py-1.5 text-sm font-medium -mb-px transition-colors",
    "hover:border-border hover:border-b-transparent",
    isActive && "border-border border-b-transparent bg-background text-foreground",
  );

const nativeSelectClassName =
  "h-7 max-w-48 rounded-lg border border-input bg-transparent px-2.5 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30";

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
      className={browseTabTriggerClass(isActive)}
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

  const yearLinkClass = cn(
    buttonVariants({ variant: "outline", size: "sm" }),
    "min-h-11 min-w-11 inline-flex items-center justify-center",
  );

  const yearBrowsePrimary =
    yearList.length > 0 ? (
      yearList.length > 12 ? (
        <>
          <nav aria-label="Recent years" className="mb-3">
            <ul className="pagination flex flex-wrap gap-2">
              {yearList.slice(0, 8).map((year) => (
                <li key={year}>
                  <Link className={yearLinkClass} href={`/machine/year/${year}/`}>
                    {year}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <details className="search-quick-jump mb-3 rounded-lg border px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium">Quick jump — all years</summary>
            <div className="mt-2 pb-1">
              <Label htmlFor="search-year-jump" className="mb-1 text-xs font-normal">
                Jump to year
              </Label>
              <select
                id="search-year-jump"
                className={nativeSelectClassName}
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
              <p className="mt-2 mb-0 text-sm text-muted-foreground">
                Shortcuts above show recent years; use the menu for any year.
              </p>
            </div>
          </details>
        </>
      ) : (
        <nav aria-label="Year list" className="mb-4">
          <ul className="pagination flex flex-wrap gap-2">
            {yearList.map((year) => (
              <li key={year}>
                <Link className={yearLinkClass} href={`/machine/year/${year}/`}>
                  {year}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )
    ) : (
      <p className="mb-4 text-muted-foreground">No job data available.</p>
    );

  const calendarBrowsePrimary =
    dateList.length > 0 ? (
      <>
        <div className="search-month-jump mb-3 rounded-lg border px-3 py-2">
          <Label htmlFor="search-month-jump-select" className="mb-1 text-xs font-normal">
            Jump to month
          </Label>
          <select
            id="search-month-jump-select"
            className={cn(nativeSelectClassName, "search-month-jump-select w-full max-w-md")}
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
      <p className="text-muted-foreground">No job data available</p>
    );

  return (
    <div className="search-home">
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">Browse jobs by time</h1>
      <p className="mb-3 text-sm text-muted-foreground">
        Open a year or calendar day to see job lists. If you already know a job ID, use{" "}
        <strong>Find Job</strong> in the header; for richer filters, use{" "}
        <strong>Extended search</strong>.
      </p>

      <div className="search-browse-tabs mb-3">
        <div className="flex border-b" role="tablist">
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
        </div>
      </div>

      <section
        id={panelCalendarId}
        role="tabpanel"
        aria-labelledby={tabCalendarId}
        className="search-home-section search-calendar-section"
        hidden={browseTab !== "calendar"}
      >
        <h2 className="sr-only">Browse by calendar</h2>
        {calendarBrowsePrimary}
      </section>

      <section
        id={panelYearId}
        role="tabpanel"
        aria-labelledby={tabYearId}
        className="search-home-section"
        hidden={browseTab !== "year"}
      >
        <h2 className="sr-only">Browse by year</h2>
        {yearBrowsePrimary}
      </section>
    </div>
  );
}

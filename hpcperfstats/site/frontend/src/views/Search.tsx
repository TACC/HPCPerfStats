import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { buttonVariants, Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { useHomeOptions } from "../hooks/use-home-options";
import { useDocumentTitle } from "../utils/useDocumentTitle";

type BrowseTab = "calendar" | "year";
type DateTuple = [string, string];
type DateListEntry = [string, DateTuple[]];

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

const SEARCH_CALENDAR_MONTHS_INITIAL = 12;

export default function Search() {
  const router = useRouter();
  const { options, error, loading } = useHomeOptions();
  const [browseTab, setBrowseTab] = useState<BrowseTab>("calendar");
  const [monthJumpValue, setMonthJumpValue] = useState("");
  const [visibleMonthCount, setVisibleMonthCount] = useState(SEARCH_CALENDAR_MONTHS_INITIAL);

  useDocumentTitle(loading ? "Loading browse" : "Browse jobs by time");

  if (loading) return <LoadingMessage message="Loading…" />;
  if (error) return <BannerErrorMessage message={error} />;

  const yearList = normalizeYearList(options?.year_list);
  const dateList = normalizeDateList(options?.date_list);
  const visibleDateList = dateList.slice(0, visibleMonthCount);
  const hasMoreMonths = dateList.length > visibleMonthCount;

  const yearLinkClass = cn(
    buttonVariants({ variant: "outline", size: "sm" }),
    "min-h-11 min-w-11 inline-flex items-center justify-center",
  );

  const yearBrowsePrimary =
    yearList.length > 0 ? (
      yearList.length > 12 ? (
        <>
          <nav aria-label="Recent years" className="mb-3">
            <ul className="flex flex-wrap gap-2">
              {yearList.slice(0, 8).map((year) => (
                <li key={year}>
                  <Link className={yearLinkClass} href={`/machine/year/${year}/`}>
                    {year}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <Collapsible className="search-quick-jump mb-3 rounded-lg border px-3 py-2">
            <CollapsibleTrigger className="cursor-pointer text-left text-sm font-medium">
              Quick jump — all years
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 pb-1">
              <Label htmlFor="search-year-jump" className="mb-1 text-xs font-normal">
                Jump to year
              </Label>
              <Select
                onValueChange={(year) => {
                  if (year) router.push(`/machine/year/${year}/`);
                }}
              >
                <SelectTrigger id="search-year-jump" className="h-7 max-w-48">
                  <SelectValue placeholder="Select year…" />
                </SelectTrigger>
                <SelectContent>
                  {yearList.map((year) => (
                    <SelectItem key={year} value={String(year)}>
                      {year}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="mt-2 mb-0 text-sm text-muted-foreground">
                Shortcuts above show recent years; use the menu for any year.
              </p>
            </CollapsibleContent>
          </Collapsible>
        </>
      ) : (
        <nav aria-label="Year list" className="mb-4">
          <ul className="flex flex-wrap gap-2">
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
          <Select
            value={monthJumpValue}
            onValueChange={(month) => {
              if (!month) return;
              const el = document.getElementById(`search-month-${toMonthSlug(month)}`);
              el?.scrollIntoView({ block: "start" });
              setMonthJumpValue("");
            }}
          >
            <SelectTrigger
              id="search-month-jump-select"
              className="h-7 w-full max-w-[min(100%,20rem)]"
            >
              <SelectValue placeholder="Select month…" />
            </SelectTrigger>
            <SelectContent>
              {dateList.map(([month]) => (
                <SelectItem key={month} value={month}>
                  {month}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-4">
          {visibleDateList.map(([month, dates]) => {
            const monthSlug = toMonthSlug(month);
            return (
              <section
                className="rounded-[var(--radius)] border border-border bg-background p-3 sm:px-4 max-md:p-[0.65rem_0.75rem]"
                key={month}
                id={`search-month-${monthSlug}`}
                aria-labelledby={`search-month-heading-${monthSlug}`}
              >
                <div className="mb-[0.65rem] border-b border-border pb-2">
                  <Link
                    className="text-[1.05rem] font-semibold text-foreground no-underline hover:underline"
                    id={`search-month-heading-${monthSlug}`}
                    href={`/machine/date/${month}/`}
                  >
                    {month}
                  </Link>
                </div>
                <ul
                  className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(2.65rem,1fr))] gap-x-[0.45rem] gap-y-2 p-0 sm:grid-cols-[repeat(auto-fill,minmax(2.85rem,1fr))] sm:gap-x-2 lg:max-w-[42rem] lg:grid-cols-[repeat(auto-fill,minmax(3rem,1fr))] max-md:grid-cols-[repeat(auto-fill,minmax(2.4rem,1fr))] max-md:gap-[0.35rem]"
                  role="list"
                >
                  {dates.map(([dateStr, day]) => (
                    <li key={dateStr} className="min-w-0" role="listitem">
                      <Link
                        className="box-border flex min-h-10 w-full items-center justify-center rounded-[var(--radius-sm)] border border-border bg-muted px-[0.35rem] py-1 text-center text-[0.9rem] font-medium text-foreground no-underline hover:border-primary hover:bg-accent hover:text-primary max-md:min-h-[2.35rem] max-md:text-[0.85rem]"
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
        {hasMoreMonths ? (
          <div className="mt-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setVisibleMonthCount((count) =>
                  Math.min(dateList.length, count + SEARCH_CALENDAR_MONTHS_INITIAL),
                )
              }
            >
              Load more months ({dateList.length - visibleMonthCount} remaining)
            </Button>
          </div>
        ) : null}
      </>
    ) : (
      <p className="text-muted-foreground">No job data available</p>
    );

  return (
    <div className="pb-6">
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">Browse jobs by time</h1>
      <p className="mb-3 text-sm text-muted-foreground">
        Open a year or calendar day to see job lists. If you already know a job ID, use{" "}
        <strong>Find Job</strong> in the header; for richer filters, use{" "}
        <strong>Extended search</strong>.
      </p>

      <Tabs
        value={browseTab}
        onValueChange={(value) => setBrowseTab(value as BrowseTab)}
        className="sticky top-0 z-[var(--z-sticky-inpage)] mb-3 bg-background pt-1"
      >
        <TabsList variant="line" className="w-full justify-start">
          <TabsTrigger value="calendar">Calendar</TabsTrigger>
          <TabsTrigger value="year">By year</TabsTrigger>
        </TabsList>

        <TabsContent value="calendar" className="search-home-section mt-3 [&_h4]:mb-2 max-md:[&_h4]:text-[1.1rem]">
          <h2 className="sr-only">Browse by calendar</h2>
          {browseTab === "calendar" ? calendarBrowsePrimary : null}
        </TabsContent>

        <TabsContent value="year" className="mb-0 mt-3 last:mb-0">
          <h2 className="sr-only">Browse by year</h2>
          {browseTab === "year" ? yearBrowsePrimary : null}
        </TabsContent>
      </Tabs>
    </div>
  );
}

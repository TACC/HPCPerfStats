import { useRouter } from "next/navigation";
import Link from "next/link";
import { useState } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { buttonVariants } from "@/components/ui/button";
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

export default function Search() {
  const router = useRouter();
  const { options, error, loading } = useHomeOptions();
  const [browseTab, setBrowseTab] = useState<BrowseTab>("calendar");
  const [monthJumpValue, setMonthJumpValue] = useState("");

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
              className="search-month-jump-select h-7 w-full max-w-md"
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
                  <Link
                    className="search-calendar-month-title"
                    id={`search-month-heading-${monthSlug}`}
                    href={`/machine/date/${month}/`}
                  >
                    {month}
                  </Link>
                </div>
                <ul className="search-calendar-day-grid" role="list">
                  {dates.map(([dateStr, day]) => (
                    <li key={dateStr} className="search-calendar-day-cell" role="listitem">
                      <Link
                        className="search-calendar-day-link"
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

      <Tabs
        value={browseTab}
        onValueChange={(value) => setBrowseTab(value as BrowseTab)}
        className="search-browse-tabs mb-3"
      >
        <TabsList variant="line" className="w-full justify-start">
          <TabsTrigger value="calendar">Calendar</TabsTrigger>
          <TabsTrigger value="year">By year</TabsTrigger>
        </TabsList>

        <TabsContent value="calendar" className="search-home-section search-calendar-section mt-3">
          <h2 className="sr-only">Browse by calendar</h2>
          {calendarBrowsePrimary}
        </TabsContent>

        <TabsContent value="year" className="search-home-section mt-3">
          <h2 className="sr-only">Browse by year</h2>
          {yearBrowsePrimary}
        </TabsContent>
      </Tabs>
    </div>
  );
}

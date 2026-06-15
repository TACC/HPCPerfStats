import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import FilterMultiCombobox from "@/components/FilterMultiCombobox";
import { cn } from "@/lib/utils";
import {
  JOB_LIST_TABLE_HEADERS,
  PROJECT_FIELD_LABEL,
} from "@/utils/site-field-labels";
import {
  applyHeaderFilterChange,
  clearAllHeaderFilters,
  clearHeaderFilterDimension,
  parseHeaderFilterSet,
  readHeaderFilterSet,
  toggleHeaderFilterValue,
  type JobListHeaderFilterKey,
} from "@/utils/job-list-header-filter-params";

export type JobListFilterOptions = {
  usernames?: string[];
  accounts?: string[];
  queues?: string[];
  states?: string[];
  performance_statuses?: Array<{ sort_rank?: number; label?: string }>;
  truncated?: {
    usernames?: boolean;
    accounts?: boolean;
    queues?: boolean;
    states?: boolean;
  };
};

type JobListHeaderFiltersProps = {
  filterOptions: JobListFilterOptions | null | undefined;
  loading?: boolean;
  routeParams: Record<string, string | string[] | undefined>;
};

function FilterChipRow({
  label,
  options,
  selected,
  disabled,
  onToggle,
  onClear,
  valueKey,
}: {
  label: string;
  options: Array<{ value: string; text: string }>;
  selected: Set<string>;
  disabled?: boolean;
  onToggle: (value: string) => void;
  onClear: () => void;
  valueKey: JobListHeaderFilterKey;
}) {
  if (!options.length) {
    return (
      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
        </div>
        <p className="text-xs text-muted-foreground">No options in this selection.</p>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        {selected.size > 0 ? (
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto px-0 text-xs"
            onClick={onClear}
            disabled={disabled}
          >
            Clear
          </Button>
        ) : null}
      </div>
      <div
        className="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto"
        role="group"
        aria-label={`${label} filters`}
      >
        {options.map((option) => {
          const isSelected = selected.has(option.value);
          return (
            <Button
              key={`${valueKey}-${option.value}`}
              type="button"
              size="sm"
              variant={isSelected ? "default" : "outline"}
              className={cn("h-7 px-2.5 text-xs", isSelected && "ring-1 ring-ring/40")}
              aria-pressed={isSelected}
              disabled={disabled}
              onClick={() => onToggle(option.value)}
            >
              {option.text}
            </Button>
          );
        })}
      </div>
    </div>
  );
}

export default function JobListHeaderFilters({
  filterOptions,
  loading = false,
  routeParams,
}: JobListHeaderFiltersProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const selectedUser = readHeaderFilterSet(searchParams, routeParams, "username");
  const selectedProject = readHeaderFilterSet(searchParams, routeParams, "account");
  const selectedQueue = readHeaderFilterSet(searchParams, routeParams, "queue");
  const selectedState = parseHeaderFilterSet(searchParams, "state");
  const selectedPerformance = parseHeaderFilterSet(searchParams, "performance_sort_rank");

  const hasAnyHeaderFilter =
    selectedUser.size +
      selectedProject.size +
      selectedQueue.size +
      selectedState.size +
      selectedPerformance.size >
    0;

  function applyToggle(key: JobListHeaderFilterKey, value: string, current: Set<string>) {
    applyHeaderFilterChange({
      router,
      pathname,
      searchParams,
      routeParams,
      key,
      nextValues: toggleHeaderFilterValue(current, value),
    });
  }

  const performanceOptions =
    filterOptions?.performance_statuses?.map((row) => ({
      value: String(row.sort_rank ?? ""),
      text: row.label || String(row.sort_rank ?? ""),
    })) ?? [];

  if (loading && !filterOptions) {
    return (
      <Card className="mb-4 border bg-card/50 shadow-none">
        <CardHeader className="pb-2">
          <CardTitle className="text-base font-medium">Refine this list</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-16 w-full" />
          ))}
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="mb-4 border bg-card/50 shadow-none">
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 pb-2">
        <CardTitle className="text-base font-medium">Refine this list</CardTitle>
        {hasAnyHeaderFilter ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="text-xs"
            onClick={() =>
              clearAllHeaderFilters({ router, pathname, searchParams, routeParams })
            }
          >
            Clear header filters
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <FilterChipRow
          label={JOB_LIST_TABLE_HEADERS.performanceData}
          options={performanceOptions}
          selected={selectedPerformance}
          disabled={loading}
          valueKey="performance_sort_rank"
          onToggle={(value) => applyToggle("performance_sort_rank", value, selectedPerformance)}
          onClear={() =>
            clearHeaderFilterDimension({
              router,
              pathname,
              searchParams,
              routeParams,
              key: "performance_sort_rank",
            })
          }
        />
        <FilterMultiCombobox
          id="job-list-filter-user"
          label={JOB_LIST_TABLE_HEADERS.user}
          options={filterOptions?.usernames ?? []}
          selected={selectedUser}
          disabled={loading}
          truncated={filterOptions?.truncated?.usernames}
          onToggle={(value) => applyToggle("username", value, selectedUser)}
          onClear={() =>
            clearHeaderFilterDimension({
              router,
              pathname,
              searchParams,
              routeParams,
              key: "username",
            })
          }
        />
        <FilterMultiCombobox
          id="job-list-filter-project"
          label={PROJECT_FIELD_LABEL}
          options={filterOptions?.accounts ?? []}
          selected={selectedProject}
          disabled={loading}
          truncated={filterOptions?.truncated?.accounts}
          onToggle={(value) => applyToggle("account", value, selectedProject)}
          onClear={() =>
            clearHeaderFilterDimension({
              router,
              pathname,
              searchParams,
              routeParams,
              key: "account",
            })
          }
        />
        <FilterChipRow
          label={JOB_LIST_TABLE_HEADERS.queue}
          options={(filterOptions?.queues ?? []).map((queue) => ({ value: queue, text: queue }))}
          selected={selectedQueue}
          disabled={loading}
          valueKey="queue"
          onToggle={(value) => applyToggle("queue", value, selectedQueue)}
          onClear={() =>
            clearHeaderFilterDimension({
              router,
              pathname,
              searchParams,
              routeParams,
              key: "queue",
            })
          }
        />
        <FilterChipRow
          label="Status"
          options={(filterOptions?.states ?? []).map((state) => ({ value: state, text: state }))}
          selected={selectedState}
          disabled={loading}
          valueKey="state"
          onToggle={(value) => applyToggle("state", value, selectedState)}
          onClear={() =>
            clearHeaderFilterDimension({
              router,
              pathname,
              searchParams,
              routeParams,
              key: "state",
            })
          }
        />
      </CardContent>
    </Card>
  );
}

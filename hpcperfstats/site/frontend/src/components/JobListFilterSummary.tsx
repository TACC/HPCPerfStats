import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useExtendedSearchLayout } from "../context/extended-search-layout-context";

type JobListFilterSummaryProps = {
  lines: string[];
  className?: string;
};

export default function JobListFilterSummary({
  lines,
  className = "",
}: JobListFilterSummaryProps) {
  const { openExtendedSearch } = useExtendedSearchLayout();
  if (!lines.length) return null;

  return (
    <Alert
      role="region"
      aria-label="Active search filters"
      className={cn("mb-3 border bg-muted/30 text-sm", className)}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <AlertDescription className="text-foreground">
          <AlertTitle className="mb-1 text-sm font-medium">Active filters</AlertTitle>
          <ul className="mb-0 list-disc pl-5">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </AlertDescription>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={openExtendedSearch}
        >
          Modify search
        </Button>
      </div>
    </Alert>
  );
}

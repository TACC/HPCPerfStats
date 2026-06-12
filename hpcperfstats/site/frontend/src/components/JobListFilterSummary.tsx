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
    <div
      className={`job-list-filter-summary alert alert-light border small mb-3 ${className}`.trim()}
      role="region"
      aria-label="Active search filters"
    >
      <div className="d-flex flex-wrap align-items-start justify-content-between gap-2">
        <div>
          <p className="mb-1 fw-medium">Active filters</p>
          <ul className="mb-0 ps-3">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
        <button
          type="button"
          className="btn btn-outline-primary btn-sm flex-shrink-0"
          onClick={openExtendedSearch}
        >
          Modify search
        </button>
      </div>
    </div>
  );
}

import { useMemo, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

type FilterMultiComboboxProps = {
  id: string;
  label: string;
  options: string[];
  selected: Set<string>;
  disabled?: boolean;
  truncated?: boolean;
  onToggle: (value: string) => void;
  onClear: () => void;
};

export default function FilterMultiCombobox({
  id,
  label,
  options,
  selected,
  disabled = false,
  truncated = false,
  onToggle,
  onClear,
}: FilterMultiComboboxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => option.toLowerCase().includes(needle));
  }, [options, query]);

  const triggerLabel =
    selected.size === 0
      ? `Any ${label.toLowerCase()}`
      : selected.size === 1
        ? [...selected][0]
        : `${label} (${selected.size})`;

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
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          nativeButton
          className={cn(
            buttonVariants({ variant: "outline", size: "sm" }),
            "w-full justify-between font-normal",
          )}
          id={id}
          disabled={disabled}
          aria-label={`${label} filter`}
        >
          <span className="truncate">{triggerLabel}</span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
        </PopoverTrigger>
        <PopoverContent className="w-[min(100vw-2rem,320px)] p-0" align="start">
          <div className="border-b p-2">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={`Search ${label.toLowerCase()}…`}
              aria-label={`Search ${label.toLowerCase()}`}
              className="h-8"
            />
          </div>
          <ul
            className="max-h-56 overflow-y-auto p-1"
            role="listbox"
            aria-label={`${label} options`}
          >
            {filtered.length === 0 ? (
              <li className="px-2 py-3 text-sm text-muted-foreground">No matches.</li>
            ) : (
              filtered.map((option) => {
                const isSelected = selected.has(option);
                return (
                  <li key={option}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent",
                        isSelected && "bg-accent/60",
                      )}
                      onClick={() => onToggle(option)}
                    >
                      <Check
                        className={cn("size-4 shrink-0", !isSelected && "opacity-0")}
                        aria-hidden="true"
                      />
                      <span className="truncate">{option}</span>
                    </button>
                  </li>
                );
              })
            )}
          </ul>
          {truncated ? (
            <p className="border-t px-2 py-1.5 text-xs text-muted-foreground">
              Showing first options only — narrow the job selection to see more.
            </p>
          ) : null}
        </PopoverContent>
      </Popover>
      {selected.size > 0 ? (
        <div className="flex flex-wrap gap-1">
          {[...selected].map((value) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant="secondary"
              className="h-6 px-2 text-xs"
              aria-pressed="true"
              onClick={() => onToggle(value)}
            >
              {value}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

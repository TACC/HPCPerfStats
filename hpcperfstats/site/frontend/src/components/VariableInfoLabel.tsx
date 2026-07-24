"use client";

import { useId, useState, useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { getVariableTooltipContent } from "../utils/variableMetadata";

const HELP_HOVER_OPEN_DELAY_MS = 150;
const HELP_HOVER_CLOSE_DELAY_MS = 150;

type VariableInfoLabelProps = {
  variableName: string;
  labelText?: string;
  enableHelp?: boolean;
  suffixBeforeHelp?: ReactNode;
};

/**
 * Renders a variable label with an optional inline help control when metadata exists.
 * Preserves the exact label text; units should be rendered by the parent outside this component.
 */
export function VariableInfoLabel({
  variableName,
  labelText,
  enableHelp = false,
  suffixBeforeHelp = null,
}: VariableInfoLabelProps) {
  const text = labelText != null ? labelText : variableName;
  const tooltipBody = enableHelp ? getVariableTooltipContent(variableName) : null;
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(false);

  function closeHelp() {
    setPinned(false);
    setOpen(false);
  }

  useEffect(() => {
    if (!pinned) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeHelp();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [pinned]);

  if (!tooltipBody) {
    return (
      <>
        {text}
        {suffixBeforeHelp}
      </>
    );
  }

  const { description, researcherUse } = tooltipBody;

  return (
    <span className="inline-flex max-w-full flex-wrap items-baseline gap-0 leading-[1.3]">
      <span className="min-w-0">{text}</span>
      {suffixBeforeHelp}
      <Popover
        open={open}
        onOpenChange={(next, details) => {
          // While pinned, ignore outside dismiss / hover-close; X and Escape call closeHelp().
          if (!next && pinned) {
            details.cancel();
            return;
          }
          setOpen(next);
          if (!next) setPinned(false);
        }}
        modal={false}
      >
        <span className="inline-flex shrink-0 items-baseline">
          <PopoverTrigger
            nativeButton
            openOnHover={!pinned}
            delay={HELP_HOVER_OPEN_DELAY_MS}
            closeDelay={HELP_HOVER_CLOSE_DELAY_MS}
            className="relative inline-flex h-[1em] w-[0.85em] cursor-pointer items-start justify-center border-0 bg-transparent p-0 align-super text-[0.65em] font-semibold leading-none text-link select-none before:absolute before:-inset-2 before:content-['']"
            data-testid="variable-info-help"
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            aria-label={`Help: ${variableName}`}
            onClick={() => {
              if (pinned) {
                closeHelp();
                return;
              }
              setPinned(true);
              setOpen(true);
            }}
          >
            ?
          </PopoverTrigger>
        </span>
        <PopoverContent
          id={panelId}
          role="region"
          data-testid="variable-info-tooltip"
          aria-label={`${variableName} description`}
          sideOffset={2}
          className={cn(
            "variable-info-tooltip variable-info-tooltip-portal relative w-auto max-w-[min(560px,calc(100vw-16px))] min-w-[min(420px,calc(100vw-16px))] p-2 text-sm font-normal",
            pinned && "pt-7",
          )}
        >
          {pinned ? (
            <button
              type="button"
              data-testid="variable-info-close"
              aria-label="Close help"
              className="absolute right-1.5 top-1.5 inline-flex h-6 w-6 cursor-pointer items-center justify-center rounded-sm border-0 bg-transparent p-0 text-muted-foreground hover:text-foreground"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                closeHelp();
              }}
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          ) : null}
          <span className="variable-info-tooltip-definition">{description}</span>
          {researcherUse ? (
            <>
              <Separator className="variable-info-tooltip-sep my-2" />
              <span className="variable-info-tooltip-researcher-use">{researcherUse}</span>
            </>
          ) : null}
        </PopoverContent>
      </Popover>
    </span>
  );
}

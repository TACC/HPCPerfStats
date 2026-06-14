"use client";

import { useId, useState, useEffect, type ReactNode } from "react";
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
  const [pinnedOpen, setPinnedOpen] = useState(false);

  useEffect(() => {
    if (!pinnedOpen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        setPinnedOpen(false);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [pinnedOpen]);

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
    <span className="inline-flex max-w-full flex-wrap items-baseline gap-[0.1rem] leading-[1.3]">
      <span className="min-w-0">{text}</span>
      {suffixBeforeHelp}
      <Popover
        open={pinnedOpen ? true : undefined}
        onOpenChange={(next, details) => {
          if (!next && pinnedOpen) {
            details.cancel();
            return;
          }
          if (!next) setPinnedOpen(false);
        }}
        modal={false}
      >
        <span className="inline-flex shrink-0 items-baseline">
          <PopoverTrigger
            nativeButton
            openOnHover={!pinnedOpen}
            delay={HELP_HOVER_OPEN_DELAY_MS}
            closeDelay={HELP_HOVER_CLOSE_DELAY_MS}
            className="inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center border-0 bg-transparent p-0 align-baseline text-[0.72em] font-semibold leading-none text-primary select-none"
            data-testid="variable-info-help"
            aria-expanded={pinnedOpen}
            aria-controls={pinnedOpen ? panelId : undefined}
            aria-label={`Help: ${variableName}`}
            onClick={() => setPinnedOpen((prev) => !prev)}
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
            "variable-info-tooltip variable-info-tooltip-portal w-auto max-w-[min(560px,calc(100vw-16px))] min-w-[min(420px,calc(100vw-16px))] p-2 text-sm font-normal",
          )}
        >
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

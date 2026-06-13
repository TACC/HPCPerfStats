"use client";

import { useId, useState, useEffect, type ReactNode } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { getVariableTooltipContent } from "../utils/variableMetadata";

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
  const [hoverOpen, setHoverOpen] = useState(false);
  const showTooltip = open || hoverOpen;

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

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
    <span className="variable-info-label">
      <span className="variable-info-label-text">{text}</span>
      {suffixBeforeHelp}
      <Popover open={showTooltip} onOpenChange={setOpen}>
        <span
          className="variable-info-help-wrap"
          onMouseEnter={() => setHoverOpen(true)}
          onMouseLeave={() => setHoverOpen(false)}
          onFocus={() => setHoverOpen(true)}
          onBlur={() => setHoverOpen(false)}
        >
          <PopoverTrigger
            nativeButton
            className="variable-info-help"
            data-testid="variable-info-help"
            aria-expanded={showTooltip}
            aria-controls={showTooltip ? panelId : undefined}
            aria-label={`Help: ${variableName}`}
          >
            ?
          </PopoverTrigger>
        </span>
        <PopoverContent
          id={panelId}
          role="region"
          data-testid="variable-info-tooltip"
          aria-label={`${variableName} description`}
          className={cn(
            "variable-info-tooltip variable-info-tooltip-portal w-auto max-w-[min(560px,calc(100vw-16px))] min-w-[min(420px,calc(100vw-16px))] p-2 text-sm font-normal",
          )}
          onMouseEnter={() => setHoverOpen(true)}
          onMouseLeave={() => setHoverOpen(false)}
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

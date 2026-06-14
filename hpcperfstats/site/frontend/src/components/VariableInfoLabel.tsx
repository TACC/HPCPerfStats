"use client";

import { useId, useState, useEffect, type ReactNode } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { useDelayedHoverState } from "@/hooks/use-delayed-hover-state";
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
  const [pinnedOpen, setPinnedOpen] = useState(false);
  const { hoverOpen, onHoverEnter, onHoverLeave } = useDelayedHoverState();
  const showTooltip = pinnedOpen || hoverOpen;

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
    <span className="variable-info-label">
      <span className="variable-info-label-text">{text}</span>
      {suffixBeforeHelp}
      <Popover
        open={showTooltip}
        onOpenChange={(next) => {
          if (!next) setPinnedOpen(false);
        }}
      >
        <span
          className="variable-info-help-wrap"
          onMouseEnter={onHoverEnter}
          onMouseLeave={onHoverLeave}
          onFocus={onHoverEnter}
          onBlur={onHoverLeave}
        >
          <PopoverTrigger
            nativeButton
            className="variable-info-help"
            data-testid="variable-info-help"
            aria-expanded={showTooltip}
            aria-controls={showTooltip ? panelId : undefined}
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
          onMouseEnter={onHoverEnter}
          onMouseLeave={onHoverLeave}
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

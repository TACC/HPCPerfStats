import { useId, useState, useEffect, useRef, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { getVariableTooltipContent } from "../utils/variableMetadata";

const VIEWPORT_MARGIN = 8;
const GAP_PX = 6;

function placeTooltipNearButton(buttonEl, tooltipEl) {
  const br = buttonEl.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  tooltipEl.style.top = `${br.bottom + GAP_PX}px`;
  tooltipEl.style.left = `${br.left}px`;

  const tr = tooltipEl.getBoundingClientRect();

  let top = br.bottom + GAP_PX;
  if (top + tr.height > vh - VIEWPORT_MARGIN) {
    const above = br.top - GAP_PX - tr.height;
    if (above >= VIEWPORT_MARGIN) {
      top = above;
    } else {
      top = Math.max(VIEWPORT_MARGIN, vh - VIEWPORT_MARGIN - tr.height);
    }
  }

  let left = br.left;
  if (left + tr.width > vw - VIEWPORT_MARGIN) {
    left = vw - VIEWPORT_MARGIN - tr.width;
  }
  if (left < VIEWPORT_MARGIN) {
    left = VIEWPORT_MARGIN;
  }

  tooltipEl.style.top = `${top}px`;
  tooltipEl.style.left = `${left}px`;
}

/**
 * Renders a variable label with an optional inline help control when metadata exists.
 * Preserves the exact label text; units should be rendered by the parent outside this component.
 *
 * @param {object} props
 * @param {string} props.variableName
 * @param {string} [props.labelText] — If omitted, variableName is shown (still normalized for lookup via variableName).
 * @param {boolean} [props.enableHelp] — Must be true to render the help control (keeps usage scoped to Job Detail).
 */
export function VariableInfoLabel({ variableName, labelText, enableHelp = false }) {
  const text = labelText != null ? labelText : variableName;
  const tooltipBody = enableHelp ? getVariableTooltipContent(variableName) : null;
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [hoverOpen, setHoverOpen] = useState(false);
  const showTooltip = open || hoverOpen;
  const buttonRef = useRef(null);
  const tooltipRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        window.requestAnimationFrame(() => buttonRef.current?.focus());
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  useLayoutEffect(() => {
    if (!showTooltip) return;
    const btnEl = buttonRef.current;
    const tipEl = tooltipRef.current;
    if (!btnEl || !tipEl) return;

    function updatePosition() {
      placeTooltipNearButton(btnEl, tipEl);
    }

    updatePosition();
    window.addEventListener("scroll", updatePosition, true);
    window.addEventListener("resize", updatePosition);
    return () => {
      window.removeEventListener("scroll", updatePosition, true);
      window.removeEventListener("resize", updatePosition);
    };
  }, [showTooltip, variableName, enableHelp]);

  if (!tooltipBody) {
    return <>{text}</>;
  }

  const { description, researcherUse } = tooltipBody;

  const tooltipNode = showTooltip ? (
    <span
      ref={tooltipRef}
      id={panelId}
      role="region"
      className="variable-info-tooltip variable-info-tooltip-portal"
      data-testid="variable-info-tooltip"
      aria-label={`${variableName} description`}
    >
      <span className="variable-info-tooltip-definition">{description}</span>
      {researcherUse ? (
        <>
          <hr className="variable-info-tooltip-sep" role="separator" />
          <span className="variable-info-tooltip-researcher-use">{researcherUse}</span>
        </>
      ) : null}
    </span>
  ) : null;

  return (
    <>
      {text}
      <span
        className="variable-info-help-wrap"
        onMouseEnter={() => setHoverOpen(true)}
        onMouseLeave={() => setHoverOpen(false)}
        onFocus={() => setHoverOpen(true)}
        onBlur={() => setHoverOpen(false)}
      >
        <button
          ref={buttonRef}
          type="button"
          className="variable-info-help"
          data-testid="variable-info-help"
          aria-expanded={showTooltip}
          aria-controls={showTooltip ? panelId : undefined}
          aria-label={`Help: ${variableName}`}
          onClick={() => setOpen((o) => !o)}
        >
          ?
        </button>
      </span>
      {tooltipNode && document.body ? createPortal(tooltipNode, document.body) : null}
    </>
  );
}

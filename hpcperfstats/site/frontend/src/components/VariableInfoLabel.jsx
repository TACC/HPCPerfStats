import { useId, useState, useEffect, useRef } from "react";
import { getDescriptionForVariable } from "../utils/variableMetadata";

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
  const description = enableHelp ? getDescriptionForVariable(variableName) : null;
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const buttonRef = useRef(null);

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

  if (!description) {
    return <>{text}</>;
  }

  return (
    <>
      {text}
      <span className="variable-info-help-wrap">
        <button
          ref={buttonRef}
          type="button"
          className="variable-info-help"
          data-testid="variable-info-help"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={`Help: ${variableName}`}
          onClick={() => setOpen((o) => !o)}
        >
          ?
        </button>
        {open ? (
          <span
            id={panelId}
            role="region"
            className="variable-info-tooltip"
            data-testid="variable-info-tooltip"
            aria-label={`${variableName} description`}
          >
            {description}
          </span>
        ) : null}
      </span>
    </>
  );
}

import { useId, useState } from "react";
import { getDescriptionForVariable } from "../utils/variableMetadata";

/**
 * Renders a variable label with an optional inline help marker when metadata exists.
 * Preserves the exact label text; units should be rendered by the parent outside this component.
 *
 * @param {object} props
 * @param {string} props.variableName
 * @param {string} [props.labelText] — If omitted, variableName is shown (still normalized for lookup via variableName).
 * @param {boolean} [props.enableHelp] — Must be true to render the help icon (keeps usage scoped to Job Detail).
 */
export function VariableInfoLabel({ variableName, labelText, enableHelp = false }) {
  const text = labelText != null ? labelText : variableName;
  const description = enableHelp ? getDescriptionForVariable(variableName) : null;
  const tooltipId = useId();
  const [open, setOpen] = useState(false);

  if (!description) {
    return <>{text}</>;
  }

  return (
    <>
      {text}
      <span className="variable-info-help-wrap">
        <span
          className="variable-info-help"
          data-testid="variable-info-help"
          tabIndex={0}
          aria-describedby={open ? tooltipId : undefined}
          aria-label={description}
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
        >
          ?
        </span>
        {open && (
          <span
            id={tooltipId}
            role="tooltip"
            className="variable-info-tooltip"
            data-testid="variable-info-tooltip"
          >
            {description}
          </span>
        )}
      </span>
    </>
  );
}

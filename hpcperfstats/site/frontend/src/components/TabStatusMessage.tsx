import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export type TabStatusMessageProps = {
  children: ReactNode;
  /** Optional ARIA role; when `"status"`, also sets `aria-live="polite"`. */
  role?: "status" | "note";
  className?: string;
};

/**
 * Centered status / empty-state message for Job Detail (and similar) tab panels.
 * Extra bottom padding keeps standalone copy off the bottom edge of the page.
 */
export default function TabStatusMessage({
  children,
  role,
  className,
}: TabStatusMessageProps) {
  return (
    <p
      className={cn(
        "tab-status-message mx-auto mb-0 max-w-prose px-4 pt-8 pb-16 text-center text-sm text-muted-foreground print:pt-0 print:pb-2",
        className,
      )}
      role={role}
      {...(role === "status" ? { "aria-live": "polite" as const } : {})}
    >
      {children}
    </p>
  );
}

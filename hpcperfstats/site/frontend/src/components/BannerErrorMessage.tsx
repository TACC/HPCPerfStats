import type { CSSProperties, ReactNode } from "react";

export type BannerErrorMessageProps = {
  message: ReactNode;
  className?: string;
  style?: CSSProperties;
  variant?: "page" | "inline";
};

/** Page-level `Error: …` banner, or `variant="inline"` for section errors. */
export default function BannerErrorMessage({
  message,
  className,
  style,
  variant = "page",
}: BannerErrorMessageProps) {
  if (variant === "inline") {
    return (
      <div className={className ?? "text-danger"} style={style} role="alert">
        {message}
      </div>
    );
  }
  return (
    <div
      className={className ?? "container text-danger"}
      style={style}
      role="alert"
    >
      Error: {message}
    </div>
  );
}

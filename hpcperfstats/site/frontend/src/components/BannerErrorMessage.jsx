/** Page-level `Error: …` banner, or `variant="inline"` for section errors (full message string). */
export default function BannerErrorMessage({
  message,
  className,
  style,
  variant = "page",
}) {
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

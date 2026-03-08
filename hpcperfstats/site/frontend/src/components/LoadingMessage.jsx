/**
 * Shared loading indicator for AJAX/data requests.
 * Use with a descriptive message so users know what is loading.
 */
export default function LoadingMessage({ message = "Loading…" }) {
  return (
    <div
      className="container text-center"
      style={{ padding: "2rem" }}
      role="status"
      aria-live="polite"
    >
      <span className="text-muted">{message}</span>
    </div>
  );
}

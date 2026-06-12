export type LoadingMessageProps = {
  message?: string;
};

/**
 * Shared loading indicator for AJAX/data requests.
 */
export default function LoadingMessage({ message = "Loading…" }: LoadingMessageProps) {
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

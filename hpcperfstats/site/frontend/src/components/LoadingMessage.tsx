export type LoadingMessageProps = {
  message?: string;
};

export default function LoadingMessage({ message = "Loading…" }: LoadingMessageProps) {
  return (
    <div
      className="mx-auto w-full max-w-7xl px-4 py-8 text-center text-foreground"
      role="status"
      aria-live="polite"
    >
      {message}
    </div>
  );
}

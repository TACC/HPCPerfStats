"use client";

import { Button } from "@/components/ui/button";
import { getErrorMessage } from "@/api/get-error-message";

export default function MachineError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const message = getErrorMessage(error, "Something went wrong loading this page.");

  return (
    <main id="main-content" className="mx-auto max-w-3xl px-4 py-8" tabIndex={-1}>
      <h1 className="mb-3 text-2xl font-semibold tracking-tight">Page error</h1>
      <p className="mb-4 text-muted-foreground" role="alert">
        {message}
      </p>
      <Button type="button" variant="outline" onClick={() => reset()}>
        Try again
      </Button>
    </main>
  );
}

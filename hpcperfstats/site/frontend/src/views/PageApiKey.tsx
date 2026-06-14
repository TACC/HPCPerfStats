import { TextLink } from "@/components/TextLink";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useUserApiKey } from "@/hooks/use-user-api-key";
import { useDocumentTitle } from "../utils/useDocumentTitle";
import { copyToClipboard } from "../utils/copy-to-clipboard";

export default function PageApiKey() {
  const rotateHelpId = useId();
  const {
    data,
    error: loadError,
    loading,
    rotate,
    rotating,
    rotateError,
    refetch,
  } = useUserApiKey();
  const [copyStatus, setCopyStatus] = useState("");
  const [actionError, setActionError] = useState("");
  const keyRef = useRef<HTMLElement | null>(null);
  const announceRef = useRef<HTMLDivElement | null>(null);

  const username = data?.username || "";
  const rawKey = data?.raw_key || null;
  const keyPrefix = data?.key_prefix || "";
  const error = actionError || loadError || rotateError || "";

  useDocumentTitle("API key");

  useEffect(() => {
    if (!rawKey) return;
    const ann = announceRef.current;
    if (ann) {
      ann.textContent =
        "A new API key is displayed on this page. Copy it now; it will not be shown again.";
    }
    const el = keyRef.current;
    if (el) {
      window.requestAnimationFrame(() => {
        el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    }
  }, [rawKey]);

  async function handleCopy() {
    const key = (rawKey || "").trim();
    if (!key) return;
    setCopyStatus("");
    const didCopy = await copyToClipboard(key);
    setCopyStatus(didCopy ? "Copied" : "Copy failed");
  }

  async function handleRotate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (rotating) return;
    if (
      !window.confirm(
        "Invalidate your current API key and create a new one? The old key will stop working immediately.",
      )
    ) {
      return;
    }
    setActionError("");
    setCopyStatus("");
    try {
      await rotate();
      await refetch();
    } catch (e: unknown) {
      setActionError(e instanceof Error ? e.message : "Unable to rotate API key.");
    }
  }

  if (loading) {
    return <LoadingMessage message="Loading API key…" />;
  }

  if (error && !username && !keyPrefix && rawKey === null) {
    return <BannerErrorMessage message={error} />;
  }

  return (
    <>
      <div
        id="api-key-page-announce"
        className="sr-only"
        ref={announceRef}
        aria-live="polite"
        aria-atomic="true"
      />
      <div className="mx-auto max-w-[640px] px-4">
        <p className="mb-3">
          <TextLink href="/machine/">
            Back to HPCPerfStats
          </TextLink>
        </p>
        <main id="api-key-main">
          <Card className="shadow-sm">
            <CardHeader>
              <CardTitle className="text-xl">HPCPerfStats API key</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p>
                Signed in as: <strong>{username}</strong>
              </p>
              {error ? (
                <Alert role="status" className="border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}
              {rawKey ? (
                <>
                  <p>Your API key for programmatic access is:</p>
                  <div className="api-key-row mt-2 flex flex-wrap items-center gap-2">
                    <code
                      ref={keyRef}
                      id="api-key-value"
                      className="inline-block break-all rounded-md bg-muted px-2 py-[0.35rem] font-mono text-sm [overflow-wrap:anywhere]"
                    >
                      {rawKey}
                    </code>
                    <Button
                      type="button"
                      id="copy-api-key"
                      variant="outline"
                      size="sm"
                      aria-label="Copy API key"
                      onClick={() => void handleCopy()}
                    >
                      Copy
                    </Button>
                  </div>
                  <div
                    id="api-key-copy-status"
                    className="mt-1 min-h-[1.25em] text-sm text-muted-foreground"
                    aria-live="polite"
                  >
                    {copyStatus}
                  </div>
                  <p className="mt-3">
                    <strong>This key is shown only once.</strong> Store it securely now.
                  </p>
                </>
              ) : (
                <>
                  <p>You already have an active API key, and for security it cannot be shown again.</p>
                  <p>
                    Active key prefix: <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm">{keyPrefix}</code>
                  </p>
                  <p>Use your saved copy, or rotate to generate a new key.</p>
                </>
              )}
              <p className="mt-3">
                Store this key securely. You can use it with the <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm">hpcperfstats-jobstats</code> and{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm">hpcperfstats-sacct-gen</code> tools (from the hpcperfstats-tools package) by passing{" "}
                <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm">--api-key</code> or using the cached key in <code className="rounded bg-muted px-1 py-0.5 font-mono text-sm">~/.hpcperfstats-api</code>.
              </p>
              <form
                className="mt-4"
                id="api-key-rotate-form"
                aria-describedby={rotateHelpId}
                onSubmit={(e) => void handleRotate(e)}
              >
                <p id={rotateHelpId} className="mb-2 text-sm text-muted-foreground">
                  This revokes your current key and creates a replacement. Confirm before submitting.
                </p>
                <Button
                  type="submit"
                  variant="secondary"
                  id="api-key-rotate-submit"
                  disabled={rotating}
                  className={cn(rotating && "opacity-70")}
                >
                  {rotating ? "Working…" : "Invalidate and Create New Key"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </main>
      </div>
    </>
  );
}

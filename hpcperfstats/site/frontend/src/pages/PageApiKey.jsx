import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import BannerErrorMessage from "../components/BannerErrorMessage";
import LoadingMessage from "../components/LoadingMessage";
import { api } from "../api";
import { useDocumentTitle } from "../utils/useDocumentTitle";

export default function PageApiKey() {
  const rotateHelpId = useId();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [username, setUsername] = useState("");
  const [rawKey, setRawKey] = useState(null);
  const [keyPrefix, setKeyPrefix] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const [rotating, setRotating] = useState(false);
  const keyRef = useRef(null);
  const announceRef = useRef(null);

  useDocumentTitle("API key");

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.getUserApiKey();
      setUsername(data.username || "");
      setRawKey(data.raw_key || null);
      setKeyPrefix(data.key_prefix || "");
    } catch (e) {
      setError(e?.message || "Unable to load API key status.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

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
    try {
      await navigator.clipboard.writeText(key);
      setCopyStatus("Copied");
    } catch (err) {
      console.error("Failed to copy API key", err);
      setCopyStatus("Copy failed");
    }
  }

  async function handleRotate(event) {
    event.preventDefault();
    if (rotating) return;
    if (
      !window.confirm(
        "Invalidate your current API key and create a new one? The old key will stop working immediately.",
      )
    ) {
      return;
    }
    setRotating(true);
    setError("");
    setCopyStatus("");
    try {
      const data = await api.rotateUserApiKey();
      setUsername(data.username || "");
      setRawKey(data.raw_key || null);
      setKeyPrefix(data.key_prefix || "");
    } catch (e) {
      setError(e?.message || "Unable to rotate API key.");
    } finally {
      setRotating(false);
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
      <a href="#api-key-main" className="visually-hidden visually-hidden-focusable">
        Skip to main content
      </a>
      <div
        id="api-key-page-announce"
        className="visually-hidden"
        ref={announceRef}
        aria-live="polite"
        aria-atomic="true"
      />
      <div className="container page-api-key-container">
        <p className="mb-3">
          <Link to="/" className="link-primary">
            Back to HPCPerfStats
          </Link>
        </p>
        <main id="api-key-main">
          <div className="card shadow-sm">
            <div className="card-body">
              <h1 className="h3 card-title">HPCPerfStats API key</h1>
              <p>
                Signed in as: <strong>{username}</strong>
              </p>
              {error ? (
                <div className="alert alert-warning" role="status">
                  {error}
                </div>
              ) : null}
              {rawKey ? (
                <>
                  <p>Your API key for programmatic access is:</p>
                  <div className="api-key-row d-flex flex-wrap align-items-center gap-2 mt-2">
                    <code
                      ref={keyRef}
                      id="api-key-value"
                      className="api-key-code-block d-inline-block"
                    >
                      {rawKey}
                    </code>
                    <button
                      type="button"
                      id="copy-api-key"
                      className="btn btn-outline-secondary btn-sm"
                      aria-label="Copy API key"
                      onClick={() => void handleCopy()}
                    >
                      Copy
                    </button>
                  </div>
                  <div
                    id="api-key-copy-status"
                    className="api-key-copy-status mt-1 small text-muted"
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
                    Active key prefix: <code>{keyPrefix}</code>
                  </p>
                  <p>Use your saved copy, or rotate to generate a new key.</p>
                </>
              )}
              <p className="mt-3">
                Store this key securely. You can use it with the <code>hpcperfstats-jobstats</code> and{" "}
                <code>hpcperfstats-sacct-gen</code> tools (from the hpcperfstats-tools package) by passing{" "}
                <code>--api-key</code> or using the cached key in <code>~/.hpcperfstats-api</code>.
              </p>
              <form
                className="mt-4"
                id="api-key-rotate-form"
                aria-describedby={rotateHelpId}
                onSubmit={(e) => void handleRotate(e)}
              >
                <p id={rotateHelpId} className="small text-muted mb-2">
                  This revokes your current key and creates a replacement. Confirm before submitting.
                </p>
                <button
                  type="submit"
                  className="btn btn-warning"
                  id="api-key-rotate-submit"
                  disabled={rotating}
                >
                  {rotating ? "Working…" : "Invalidate and Create New Key"}
                </button>
              </form>
            </div>
          </div>
        </main>
      </div>
    </>
  );
}

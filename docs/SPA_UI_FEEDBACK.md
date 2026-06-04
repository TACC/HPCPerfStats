# SPA user feedback policy

The HPCPerfStats React SPAs (`/machine`, `/pub`) do **not** use toast notifications (no Sonner, react-hot-toast, etc.).

## Patterns

| Situation | Pattern |
|-----------|---------|
| Page loading | `LoadingMessage` or route-level skeleton (`job-list-skeleton`, `job-detail-skeleton`) |
| Fatal fetch error | `BannerErrorMessage` (early return) |
| Partial / section load | Muted “Loading …” text or per-section `LoadingMessage` |
| Partial API failure | `alert alert-warning` with optional **Retry** (job list histograms, job detail plots) |
| Staff diagnostics | Expandable plot error detail in `BokehEmbed` |
| Destructive confirm | `window.confirm` before staff/API-key actions |
| Success after mutation | Inline text (`Copied`, `Working…`) or staff `alert-info` in the navbar |

## Rationale

Toasts are easy to miss, stack poorly on mobile, and fight focus management for dialog-style extended search. Inline and banner feedback stay in context for data-heavy pages.

## When adding features

Reuse the components above before introducing a new feedback channel. If a new surface needs persistent success messaging, prefer an `alert-success` region with `role="status"` near the action—not a global toast.

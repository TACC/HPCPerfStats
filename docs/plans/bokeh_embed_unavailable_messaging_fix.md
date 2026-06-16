---
name: BokehEmbed unavailable messaging fix
overview: Restore BokehEmbed staff vs non-staff unavailable plot messaging to match job-detail-analysis-tab-consistency.mdc and fix six failing Vitest view tests plus stale BokehEmbed unit tests.
todos:
  - id: fix-bokeh-embed-message-logic
    content: "BokehEmbed.tsx: generic public status when unavailableReason set; staff expand/copy uses API reason in panel"
    status: pending
  - id: lock-bokeh-embed-unit-tests
    content: "Rewrite BokehEmbed.test.tsx unavailable/staff tests to match canonical contract (replace inline-reason assertions)"
    status: pending
  - id: verify-view-regressions
    content: "Run JobDetail, TypeDetail, HostDetail, BokehEmbed Vitest; confirm interactive-ready JobDetail multiprecision test passes"
    status: pending
  - id: rules-cross-link
    content: "Cross-link bokeh-layout-surface-split.mdc or design-focused-spa-ux.mdc to staff/generic unavailable contract (no new rule if overlap suffices)"
    status: pending
  - id: log-verification
    content: "Log typecheck + Vitest + build to test_runs/bokeh_embed_unavailable_messaging_<date>.md"
    status: pending
  - id: post-implementation-review
    content: "Final senior code review (diff + workflow) clean; structured self-review in chat (why it works, edge cases, convention check)"
    status: pending
isProject: false
---

# BokehEmbed unavailable messaging fix

Restore canonical **Unavailable — Data not available.** public copy for researchers while staff retain expandable API **`unavailableReason`** detail — fixing six failing view tests left after the interactive-ready controls plan.

**Governed by:** `agent-discipline-core.mdc`, `plan-completion-gate.mdc`, `plan-creation-contract.mdc`, `implementation-review-workflow.mdc`.

**Artifact placement:** committed design baseline (`test-runs-output-directory.mdc`). Run logs go under `test_runs/`.

---

## 1. Problem and facts

### Verified failures (2026-06-15)

Targeted Vitest after interactive-ready controls: **72/74 pass** in the plan suite; **6 failures** in a broader run of plot-detail views:

| Test file | Failing test | Expected | Actual (DOM) |
|-----------|--------------|----------|--------------|
| `JobDetail.test.tsx` | `shows multiprecision unavailable copy while full job detail fetch is still pending` | ≥2× `"Unavailable — Data not available."` | API reason strings from `minimalJobDetailResponse` |
| `JobDetail.test.tsx` | `shows staff plot error detail controls on Multiprecision Mix when reasons are present` | 2× **Show plot error details** / **Copy error detail** | No buttons (reason shown inline) |
| `TypeDetail.test.tsx` | `shows plot unavailable message without details for non-staff` | Generic unavailable copy | Full `tplot_unavailable_reason` text |
| `TypeDetail.test.tsx` | `shows staff plot error detail controls when the plot is unavailable` | Staff expand/copy buttons | No buttons |
| `HostDetail.test.tsx` | Same two patterns as TypeDetail | Same | Same |

Reproduce:

```bash
cd HPCPerfStats/hpcperfstats/site/frontend
npm test -- --run \
  src/views/__tests__/JobDetail.test.tsx \
  src/views/__tests__/TypeDetail.test.tsx \
  src/views/__tests__/HostDetail.test.tsx
```

### Root cause (code, not tests)

`BokehEmbed.tsx` message selection (lines 527–541) sets **`message = unavailableReason`** when a reason exists:

```typescript
} else if (isUnavailable && unavailableReason) {
  message = unavailableReason;
```

Staff expand/copy controls render only when **`detailsMessage !== message`** (line 562). With inline reasons, **`detailsMessage === message`**, so staff controls never appear.

### Canonical contract (already in repo)

**`job-detail-analysis-tab-consistency.mdc`** (Loading / unavailable messaging):

> **Unavailable plots**: **`BokehEmbed`** shows the user-visible **`Unavailable — Data not available.`** status line for everyone; staff-only expandable detail uses **`unavailableReason`** from the API.

Same rule also forbids driving multiprecision **`isLoading`** from **`detailsLoading`** when light detail already carries plot/unavailable payloads — **`JobDetail.tsx` `PlotPanel`** already complies; the multiprecision JobDetail test should pass once **`BokehEmbed`** shows generic copy again.

### Stale unit tests

**`BokehEmbed.test.tsx`** currently asserts the **wrong** behavior:

- `shows unavailable reason directly for staff when reason is provided` — expects inline reason, **no** expand button
- `hides error detail UI for non-staff users` — expects inline reason visible to non-staff

These contradict **`job-detail-analysis-tab-consistency.mdc`** and the view-layer tests.

### Scope boundary

- **In scope:** `BokehEmbed.tsx` message/staff UX + **`BokehEmbed.test.tsx`** + verification of the six view tests.
- **Out of scope:** JobList histogram staff messaging (already asserts no staff controls on list thumbs — passes).
- **No backend/API change** — `unavailableReason` fields unchanged.

---

## 2. Approach

**Industry practice:** Progressive disclosure (WCAG-aligned) — researchers see a stable, non-technical status; operators/staff opt into diagnostic detail. Fix at the **shared component** layer so TypeDetail, HostDetail, JobDetail, and future surfaces stay consistent without per-view duplication.

### Step 1 — Fix `BokehEmbed` message logic

In **`hpcperfstats/site/frontend/src/components/BokehEmbed.tsx`**, adjust the `message` branch order:

| Condition | Public `message` | `detailsMessage` (staff panel / copy) |
|-----------|------------------|----------------------------------------|
| `isLoading` | `Loading ${plotName}…` | — (no staff controls) |
| `loadFailed && failureReason` | `failureReason` (embed failure — keep current) | same |
| `isUnavailable && unavailableReason` | **`Unavailable — Data not available.`** | `unavailableReason` |
| `isUnavailable` (no reason) | **`Unavailable — Data not available.`** | null |

Leave the existing staff control gate:

```typescript
isUnavailable && detailsMessage && canViewErrorDetails && detailsMessage !== message
```

After the change, staff with an API reason get generic status + expand/copy; non-staff see generic status only (reason not in visible text unless staff opens panel).

**Trade-off:** Staff see generic line first, then expand — matches existing TypeDetail/HostDetail/JobDetail test contract and researcher doc intent (`researcher-job-detail-doc-sync.mdc`).

### Step 2 — Rewrite `BokehEmbed.test.tsx` unavailable tests

Replace the two stale tests with:

1. **Non-staff + `unavailableReason`** — generic copy visible; reason string **absent**; no staff buttons.
2. **Staff + `unavailableReason`** — generic copy visible; **Show plot error details** + **Copy error detail** present; expand panel contains full reason.
3. Keep **`shows loading message while external plot query is still running`** — must not show Unavailable during load (`design-focused-spa-ux.mdc`, `interactive-ready-controls.mdc`).

Optional: assert copy button writes `unavailableReason` to clipboard (mock `navigator.clipboard`).

### Step 3 — View tests (no edits expected)

Re-run view tests — they already encode the canonical contract. If any assertion still fails, adjust only after confirming product intent with the rule above (prefer **product fix**, not weakening tests).

### Step 4 — Rules cross-link (minimal)

**No new domain rule** — contract already lives in **`job-detail-analysis-tab-consistency.mdc`**.

Add one bullet to **`design-focused-spa-ux.mdc`** → *Loading vs unavailable*:

- Unavailable Bokeh plots: generic public status; staff expandable API reason — **`job-detail-analysis-tab-consistency.mdc`**.

Optionally add an **Overlap** line in **`bokeh-layout-surface-split.mdc`** pointing to the same unavailable messaging rule.

---

## 3. Testing

Every fix gets regression coverage at the narrowest layer (`every-error-regression-test.mdc`, `test-first-discipline.mdc`).

| Test | Module | Contract |
|------|--------|----------|
| Non-staff generic unavailable | `BokehEmbed.test.tsx` | Reason not leaked in status text |
| Staff expand/copy | `BokehEmbed.test.tsx` | Controls render; panel has API reason |
| Multiprecision defer + unavailable | `JobDetail.test.tsx` | Generic copy during `detailsLoading`, not Loading… |
| Staff multiprecision controls | `JobDetail.test.tsx` | 2× expand/copy on Multiprecision tab |
| Type/Host detail | `TypeDetail.test.tsx`, `HostDetail.test.tsx` | Same staff/non-staff split |

### Pre-merge verification matrix

| Tier | Command |
|------|---------|
| Component unit | `npm test -- --run src/components/BokehEmbed.test.tsx` |
| View integration | `npm test -- --run src/views/__tests__/JobDetail.test.tsx src/views/__tests__/TypeDetail.test.tsx src/views/__tests__/HostDetail.test.tsx` |
| Interactive-ready regression | `npm test -- --run src/utils/interactive-ready-drift.test.ts src/views/__tests__/JobList.test.tsx` |
| Typecheck + build | `npm run typecheck && npm run build` |

Log under **`test_runs/bokeh_embed_unavailable_messaging_2026-06-15.md`** (command, exit codes, pass/fail counts).

---

## 4. Implementation

| Concern | Location |
|---------|----------|
| Public vs staff unavailable messaging | `hpcperfstats/site/frontend/src/components/BokehEmbed.tsx` |
| Component regression lock | `hpcperfstats/site/frontend/src/components/BokehEmbed.test.tsx` |
| View tests (verify only) | `JobDetail.test.tsx`, `TypeDetail.test.tsx`, `HostDetail.test.tsx` |
| Rule cross-link | `design-focused-spa-ux.mdc` (optional `bokeh-layout-surface-split.mdc`) |
| Verification log | `test_runs/bokeh_embed_unavailable_messaging_<date>.md` |

---

## 5. Cursor rules / docs sync

| Action | Rationale |
|--------|-----------|
| **No new `.mdc`** | Canonical text already in **`job-detail-analysis-tab-consistency.mdc`** |
| Update **`design-focused-spa-ux.mdc`** | One cross-link so SPA UX rule dispatches engineers to plot unavailable contract |
| **Researcher doc** | Only if manual review finds job-detail unavailable copy changed for researchers — generic message is already doc-friendly; likely **no change** to `docs/using-the-website-as-a-researcher.md` |

Triggered rules when implementing: **`job-detail-analysis-tab-consistency.mdc`**, **`design-focused-spa-ux.mdc`**, **`interactive-ready-controls.mdc`**, **`testing-best-practices.mdc`**, **`react-next-ts-cursor-rule.mdc`**.

---

## Invariants / edge cases

| Case | Expected |
|------|----------|
| `unavailableReason` null, no item | Generic unavailable; no staff controls |
| `unavailableReason` set, non-staff | Generic unavailable only |
| `unavailableReason` set, staff | Generic + expand/copy with full reason |
| `isLoadingExternal` true | Loading copy; never Unavailable |
| `loadFailed` with `failureReason` | Show failure text (embed error path unchanged) |
| JobDetail multiprecision + `detailsLoading` + reasons in light payload | Generic unavailable (not Loading…); tabs stay interactive |
| Long `unavailableReason` | Shown in staff panel only; public line stays generic |

---

## Final code review (mandatory before implementation close)

Per `plan-completion-gate.mdc` step 1 — senior pass on full diff and workflows:

- [ ] Correctness: non-staff never see API diagnostic strings in plot status
- [ ] Staff expand/copy and clipboard still work
- [ ] Loading vs unavailable unchanged (`interactive-ready-controls.mdc`)
- [ ] `BokehEmbed.test.tsx` matches view tests and `job-detail-analysis-tab-consistency.mdc`
- [ ] No duplicate messaging logic in TypeDetail/HostDetail/JobDetail views
- [ ] All six previously failing tests green; interactive-ready suite still green
- [ ] Typecheck + build pass

Fix anything found; re-run Vitest before close.

---

## Post-implementation review (required before close)

Per `plan-completion-gate.mdc` step 2 — write in chat when closing:

- [ ] **Why it works** — one paragraph, contracts cited
- [ ] **Edge cases** — ≥3 realistic failure modes named
- [ ] **Convention check** — tests, triggered rules, `test_runs/` logging
- [ ] Regression tests for fixed errors (`every-error-regression-test.mdc`)
- [ ] Test run logged under `test_runs/`
- [ ] `post-implementation-review` todo completed last

---

## Completion bar

Not complete until: BokehEmbed fix merged, unit + view tests executed and logged, rules cross-linked (or explicit no-op), senior review clean, structured chat self-review delivered.

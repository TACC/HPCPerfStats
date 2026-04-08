# Frontend Naming Incremental Plan

Apply this plan only when touching the listed files for feature work or bugfixes.

## Rename targets (local scope only)

- `hpcperfstats/site/frontend/src/Layout.jsx` -> `PageLayoutMain.jsx`
- `hpcperfstats/site/frontend/src/App.jsx` -> `PageAppMachine.jsx`
- `hpcperfstats/site/frontend/src/pages/JobList.jsx` -> `PageJobList.jsx`
- `hpcperfstats/site/frontend/src/pages/JobDetail.jsx` -> `PageJobDetail.jsx`
- `hpcperfstats/site/frontend/src/pages/Search.jsx` -> `PageSearch.jsx`

## Guardrails

- Rename only when the touched PR already modifies the target file.
- Update all imports and matching tests in the same change.
- Do not perform repo-wide naming churn without explicit request.
- Keep behavior unchanged; this is naming consistency only.

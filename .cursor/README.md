# Cursor rules layout

- **Canonical copies (edit these):** [`cursor-rules/`](../cursor-rules/) at the repository root. Commit changes there.
- **What Cursor loads:** [`.cursor/rules/`](rules/) — project-specific rules (`.mdc`). Most entries are **symlinks** into `cursor-rules/` so the IDE and git stay in sync. A few rules exist only here (variable-metadata and WCAG); edit those files in place.

When adding a new workspace rule, add the `.mdc` file under `cursor-rules/`, then create a symlink in `.cursor/rules/` if it is not picked up automatically:

```bash
cd .cursor/rules && ln -s ../../cursor-rules/your-rule.mdc .
```

## SPA component naming

A wholesale rename of React files to strict `Page*` / `Button*` prefixes is **deferred**; follow incremental adoption in `cursor-rules/react-js-cursor-rule.mdc` when editing UI code.

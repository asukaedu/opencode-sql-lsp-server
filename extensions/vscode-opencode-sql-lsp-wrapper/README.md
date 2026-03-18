# OpenCode SQL LSP VS Code wrapper

This directory contains a minimal VS Code extension wrapper that launches the existing Python language server with:

```bash
opencode-sql-lsp --stdio
```

## Requirements

- VS Code desktop
- Node.js/npm to build/package the extension
- `opencode-sql-lsp` installed in the same environment where the workspace extension runs

## Commands

```bash
npm ci
npm run compile
npm run verify
```

## Install the packaged extension

After `npm run verify`, install the generated VSIX from:

```text
dist/opencode-sql-lsp-wrapper.vsix
```

In VS Code: `Extensions: Install from VSIX...`

## Notes

- The wrapper is intentionally thin and does not bundle Python or the language server.
- The wrapper bundles its TypeScript client code for a smaller Marketplace package, but it still launches the external Python server process unchanged.
- If `opencode-sql-lsp` is not on `PATH`, set `opencodeSql.serverPath` in VS Code settings.
- Wrapper artifacts stay inside this directory so the Python package build under repo-root `dist/` remains unchanged.

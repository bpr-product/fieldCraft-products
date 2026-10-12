# Verification performed on focused extraction

- Python syntax: **510 of 510 Python files parsed successfully** via `ast.parse`.
- React/TypeScript source: **234 of 234 JS/TS/TSX/JSX files parsed successfully** using TypeScript 5.8.3 `createSourceFile`.
- Original frontend package manifest: valid JSON.
- Relative frontend imports for the selected starting modules: no unresolved imports found in the lexical import traversal used for extraction.
- Existing QBO/ABC/EagleView FastAPI source entrypoint: unrelated CalBridge, tenant reset and other-provider routes were removed; Python parse succeeds.
- **NOT verified**: frontend bundler build and runtime; Java Gradle compile; end-to-end API tests; provider OAuth; tenant database connectivity; distributed ETL execution; AI inference; UI state that depends on missing app shell.
- Python integration API contains direct references to two absent QuickBooks test-service names; these names are also absent in the supplied full original integration service and require a follow-up fix. No full runtime guarantee is made.

A focused extract is useful for code review and portfolio evidence but requires additional modularization to run independently. These source files should not be advertised as a complete production-equivalent application.

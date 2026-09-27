# Working with this project

Use Graphify first for architecture and code relationship analysis:

```sh
graphify extract . --code-only
graphify query "<question>"
graphify affected <changed-file>
graphify god-nodes
graphify explain <path>::<symbol>
```

Rebuild the graph after structural changes. Verify graph findings against source before edits; templates and runtime relationships may be absent from the AST graph. Keep graphify-out/ untracked. Never commit credentials, local databases, or generated exports.

The application is FastAPI + Jinja2 + SQLite. Preserve existing API contracts and authentication. User-facing text is Russian. Dashboard data must come from real APIs, with explicit loading, empty, and error states. Test changes using an isolated temporary database.

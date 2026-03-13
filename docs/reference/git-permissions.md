# Git permission levels

The `git` parameter on [`ctx.agent()`](ctx.md#ctxagent) controls what GitHub API scopes the
agent's token receives. When `git` is `None`, the agent gets no GitHub token
and no repo is cloned.

| Level | GitHub permissions | Description |
|---|---|---|
| `"read"` | `contents: read` | Clone and read repository contents. |
| `"post"` | `contents: read`, `pull_requests: write`, `issues: write` | Read contents, create/update PRs and issues. |
| `"write"` | `contents: write`, `pull_requests: write` | Push commits, create/update PRs. |

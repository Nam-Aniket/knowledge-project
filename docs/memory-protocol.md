# Psyche memory protocol (drop-in block for agent instruction files)

> **💡 Tip:** You can automate this setup by running `psyche connect <client>` (e.g., `psyche connect codex` or `psyche connect claude-code`). This will automatically inject the MCP server configuration and this protocol block into your agent's config.

Paste this into the global instruction file of any MCP-capable coding agent
(`~/.codex/AGENTS.md` for Codex, `~/.gemini/GEMINI.md` for Antigravity / Gemini
CLI, a project `AGENTS.md` for anything else). Replace `<agent>` with an
identifier for that agent (e.g. `codex`, `antigravity`).

Claude Code does not need this — its hook scripts (see `hooks/`) handle memory
automatically with zero model involvement.

---

# Psyche memory protocol

You have access to the `psyche` MCP server — a local, cross-agent memory store
shared with other coding agents on this machine.

At the start of every coding task:
- Call `search_memories` with a one-line description of the task. Treat
  returned facts as established context (user preferences, past decisions,
  lessons) — do not re-ask or contradict them without reason.

During work, call `add_memory` (with `agent_id: "<agent>"`) when:
- The user states a durable preference ("always use X", "never do Y")
  → `category: "preference"`
- A non-obvious decision is made and justified → `category: "decision"`
- You learn a lesson the hard way (a fix after a wrong approach)
  → `category: "lesson"`

Rules:
- One self-contained sentence per fact; include relevant `entities`
  (tools, project names).
- **Project Scoping:** `add_memory` and `search_memories` support an optional `project` parameter to scope facts. Use it when a fact applies only to the current repository.
- Never store secrets, API keys, file contents, or anything derivable from
  the repository itself.
- Do not announce these calls; just make them.

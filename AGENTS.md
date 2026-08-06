# AGENTS.md

## Project status

Early-stage scaffold: "YouTube Shorts analyzer and generator" (see `README.md`). No source code, language, dependencies, build system, or tests exist yet — the tree contains only `.gitkeep` files. Do not invent tooling or run package/test commands; they don't exist.

## Repository layout (intended architecture)

The directory tree was created as scaffolding and encodes the planned structure. Add new files in the matching area, not at the root:

- `prompts/` — LLM prompt content: `chains/` (multi-step sequences), `system/`, `templates/`, `user/`
- `workflows/` — orchestration: `core/`, `shared/`, `templates/`, `testing/`
- `assets/` — media: `audio/`, `branding/`, `fonts/`, `music/`
- `config/` — app configuration
- `docs/` — architecture notes, decision records, guides, roadmap
- `examples/`, `scripts/`, `backups/`, `logs/`

## Docs

- `docs/PROJECT_STATE.md` is the designated project state document (currently empty and untracked in git).
- `docs/architecture/`, `docs/decisions/`, `docs/guides/`, `docs/roadmap/` are the homes for their respective doc types.

## Conventions

- No `.gitignore` exists yet; keep build outputs and secrets out of commits.
- Commit style from history: lowercase conventional-ish messages (`chore: initialize project structure`).

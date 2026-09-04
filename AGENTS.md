# Project tooling

- Spec Kit is scoped to this repository. Its Codex skills live in `.agents/skills/`.
- Run the Specify CLI through `./scripts/specify <arguments>` from the repository root. This wrapper pins the version and keeps its environment and cache under `.cache/uv-speckit/`.
- Do not install Spec Kit globally, copy its skills to user-level directories, or add it to shell startup files.
- Use Spec Kit when requested; installation alone does not authorize creating product requirements or starting implementation.
- Setup and usage: `docs/spec-kit.md`.

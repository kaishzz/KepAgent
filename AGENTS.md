## KepAgent Project Conventions

These rules apply to the `E:\GitHubProjects\KepRepository\KepAgent` workspace.

## Common Requirements

- Before working in this project, first read the shared requirements file at `C:\Users\24854\.codex\AGENTS.md`.
- Treat `C:\Users\24854\.codex\AGENTS.md` as the common baseline instruction set for this project.
- Apply the shared requirements first, then apply the KepAgent-specific rules in this file.
- If a shared requirement conflicts with an explicit rule in this file, follow this file for work inside `E:\GitHubProjects\KepRepository\KepAgent`.
- Do not duplicate the shared baseline content here; `C:\Users\24854\.codex\AGENTS.md` is the source of truth for it.

### Canonical Path

- This repository's canonical local path is `E:\GitHubProjects\KepRepository\KepAgent`.
- If another workspace references `KepAgent`, assume it means this path unless the user explicitly says otherwise.

### Companion Website Repository

- The companion website/control-plane repository is `E:\GitHubProjects\WebSite\kepcs.kaish.cn`.
- When a task involves website-to-agent integration, RCON flow, command payload formats, audit display, or shared config expectations, inspect the website repo directly instead of asking for the path again.
- If a KepAgent change depends on a website change, treat it as a cross-repo task by default unless the user explicitly says to keep it agent-only.

### Example File Sync

- Example files and real files must stay structurally aligned.
- In this repo, the primary mappings are `.env.example` -> `.env` and `agent.example.yaml` -> `agent.yaml`.
- When an example file adds keys, fields, or variables, add the missing ones to the real file.
- When an example file removes obsolete keys or fields, remove the corresponding entries from the real file only if they are part of that same mirrored structure.
- When an example file renames or restructures keys or fields, apply the same name and structure changes to the real file while preserving the user's existing values whenever possible.
- Sync names, structure, and expected keys, but do not overwrite existing secrets, tokens, passwords, URLs, paths, or other user data unless explicitly asked.
- Never replace real values with example placeholder values just because the example file changed.
- Never print secret values unless the user explicitly asks.
- If the real config file does not exist, mention it and do not create it unless the task requires it.

### Completion Checks

- After touching example config files, explicitly verify whether the corresponding real config files were checked and synchronized.
- Mention clearly if a real config file was absent and therefore not updated.

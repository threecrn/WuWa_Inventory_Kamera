# Repository Custom Instructions

## Tooling & Package Management
- **Preferred Manager:** Use `uv` for managing dependencies and virtual environments.
- **Dependency Management:** Use `uv add <package>` instead of `pip install`.
- **Script Execution:** Run Python scripts using `uv run <script.py>` to ensure the correct environment is used.
- **One-off Tools:** Use `uvx` (the uv equivalent of npx) for running ephemeral CLI tools without installing them globally.
- **Environment:** Assume the presence of a `.venv` managed by `uv`.

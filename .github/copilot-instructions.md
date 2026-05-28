# Repository Custom Instructions

## Tooling & Package Management
- **Preferred Manager:** Use `uv` for managing dependencies and virtual environments.
- **Dependency Management:** Use `uv add <package>` instead of `pip install`.
- **Script Execution:** Run Python scripts using `uv run <script.py>` to ensure the correct environment is used.
- **One-off Tools:** Use `uvx` (the uv equivalent of npx) for running ephemeral CLI tools without installing them globally.
- **Environment:** Assume the presence of a `.venv` managed by `uv`.

## Activity log
- After a request, append a proposed git commit message to the *end* of scratchpad/copilot_changes.log in the format:
    ```
    [YYYY-MM-DD HH:MM:SS] Copilot
    <Subject of the commit message>

    - <key point 1 about the change>
    - <key point 2 about the change>
    - ...

    - <Any additional notes about the commit if any>
    ```

# Project Guidelines

## Project Overview & Workflow

- **Purpose**: Scripted Export plugins for **KDE Purpose** (`src/python/` and `src/bash/`), shared Python helpers in `src/python/common/purpose_common.py`.
- **Install**: Run `./install.sh` after changes. It **_copies_** (never symlinks) `metadata.json`, `main.*`, and `purpose_common.py` into `~/.local/share/kpackage/Purpose/<plugin>/`.
- **Tests**: Always run tests via `./tests/run.sh` (uses local fakes in `tests/fakes/` so uploads, **Klipper**, and **notification** calls never touch the network or live desktop).
- **KDE Purpose protocol invariants**:
  - Connect to `--server` **Unix socket** first before any code or import that can fail (exiting before connecting crashes the host app).
  - Read the **CBOR** request before running dependency or file checks (closing a socket with unread bytes resets it and drops the error reply).
  - Report failures with `"error": 100` (Spectacle treats `1` as user-cancelled).
  - Run **Klipper** and **notification** D-Bus calls (`busctl`) only **_after_** closing the Purpose socket.

## Markdown Guidelines

### Structure

- Use H1 only once for the document title, in Title Case.
- Use H2–H6 to structure the text.
- Use sentence case for all other headings (H2–H6), capitalizing only the first word and proper nouns or acronyms.
- Never number headings (no "1.", "3.5").
- Never put URLs or links in headings.
- Never put a divider line `---` before an H2 heading (preview themes usually draw one for H2 headings).
- Do not insert hard line breaks: keep each paragraph, list item, and table row on one line.

### Wording & explanations

- Short sentences, short paragraphs (2–4 sentences max).
- Explain the rationale: Clarify *why* technologies and architectural choices are used (e.g. why Unix sockets over `stdin`/`stdout`, why boundary strings in HTTP multipart).
- Concrete examples: Pair non-trivial concepts with minimal, realistic code snippets (Python, Bash, HTTP, ASCII bit diagrams).
- Prefer lists and tables over prose:
  - Use tables for dense multi-column structured data (token maps, byte layouts, error catalogs, fields).
  - Use single-level bullet lists when items require 1–2 explanatory sentences (easier to read than cramped table cells).
- Link key terms: Link foundational technologies to their canonical Wikipedia articles or RFC specifications in glossaries.

### Code blocks

- Always specify the language tag on fenced code blocks (`python`, `bash`, `http`, `json`, etc.).
- Keep code examples minimal and focused on the specific mechanism being explained.

### Emphasis

- **Bold** only for important technologies, tools and operations (e.g. **KDE Purpose**, **Unix socket**, **notification**, **error message**). Not for verbs or ordinary words.
- Bold+italic (`**_text_**`) for important facts and warnings (e.g. **_no documentation_**, **_never waits_**).
- Use emphasis sparingly; mainly on the first mention.

### Callouts

- Use GitHub callouts where suitable: `> [!note]`, `> [!tip]`, `> [!important]`, `> [!warning]`, `> [!caution]`.

### Diagrams

- Prefer **PlantUML** for diagrams.
- Save source files in `docs/diagrams/<name>.puml` and generated SVGs in `docs/diagrams/out/<name>.svg`.
- Render SVGs with `plantuml -tsvg -o out docs/diagrams/<name>.puml` and embed them as `![Description](diagrams/out/<name>.svg)`.
- Start every diagram with `!include _style.iuml` and use its semantic variables (`$OK`, `$FAIL`, `$EXTERNAL`).
- Do not add a `title` directive unless requested (the surrounding Markdown heading or caption is enough).
- Use `hide footbox` in sequence diagrams and `!pragma useVerticalIf on` in vertical activity branching.

## Bash Guidelines

### Formatting

- Indent with 4 spaces.
- Max line length for code: 88. Long strings and hard-to-break expressions may exceed it.
- Divider lines (`#---` or `#===`) are exactly 79 characters wide.
- Comment text inside headers wraps to fit within 88 characters.
- Add an empty line between `local` variable declarations and the rest of the function code.
- Add empty lines between distinct conditional statements and logical phases.
- Add short, meaningful comments inside function bodies where they clarify non-obvious logic, safety guards, or parameter expansions (avoid redundant comments on self-evident code).

### Script header

- Runnable scripts start with `#!/usr/bin/env bash`, then a header block: name and one-line title, `Description:`, `Usage:` (if applicable), and `Update:`.
- `Author:` is added **_only manually by the user_**. The AI must **_never add_** an `Author:` field if missing, and must leave existing `Author:` lines untouched.
- The AI updates only the `Update:` date field (`Update: YYYY-MM-DD`) when modifying files.

```bash
#!/usr/bin/env bash
#==============================================================================
# dot - Hybrid Dotfiles Manager
#==============================================================================
# Description:
# - A script for managing user configs (dotfiles) using either GNU Stow or
#   Rsync under the hood.
#
# Usage: dot COMMAND [MODULE] [FILE] [OPTIONS]
#
#------------------------------------------------------------------------------
# Author: midvok
# Update: 2026-09-26
#==============================================================================

# Bash strict mode
set -euo pipefail
```

### Usage help

- Implement a `usage()` function (wired to `-h, --help` and printing a short help text via heredoc) only when asked for it in the prompt.
- Place `usage()` near the top of the script (below strict mode and global declarations) so the CLI contract is immediately visible.
- Help text: one-line summary, `Usage:`, `Commands:` (if any), `Options:`.

```bash
#------------------------------------------------------------------------------
# HELP & USAGE
#------------------------------------------------------------------------------
usage() {
    cat <<EOF
A dotfiles manager wrapping Stow and Rsync for file deployment via symlinks or physical copies.

Usage: dot COMMAND [MODULE] [FILE] [OPTIONS]

Commands:
  list     [MODULE]     List all modules, or show file tracking status for a specific module
  deploy   MODULE       Deploy a module to \$HOME
  undeploy MODULE       Remove an active module from \$HOME
  pull     MODULE       Pull live changes from \$HOME back into the repository (Rsync only)
  add      MODULE FILE  Track a new file from \$HOME inside a module
  remove   MODULE FILE  Stop tracking a file and restore it safely to \$HOME

Options:
  -n, --dry-run         Preview the command execution without making actual changes
  -h, --help            Display this help message
EOF
}
```

### Function headers

- Non-trivial functions get a divider header: a block of `#---` comment lines (79 chars wide) with an uppercase title on the first line, then the description.
- Self-explanatory functions (e.g. `usage()`) use only the divider block and uppercase title, omitting redundant description text.
- Simple helper functions (e.g. `die()`, `warn()`) do not need individual divider headers. Group related helpers under a shared section divider (e.g. `# HELPERS`), or use an optional single-line `# ...` comment only when explanation is genuinely needed.
- Place `main()` at the bottom of the script under an `# ENTRY POINT` divider header, followed by `main "$@"`.

```bash
#------------------------------------------------------------------------------
# CMD STATUS
#------------------------------------------------------------------------------
# Analyzes the status of every file tracked in a module compared to $HOME.
#
# Rsync Modules:
# - Checks if physical files are ignored, identical, modified, or not deployed
#   (missing in $HOME).
#
# Stow Modules:
# - Checks if files are symlinked, conflicted (physical file exists),
#   or not linked.
#------------------------------------------------------------------------------
cmd_status() {}

#------------------------------------------------------------------------------
# HELPERS
#------------------------------------------------------------------------------
die() {}

warn() {}

#------------------------------------------------------------------------------
# ENTRY POINT
#------------------------------------------------------------------------------
main() {}

main "$@"
```

## Python Guidelines

### Formatting

- Indent with 4 spaces.
- Max line length for code: 88 (black-compatible).
- Divider lines (`#---`) are exactly 79 characters wide.
- Prefer double quotes `"`.
- Use type annotations where they make sense (always on function signatures).
- When wrapping long lines, put the closing bracket on its own line:

```python
def create_folders(
    folders: list[Path], root_path: Path, dry_run: bool = False
) -> tuple[int, int, int]:
```

### Module docstring

- Runnable scripts start with `#!/usr/bin/env python3`.
- Every script and module has a module docstring.
- Scripts with a CLI include `Usage:`, `Args:`, and `Examples:` sections.
- `Author:` is added **_only manually by the user_**. The AI must **_never add_** an `Author:` field if missing, and must leave existing `Author:` lines untouched.
- The AI updates only the `Update:` date field (`Update: YYYY-MM-DD`) when modifying files.

```python
#!/usr/bin/env python3
"""
This script reads a YAML file containing a folder structure and creates
the corresponding empty folders in the specified root directory.

Usage:
    python make_folders.py <yaml_file> <root_path> [options]

Args:
    yaml_file   Path to the YAML file containing the folder structure
    root_path   Root directory where folders should be created
    --dry-run   Optional flag to preview changes without creating folders
    -h, --help  Show help message and exit

Examples:
    python make_folders.py test/notes-vault.yaml test/output/notes-vault
"""
```

### Function docstrings and headers

- Every non-trivial function has a **Google-style docstring**: one-line summary, then `Args:`, `Returns:`, `Raises:` (omit sections that don't apply).
- Optional divider header above a function only for complex logic worth explaining.

```python
#------------------------------------------------------------------------------
# Optional header comment explaining the core folder creation logic.
#------------------------------------------------------------------------------
def create_folders(
    folders: list[Path], root_path: Path, dry_run: bool = False
) -> tuple[int, int, int]:
    """
    Create the given folders under the specified root path.

    Args:
        folders:   Paths of the folders to create.
        root_path: Root directory where the folders should be created.
        dry_run:   If True, only preview paths without creating directories.
    Returns:
        A tuple of (created, existing, errors) counts.
    Raises:
        OSError: If a folder cannot be created.
    """
```

- Simple helper functions may use a **one-line docstring** in imperative form instead of the full Google-style sections. Omit `Args:`, `Returns:`, and `Raises:` when they add no value.

```python
def kos_root() -> Path:
    """Return the pathname of the KOS root directory."""
```

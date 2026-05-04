"""Load prompt templates from the ``prompts/`` tree.

Templates are plain ``.txt`` files using Python ``str.format`` placeholders
(``{var_name}``). Categories correspond to subdirectories of ``prompts/``.
"""

from __future__ import annotations

from pathlib import Path

from shared import PROJECT_ROOT

_PROMPTS_DIR: Path = PROJECT_ROOT / "prompts"


class PromptError(RuntimeError):
    """Raised when a prompt cannot be loaded or rendered."""


def load_prompt(category: str, name: str, /, **kwargs: object) -> str:
    """Load and render the prompt template ``prompts/{category}/{name}.txt``.

    ``category`` and ``name`` are positional-only so that template variables
    named ``category`` or ``name`` don't collide with these parameters.

    Args:
        category: Subdirectory under ``prompts/`` (e.g. ``"geo_audit"``).
        name: Template filename without the ``.txt`` extension.
        **kwargs: Variables to substitute via :py:meth:`str.format`.

    Returns:
        The rendered template string.

    Raises:
        PromptError: If the template file is missing or a referenced
            variable wasn't supplied.
    """
    path = _PROMPTS_DIR / category / f"{name}.txt"
    if not path.is_file():
        raise PromptError(
            f"Prompt template not found: {path.relative_to(PROJECT_ROOT)}"
        )
    try:
        template = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"Could not read prompt {path}: {exc}") from exc

    try:
        return template.format(**kwargs)
    except KeyError as exc:
        missing = exc.args[0] if exc.args else "<unknown>"
        raise PromptError(
            f"Prompt {category}/{name} requires variable {{{missing}}} "
            f"but it was not provided."
        ) from exc
    except (IndexError, ValueError) as exc:
        raise PromptError(
            f"Prompt {category}/{name} has a malformed placeholder: {exc}"
        ) from exc


def list_prompts(category: str) -> list[str]:
    """Return the names of available prompts in ``category`` (sorted, no extension)."""
    cat_dir = _PROMPTS_DIR / category
    if not cat_dir.is_dir():
        return []
    return sorted(p.stem for p in cat_dir.glob("*.txt") if p.is_file())

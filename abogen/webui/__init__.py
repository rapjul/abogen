"""Abogen WebUI package."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import create_app

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    """Dynamically import and expose attributes to avoid circular imports.

    Args:
        name: Name of the attribute to retrieve.

    Returns:
        The imported attribute object.

    Raises:
        AttributeError: If the requested attribute name is unknown.
    """
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(name)

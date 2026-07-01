"""Jinja2 environment construction for packaged adapter templates."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, override

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PackageLoader,
    select_autoescape,
)

if TYPE_CHECKING:
    from my_usermanager.adapters.fastapi_htmx.config import UserManagerUiConfig

_PACKAGE_NAME = "my_usermanager.adapters.fastapi_htmx"


@dataclass(frozen=True, slots=True)
class TemplateLoaderConfigError(ValueError):
    """Raised when mutually exclusive template loader settings are supplied."""

    @override
    def __str__(self) -> str:
        """Return a stable template configuration error."""
        return "set either template_loader or template_override_directory, not both"


def create_template_environment(config: UserManagerUiConfig) -> Environment:
    """Create a Jinja environment using host overrides before packaged templates."""
    if config.template_loader is not None:
        if config.template_override_directory is not None:
            raise TemplateLoaderConfigError
        loader = config.template_loader
    elif config.template_override_directory is not None:
        loader = ChoiceLoader(
            [
                FileSystemLoader(config.template_override_directory),
                PackageLoader(_PACKAGE_NAME, "templates"),
            ],
        )
    else:
        loader = PackageLoader(_PACKAGE_NAME, "templates")
    environment = Environment(
        loader=loader,
        autoescape=select_autoescape(("html", "xml")),
    )
    environment.filters["html_escape"] = _html_escape
    return environment


def _html_escape(value: str) -> str:
    return escape(value, quote=True)

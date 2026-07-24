"""Jinja environment construction for packaged adapter templates."""

from __future__ import annotations

from html import escape

from app_factory.jinja import configure_jinja_env
from jinja2 import ChoiceLoader, Environment, PackageLoader, select_autoescape


def create_template_environment(_config: object | None = None) -> Environment:
    """Build the adapter environment using app-factory's canonical shell."""
    environment = Environment(
        loader=ChoiceLoader(
            [
                PackageLoader("my_usermanager.adapters.fastapi_htmx", "templates"),
            ]
        ),
        autoescape=select_autoescape(("html", "xml")),
    )
    _ = configure_jinja_env(environment)

    def html_escape(value: object) -> str:
        return escape(str(value), quote=True)

    environment.filters["html_escape"] = html_escape
    return environment

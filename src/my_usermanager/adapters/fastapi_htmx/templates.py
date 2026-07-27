"""Jinja environment construction for packaged adapter templates."""

from __future__ import annotations

from html import escape
from typing import Final

from app_factory.jinja import configure_jinja_env
from jinja2 import ChoiceLoader, Environment, PackageLoader, select_autoescape

_PACKAGE_TEMPLATE_PACKAGE: Final = "my_usermanager.adapters.fastapi_htmx"
_PACKAGE_TEMPLATE_DIR: Final = "templates"
_ATTACHED_FLAG: Final = "_my_usermanager_package_templates"


def package_template_loader() -> PackageLoader:
    """Return the loader for packaged account/admin templates."""
    return PackageLoader(_PACKAGE_TEMPLATE_PACKAGE, _PACKAGE_TEMPLATE_DIR)


def create_template_environment(_config: object | None = None) -> Environment:
    """Build the adapter environment using app-factory's canonical shell."""
    environment = Environment(
        loader=ChoiceLoader([package_template_loader()]),
        autoescape=select_autoescape(("html", "xml")),
    )
    _ = configure_jinja_env(environment)
    _install_filters(environment)
    setattr(environment, _ATTACHED_FLAG, True)
    return environment


def attach_package_templates(environment: Environment) -> Environment:
    """Ensure packaged templates resolve on a host Jinja environment.

    Host loaders stay first so host names (for example a product ``base.html``)
    win collisions. Package-only paths such as ``users/list.html`` still resolve
    through the appended package loader. Idempotent.
    """
    if getattr(environment, _ATTACHED_FLAG, False):
        return environment
    package_loader = package_template_loader()
    existing = environment.loader
    if existing is None:
        environment.loader = package_loader
    else:
        environment.loader = ChoiceLoader([existing, package_loader])
    _ = configure_jinja_env(environment)
    _install_filters(environment)
    setattr(environment, _ATTACHED_FLAG, True)
    return environment


def _install_filters(environment: Environment) -> None:
    def html_escape(value: object) -> str:
        return escape(str(value), quote=True)

    environment.filters["html_escape"] = html_escape

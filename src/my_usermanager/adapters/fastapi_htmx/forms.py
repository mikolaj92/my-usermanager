"""URL-encoded form parsing for mutating user-manager actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import parse_qs

from fastapi import Request, status

_FORM_CONTENT_TYPE: Final = "application/x-www-form-urlencoded"


@dataclass(frozen=True, slots=True)
class MutationForm:
    """Parsed mutating form payload."""

    user_id: str
    csrf_token: str | None


@dataclass(frozen=True, slots=True)
class GrantForm:
    """Parsed grant/revoke form payload."""

    user_id: str
    value: str
    scope_type: str | None = None
    scope_id: str | None = None
    csrf_token: str | None = None


@dataclass(frozen=True, slots=True)
class FormError:
    """Typed form parsing error for HTML rendering."""

    status_code: int
    title: str
    message: str


type MutationFormResult = MutationForm | FormError
type GrantFormResult = GrantForm | FormError


async def read_mutation_form(request: Request) -> MutationFormResult:
    """Parse the hidden user_id field from a URL-encoded form body."""
    form = await _read_form_values(request)
    if isinstance(form, FormError):
        return form
    user_id = _first_value(form, "user_id")
    if user_id is None:
        return FormError(
            status.HTTP_400_BAD_REQUEST,
            "Missing user id",
            "The submitted user action did not include a user id.",
        )
    return MutationForm(user_id, _csrf_value(form))


async def read_grant_form(request: Request, *, value_field: str) -> GrantFormResult:
    """Parse a role/capability grant form."""
    form = await _read_form_values(request)
    if isinstance(form, FormError):
        return form
    user_id = _first_value(form, "user_id")
    if user_id is None:
        return FormError(
            status.HTTP_400_BAD_REQUEST,
            "Missing user id",
            "The submitted grant action did not include a user id.",
        )
    value = _first_value(form, value_field)
    if value is None:
        return FormError(
            status.HTTP_400_BAD_REQUEST,
            "Missing grant value",
            "The submitted grant action did not include the selected grant.",
        )
    return GrantForm(
        user_id=user_id,
        value=value,
        scope_type=_optional_value(form, "scope_type"),
        scope_id=_optional_value(form, "scope_id"),
        csrf_token=_csrf_value(form),
    )


async def _read_form_values(request: Request) -> dict[str, list[str]] | FormError:
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0]
    if content_type.casefold() != _FORM_CONTENT_TYPE:
        return FormError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Unsupported form encoding",
            "Submit user actions as application/x-www-form-urlencoded.",
        )
    try:
        body = (await request.body()).decode()
    except UnicodeDecodeError:
        return FormError(
            status.HTTP_400_BAD_REQUEST,
            "Malformed form body",
            "The submitted form body is not valid UTF-8.",
        )
    try:
        return parse_qs(
            body,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
        )
    except UnicodeDecodeError:
        return FormError(
            status.HTTP_400_BAD_REQUEST,
            "Malformed form body",
            "The submitted form body is not valid UTF-8.",
        )


def _first_value(values: dict[str, list[str]], name: str) -> str | None:
    field_values = values.get(name, [])
    if field_values == [] or field_values[0] == "":
        return None
    return field_values[0]


def _csrf_value(values: dict[str, list[str]]) -> str | None:
    """Extract the authoritative standard CSRF field."""
    return _first_value(values, "csrf")


def _optional_value(values: dict[str, list[str]], name: str) -> str | None:
    field_value = _first_value(values, name)
    if field_value is None:
        return None
    return field_value

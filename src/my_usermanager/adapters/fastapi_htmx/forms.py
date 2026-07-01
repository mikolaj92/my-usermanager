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


@dataclass(frozen=True, slots=True)
class FormError:
    """Typed form parsing error for HTML rendering."""

    status_code: int
    title: str
    message: str


type FormResult = MutationForm | FormError


async def read_mutation_form(request: Request) -> FormResult:
    """Parse the hidden user_id field from a URL-encoded form body."""
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
        values = parse_qs(
            body,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
        ).get("user_id", [])
    except UnicodeDecodeError:
        return FormError(
            status.HTTP_400_BAD_REQUEST,
            "Malformed form body",
            "The submitted form body is not valid UTF-8.",
        )
    if values == [] or values[0] == "":
        return FormError(
            status.HTTP_400_BAD_REQUEST,
            "Missing user id",
            "The submitted user action did not include a user id.",
        )
    return MutationForm(values[0])

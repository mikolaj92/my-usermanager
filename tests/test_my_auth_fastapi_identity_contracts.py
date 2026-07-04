from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from my_usermanager.adapters import my_auth as my_auth_adapter
from my_usermanager.adapters.my_auth_fastapi_contracts import (
    assert_my_auth_fastapi_identity_contract,
)

if TYPE_CHECKING:
    import pytest


@dataclass(frozen=True, slots=True)
class FakePasskeyUser:
    user_id: str
    user_handle: bytes
    name: str
    display_name: str | None = None


class FakeMyAuthModule:
    PasskeyUser: ClassVar[type[FakePasskeyUser]] = FakePasskeyUser


def test_my_auth_fastapi_identity_contract_is_reusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a host app supplies only the my-auth PasskeyUser constructor.
    monkeypatch.setattr(my_auth_adapter, "import_module", import_fake_my_auth)

    # When: the shared contract exercises the FastAPI identity-linking adapter.
    assert_my_auth_fastapi_identity_contract()

    # Then: each registration/login link is explicit, idempotent, and grant-free.


def import_fake_my_auth(name: str, _package: str | None = None) -> FakeMyAuthModule:
    if name == "my_auth":
        return FakeMyAuthModule()
    raise ModuleNotFoundError(name)

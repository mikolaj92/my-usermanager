# Provider-aware account UI

Implementation slice of #127/#129, not a completed OIDC integration.

`my_usermanager.identity_capabilities` is dependency-free. Its frozen
`IdentityProviderCapabilities` describes credentials, recovery, provider profile,
reauthentication, sessions, revocation and sign-out entry pages. Each feature is
unavailable, local or delegated. These values are UI configuration, not evidence
of authentication, authorization, MFA, recovery or protocol logout support.

Configure `StandardUserManagerUiHooks(identity_providers=(...))` and pass the
hooks through `app_factory.adapters.install_identity_adapters` using only
`UserManagerBinding`. No dummy `PasskeyBinding` or provider SDK is required.
Standard hooks reload local user links before selecting provider descriptors;
unlinking hides the corresponding UI without modifying accounts or grants.

```python
from my_usermanager.identity_capabilities import (
    AccountCapability, IdentityProviderCapabilities,
)

company = IdentityProviderCapabilities(
    provider="company",  # matches the configured local ExternalIdentity key
    label="Company identity",
    credentials=AccountCapability(
        mode="provider",
        url="https://identity.example/account",
        trusted_origin="https://identity.example",
    ),
)
# StandardUserManagerUiHooks(..., identity_providers=(company,))
```

URLs must come from trusted application configuration, never claims or request
parameters. Only clean GET entry pages are accepted: no query, fragment,
userinfo, whitespace, percent escapes or backslashes. Provider links require
an exact configured HTTPS origin, including port. Local links are absolute
application-relative paths. Hosts must ensure configured entry pages exist;
mutations remain authenticated POSTs protected by CSRF behind those pages.
These restrictions deliberately exclude URLs carrying tokens. No network fetch
or discovery runs while constructing descriptors.

`identity_providers=None` retains legacy passkey-panel hooks. An explicit tuple
opts into provider-aware rendering; absent local credential capability suppresses
the passkey panel callback. Custom hooks may implement the optional
`account_identity_providers(request, current_user)` callback (sync or async).
They must likewise return only descriptors for current, authorized local links.

Local UM profile editing is separate from the provider's identity profile.
Local application logout remains unchanged and independent of remote support.
Provider sign-out links are options pages, not claims of global logout. Session
links identify whether they affect the application or provider. No front/back
channel logout or session revocation implementation is implied. UI capabilities
never replace endpoint permission checks or CSRF.

All feature captions use the existing config/request `labels` mechanism:
`identity_credentials`, `identity_recovery`, `identity_profile`,
`identity_reauthentication`, `identity_sessions`, `identity_revoke_sessions`,
`identity_logout`, `identity_provider_managed`, `identity_local_managed`,
`identity_provider_sessions`, `identity_application_sessions`.

Remaining #129 work is host logout/session behavior after unlinking a local
provider, not a third-party IdP runtime. The current full-page/HTMX tests cover
composition, delegated links, omission of unsupported features, two linked
providers and unlinking.

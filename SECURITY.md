# Security policy

## Report a vulnerability privately

Use [GitHub private vulnerability reporting](https://github.com/mikolaj92/my-usermanager/security/advisories/new).
Private reporting is enabled for this repository. Include the affected tag/commit,
a minimal reproduction, impact, and any known mitigation. Do not disclose tokens,
credentials, or unredacted production records in a public issue.

## Security boundary

my-usermanager provides local users, external identity links, authorization,
grants, account lifecycle, and optional adapters. The core must not own password
storage, passkey/WebAuthn ceremonies, OAuth/OIDC login, or session-token generation.
Hosts own cookie/CSRF policy, provisioning and linking decisions, and session
lifetime. A validated data model is not evidence of provider authentication.

## Versions and support

This is pre-1.0 software. The single source for the package version is
`pyproject.toml`; see [CHANGELOG.md](CHANGELOG.md) for changes and the
[app-factory compatibility matrix](https://github.com/mikolaj92/app-factory/blob/main/COMPAT.md)
for tested identity combinations.

Reports should identify the exact version. There is currently no promised
response-time SLA or blanket backport guarantee for older branches. Maintainers
will state affected versions, fixes, and any backports in the relevant advisory.
This policy does not declare older tags safe or silently mark them end-of-life.

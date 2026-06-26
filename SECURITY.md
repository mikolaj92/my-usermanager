# Security Policy

`my-usermanager` is pre-release software at version `0.1.0`.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately to the maintainers. Include affected versions, a minimal reproduction, expected impact, and any known mitigations.

## Security Boundary

This package is planned for authorization and user management only. It must not own password storage, passkey/WebAuthn ceremonies, OAuth/OIDC login, or session-token generation.

# API Compatibility And Stability Policy

## Status
- Current maturity: pre-1.0.
- Goal: stabilize public API and release `v1.0.0` with compatibility guarantees.

## Public API Surface
The following are treated as public API:
- Symbols exported from `turbo/__init__.py`.
- Route decorators and runtime hooks on `Turbo` and `APIRouter`.
- Dependency marker and security helper APIs documented in `README.md`.
- OpenAPI output contract fields that are documented by TurboAPI.

Everything else is internal and may change without notice before 1.0.

## Versioning
- TurboAPI follows SemVer after `v1.0.0`.
- `MAJOR`: breaking public API changes.
- `MINOR`: backward-compatible features.
- `PATCH`: backward-compatible fixes.

## Deprecation Policy (Post-1.0)
- Deprecations are announced in release notes and docs.
- Deprecated behavior remains available for at least two minor releases.
- Deprecations emit runtime warnings where feasible.
- Removals happen only in a major release.

## Security Compatibility
- Security helper behavior and OpenAPI security metadata are public API.
- Claim parsing, scope enforcement semantics, and failure status codes are maintained unless explicitly versioned in a major release.

## Migration Requirements
Breaking changes must include:
- Clear migration section in release notes.
- Before/after code examples.
- Any required config or env variable changes.

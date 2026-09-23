---
status: accepted
---
# The redaction secret set derives from the Settings schema

ADR-0008 requires every configured secret to be redacted, but the list of secrets was maintained by hand in three places — `Settings.secret_values()`, `tests/unit/test_settings.py`, and `tests/contract/test_providers.py` — so adding a credential meant remembering all three, and forgetting `secret_values()` would silently drop redaction while the hand-copied tests stayed green (the design review's C7: a fail-open drift).

`secret_values()` now derives from `Settings.model_fields`: every field annotated `SecretStr` contributes its configured value (`src/jev_judge_mcp/settings.py`). Two test dimensions keep it fail-closed, and both derive independently from the schema (`tests/support/secrets.py`), never by calling the production method:

- **Coverage:** every `SecretStr` field's marker value survives nowhere in a `Redactor`-processed error string — `tests/unit/test_settings.py::test_every_secret_field_participates_in_redaction`, and the provider suite's echo matrix (`test_providers.py`), whose echoed and asserted marker sets now auto-extend to fields the hand list has not grown to cover.
- **The inverse invariant:** any field named like a credential (`*_key`, `*_token`, `*_secret`, `*_password`, and `*_base_url` per ADR-0008) must be `SecretStr` — `test_credential_named_fields_are_secret_str` — so a plain-`str` credential cannot slip past the derivation in the first place.

## Consequences

- A new credential field is redacted and tested the moment it is declared `SecretStr`.
- Declaring one as `str` fails the naming guard instead of silently un-redacting it.
- The same change deletes the empty `telemetry.py` placeholder (the review's C6): CI stages pass on absent files, and P9 introduces a telemetry port only when two real implementations exist.

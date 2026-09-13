# Independent authentication migration review — 2026-09-14

Reviewed the Passlib-to-bcrypt replacement authored by another agent and the root-authored CSRF log redaction. No production source edits were made by this reviewer. Added three focused CSRF regression tests in `backend/tests/test_csrf_secret_logging.py`.

## Password hashing

`backend/tools/auth.py` now uses direct bcrypt 5.0.0. New hashes use independently generated salts and work factor 12; verification uses `bcrypt.checkpw`, preserving the cryptographic comparison implementation. A common input conversion enforces valid UTF-8, at most 72 **encoded bytes**, and no NUL. Non-string input, malformed stored hashes, invalid encoding and oversized passwords return authentication failure through `verify_password`; invalid password updates leave the existing hash unchanged. Creation validates/hashes before inserting the new user.

The tests include a legacy known-answer fixture independently generated through the older Passlib implementation, successful `$2a$`/`$2b$`/`$2y$` verification, wrong-password rejection, malformed hash rejection, cost/salt properties and Unicode byte-boundary behavior. These are stronger evidence than a new implementation checking only its own generated hashes. They do not prove compatibility with every historical buggy bcrypt implementation.

Pydantic password validators are attached to both registration/login models. The FastAPI boundary test uses the real registration model and verifies an oversized request returns 422 without reaching its handler. This proves that model boundary, not every deployed authentication endpoint or consumer of this helper. No login bypass or silent input truncation was introduced.

**Compatibility boundary:** old Passlib bcrypt configurations could silently truncate passwords beyond 72 bytes; the new implementation intentionally rejects them. Existing users enrolled with such passwords may need a verified password-reset process. This is not universal backward compatibility, and silently truncating again would restore ambiguous password equivalence. Empty-password policy, rate limiting, account enumeration, password-reset identity checks and production identity persistence were not strengthened by this migration and remain outside its validation scope.

## CSRF handshake secret

The production code diff only changes developer documentation and log wording. It retains `secrets.token_urlsafe(32)` for the ephemeral development secret, returns the generated value internally, retains the explicit environment value, and preserves refusal when `ENVIRONMENT=production` has no configured secret. The handshake still uses `hmac.compare_digest` against the per-process secret; CSRF token checks and browser/IPC binding logic are unchanged.

The developer can no longer obtain the generated secret by reading logs; a local launcher/operator needing a known value must explicitly supply `VOS3_TAURI_IPC_SECRET`. This is the intended exposure reduction, not an authentication bypass. The new tests verify production missing-secret refusal, no generated-secret value in logs, and preservation of an explicit production secret without logging it.

The guard is conditional on the actual deployment environment being production. Root is separately updating the backend image to set that environment explicitly. This review does not independently certify all launchers, JWT configuration paths, environment-secret strength or complete desktop origin policy.

## Validation

After the environment sync installed bcrypt 5.0.0 and email-validator 2.3.0 and removed Passlib, the independent combined run passed **19 tests**:

- 7 bcrypt migration tests.
- 3 new secret-resolution/logging regression tests.
- 9 existing CSRF tests, including correct/missing/wrong handshake and protected mutation behavior.

Command: `.venv-upgrade/bin/python -m pytest backend/tests/test_auth_bcrypt_migration.py backend/tests/test_csrf_secret_logging.py backend/tests/test_w3_3_csrf.py -n 0 -q --tb=short`.

Log: `/private/tmp/vos5-auth-csrf-independent-tests.log`. No hosted provider calls or real credential disclosure were needed. Review found no migration-specific security regression in the inspected changes; this is not a complete authentication-system certification.

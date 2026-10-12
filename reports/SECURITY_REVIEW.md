# Public publishing review

## Changes applied
- Started with the previously sanitized FieldCraft full-source candidate, not raw original archives.
- Excluded known environment files, key/certificate formats, build caches, private deployment scripts and unrelated services.
- Replaced remaining real-looking frontend Azure deployment URLs, legacy branded AI endpoints and OIDC IDs with environment-driven configuration and local development defaults.
- Removed an old private domain from an Eden test configuration.
- Removed unrelated router registrations from the Python integration gateway. All named provider endpoints are still included.
- A static scan found **no matches** for common high-confidence credential token formats (Google API key, GitHub token, AWS access-key ID, OpenAI secret key, private key block, Azure AccountKey, Slack token) and the specified old company-domain patterns. This is not proof the entire repository is safe.
- The 24 username/password connection-URI pattern matches are artificial PostgreSQL test fixtures using the literal `user` on host `host` and were retained to keep the unit tests representative.
- Other literal `password` / `token` matches are SQL bind placeholders, string composition, or mocked test inputs. They should still be manually checked.

## Before making public
1. Verify written permission covers these actual source files, included images, any third-party code and legal notices.
2. Manually inspect test data, credentials, Azure/Intuit configuration, hostnames, email addresses, internal identifiers and licensing headers. Static scans can miss sensitive data.
3. Rotate any known original production keys found during earlier migration; this ZIP cannot invalidate existing keys.
4. Run builds and integration tests using your own credentials and synthetic tenants; ensure services cannot accidentally call production systems.
5. Confirm branch diff before invoking `PUBLISH_REVIEWED.ps1`. The script replaces old FieldCraft demo files by design.

## Honest scope
- Full backend service boot and runtime dependencies are not verified.
- Company-rights statement is from the user, not independently checked.
- Publishing is not performed by creating this archive.

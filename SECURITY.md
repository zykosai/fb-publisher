# Security and privacy

FB Publisher stores Facebook browser sessions locally. Session files can provide
account access even without a password; treat `data/` as sensitive credentials.
The connection URL is also a credential. Do not share screenshots containing it.

## Reporting an issue

Do not post a working exploit, cookies, pairing key, database or personal data in
a public issue. Use GitHub's private vulnerability reporting if enabled in this
repository, or arrange a private channel with the maintainer before sharing details.
No response-time or security-support guarantee is provided.

## Deployment

- Default to localhost. Use a trusted private network/VPN for remote access.
- Do not expose the HTTP dashboard directly to the public internet.
- Restrict host access, keep dependencies updated and encrypt private backups.
- Do not distribute a release that has already been logged into Facebook.
- Browser automation is unofficial and does not guarantee protection from account restrictions.

If a secret was committed, removing it from the latest commit is not enough.
Revoke/rotate the credential and inspect the repository's history before sharing it.
The included release scanner is a guardrail, not a complete security audit.

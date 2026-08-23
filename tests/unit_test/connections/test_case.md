# External connection tests

- AES-256-GCM round trip, AAD ownership isolation, tamper detection, invalid keys.
- SQLite connection CRUD never exposes ciphertext and enforces owner checks.
- Personal SMTP rejects plaintext/mismatched ports; real SMTP is integration-only.
- Personal SMTP derives both the display sender and encrypted From address from the mailbox account, so users cannot create conflicting values.
- The runtime rejects legacy non-SMTP connection providers instead of silently using them.
- Explicit test sends report provider acceptance without claiming final delivery.

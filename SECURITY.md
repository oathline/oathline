# Security policy

Oathline decides whether an AI agent may act, so a bug here can be a security bug. Please report
problems privately.

## Reporting a vulnerability

- Use GitHub's **private vulnerability reporting**: the **Security** tab, then **Report a
  vulnerability**. Don't open a public issue.
- If private reporting isn't available to you, email **hello@nexxtnestgroup.com.au** with the subject
  "Oathline security".
- Include the version or commit, a minimal reproduction, and the impact you expect. For example:
  a capability ran without a grant, a token confirmed twice, a model field was treated as
  authority, or audit tampering went undetected.
- We aim to acknowledge within **3 business days** and to agree a fix and disclosure timeline
  with you within **10 business days**.
- Please give us a reasonable time to fix before disclosing publicly. We will credit you unless
  you prefer not to be named.

## Scope

In scope: everything in the `oathline/` package, namely the capability registry, validator,
confirmation tokens, audit log and engine.

Out of scope:
- Your executors, your authentication, and how you store the SQLite files.
- The documented limits in the README. Oathline does not authenticate people, and a chain that
  has been entirely rewritten can only be caught against an externally anchored head.

## Supported versions

Only the latest release on `main` receives fixes while the project is pre-1.0.

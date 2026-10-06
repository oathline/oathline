<!-- A security fix is not opened as a public pull request first. See SECURITY.md. -->

**What this changes**

**The risk** (which refusal, record or limit does it touch?)

**Tests added** (including the refusal cases)

### Checklist (from CONTRIBUTING.md)

- [ ] Standard library only. No runtime dependencies.
- [ ] Deterministic. Nothing in the decision path calls a model, the network or a random source that changes an outcome.
- [ ] Fail closed. When unsure, the code refuses and records why. No grant is widened implicitly.
- [ ] Every behaviour change comes with a test, including the refusal cases.
- [ ] The audit format is stable, or the change comes with a new `SCHEMA_VERSION` and a new chain.
- [ ] `python -m unittest discover -s tests -v` passes, and all four examples run.
- [ ] Every sentence added to the README or another document is true of the code. `ATTACKS.md` only lists attacks that a test proves fail.
- [ ] No secrets, real keys or real customer data, including in tests and examples.
- [ ] An issue was opened first for anything beyond a small fix.

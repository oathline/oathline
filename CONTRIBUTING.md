# Contributing to Oathline

Thanks for helping. Oathline is small on purpose, and every line in it is part of a safety
boundary, so the bar for changes is "clearly correct and clearly needed".

## Ground rules

- **Standard library only.** No runtime dependencies.
- **Deterministic.** Nothing in the decision path may call a model, the network or a random
  source that changes an outcome.
- **Fail closed.** When unsure, refuse and record why. Never widen a grant implicitly.
- **Every behaviour change comes with a test**, including the refusal cases.
- **Keep the audit format stable.** Changes to the canonical serialisation need a new
  `SCHEMA_VERSION` and a new chain, never an in-place migration.

## Workflow

1. Open an issue first for anything beyond a small fix, so we can agree the approach.
2. Fork, then branch from `main`.
3. Run the checks locally:
   ```bash
   python -m unittest discover -s tests -v
   python examples/quickstart.py
   python examples/llm_guard.py
   python examples/agent_proposes.py
   python examples/nine_seconds.py
   ```
4. Open a pull request describing the change, the risk and the tests added. CI runs the tests
   and examples on Linux, Windows and macOS (Python 3.10, 3.11, 3.12 and 3.13) plus a gitleaks scan of
   the full history.
5. Never commit secrets, real keys or real customer data, including in tests or examples.

## Reporting security issues

Please don't use issues or pull requests for vulnerabilities. See [SECURITY.md](SECURITY.md).
[ATTACKS.md](ATTACKS.md) lists the attacks that a test already proves fail.

## Licence

By contributing you agree that your contribution is licensed under the MIT licence in
[LICENSE](LICENSE).

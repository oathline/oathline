"""The documents point at real things: every ATTACKS.md row names a test and code lines that exist."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class AttacksTableTest(unittest.TestCase):
    def rows(self):
        return [line for line in read("ATTACKS.md").splitlines() if line.startswith("| ") and "::test_" in line]

    def test_every_row_names_an_existing_test_and_existing_code_lines(self):
        rows = self.rows()
        self.assertGreaterEqual(len(rows), 20)
        for row in rows:
            tests = re.findall(r"`(tests/\w+\.py)::(test_\w+)`", row)
            code = re.findall(r"`(oathline/\w+\.py):(\d+)(?:-(\d+))?`", row)
            self.assertTrue(tests and code, row)
            for path, name in tests:
                self.assertIn(f"    def {name}(self", read(path), row)
            for path, first, last in code:
                total = len(read(path).splitlines())
                self.assertTrue(1 <= int(first) <= int(last or first) <= total, row)

    def test_attacks_is_linked_from_readme_and_contributing(self):
        self.assertIn("/blob/main/ATTACKS.md)", read("README.md"))      # absolute: PyPI shows the README too
        self.assertIn("(ATTACKS.md)", read("CONTRIBUTING.md"))


class ReferenceTest(unittest.TestCase):
    def codes_in_the_code(self):
        engine, tokens = read("oathline", "engine.py"), read("oathline", "tokens.py")
        found = set(re.findall(r'"error": "(\w+)"', engine))
        found |= set(re.findall(r'_refuse(?:_confirm)?\([^)]*?, "(\w+)"', engine))
        found |= set(re.findall(r'return "(\w+_\w+)"', engine))
        found |= set(re.findall(r'else "(approver_proof_invalid|not_an_approver)"', engine))
        found |= set(re.findall(r'error="(\w+)"', tokens))
        found |= set(re.findall(r'else "(wrong_principal)"', tokens))
        return found

    def test_readme_lists_every_refusal_code_the_package_can_return(self):
        section = read("README.md").split("## Reference", 1)[1].split("\n## ", 1)[0]
        listed = set(re.findall(r"^\| `(\w+)` \|", section, flags=re.M))
        in_code = self.codes_in_the_code()
        self.assertGreater(len(in_code), 25)
        self.assertEqual(in_code - listed, set(), "codes the README does not list")
        self.assertEqual(listed - in_code, set(), "codes the README lists that the code never returns")

    def test_readme_lists_the_default_non_human_names_and_event_types(self):
        from oathline.engine import DEFAULT_NON_HUMAN
        readme = read("README.md")
        for name in DEFAULT_NON_HUMAN:
            self.assertIn(f"`{name}`", readme)
        section = readme.split("## Reference", 1)[1].split("\n## ", 1)[0]
        events = set(re.findall(r'append\(\s*"([A-Z_]+)"', read("oathline", "engine.py")))
        events |= set(re.findall(r'_record_outcome\("([A-Z_]+)"', read("oathline", "engine.py")))
        events |= {"PROPOSED", "PROPOSED_BY_AGENT"}
        for event in events:
            self.assertIn(f"`{event}`", section, event)


class SameWordsEverywhereTest(unittest.TestCase):
    CLOCK = ("A clock set back to inside the lifetime, before any confirmation has been refused as `expired`, "
             "makes the token live again.")

    def one_line(self, *parts):
        return " ".join(read(*parts).replace("\n    ", " ").replace("\n  ", " ").split())

    def test_the_clock_limit_is_stated_in_the_same_words_everywhere(self):
        for parts in (("README.md",), ("ATTACKS.md",), ("CHANGELOG.md",), ("oathline", "tokens.py")):
            self.assertIn(self.CLOCK, self.one_line(*parts), parts)

    def test_no_waitlist_and_one_line_about_agents(self):
        index = read("docs", "index.html")
        self.assertIn("Oathline Agents are in development and are not part of this repository.", index)
        for page in ("index.html", "guide.html", "privacy.html"):
            self.assertNotIn("waitlist", read("docs", page).lower(), page)
        self.assertNotIn("waitlist", read("README.md").lower())

    def test_the_incident_sentence_has_a_source_and_names_no_company(self):
        section = read("README.md").split("## A replay of the 9-second delete", 1)[1].split("\n## ", 1)[0]
        self.assertRegex(section, r"publicly reported.*\]\(https://[^)]+\)")

    def test_test_files_carry_no_process_wording(self):
        text = read("tests", "test_hardening.py").lower()
        for word in ("attacker", "checker", "stranger", "october", "reviewer", "independent review"):
            self.assertNotIn(word, text, word)

    def test_the_interrupt_rule_is_stated_in_changelog_and_readme(self):
        for parts in (("README.md",), ("CHANGELOG.md",)):
            text = self.one_line(*parts)
            for word in ("SystemExit", "KeyboardInterrupt", "asyncio.CancelledError", "GeneratorExit",
                         "approver_check_interrupted"):
                self.assertIn(word, text, (parts, word))


class VersionTest(unittest.TestCase):
    def test_version_is_the_same_everywhere(self):
        import oathline
        in_pyproject = re.search(r'^version = "([^"]+)"$', read("pyproject.toml"), flags=re.M).group(1)
        in_changelog = re.search(r"^## (\d+\.\d+\.\d+)", read("CHANGELOG.md"), flags=re.M).group(1)
        self.assertEqual({oathline.__version__, in_pyproject, in_changelog}, {"0.1.2"})

    def test_package_metadata_matches_the_repository(self):
        pyproject = read("pyproject.toml")
        self.assertIn('requires-python = ">=3.10"', pyproject)
        self.assertIn("dependencies = []", pyproject)
        self.assertIn('packages = ["oathline"]', pyproject)
        self.assertIn("MIT License", read("LICENSE"))
        for pruned in ("tests", "docs", "examples", ".github"):
            self.assertIn(f"prune {pruned}", read("MANIFEST.in"))


class RepositorySettingsTest(unittest.TestCase):
    def test_ci_workflow_is_pinned_read_only_bounded_and_wide(self):
        wf = read(".github", "workflows", "tests.yml")
        uses = re.findall(r"uses:\s*(\S+)", wf)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")         # a full commit hash, never a tag
        self.assertIn("permissions:\n  contents: read\n", wf)
        self.assertNotIn("write", wf)
        self.assertEqual(len(re.findall(r"timeout-minutes: \d+", wf)), len(re.findall(r"runs-on:", wf)))
        self.assertIn("cancel-in-progress: true", wf)
        for needed in ("ubuntu-latest", "windows-latest", "macos-latest", '"3.10"', '"3.11"', '"3.12"', '"3.13"'):
            self.assertIn(needed, wf)
        for example in ("quickstart", "llm_guard", "agent_proposes", "nine_seconds"):
            self.assertIn(f"python examples/{example}.py", wf)

    def test_community_files_exist_and_send_security_reports_to_the_policy(self):
        config = read(".github", "ISSUE_TEMPLATE", "config.yml")
        self.assertIn("blank_issues_enabled: false", config)
        self.assertIn("security/policy", config)
        for template in ("bug_report.md", "feature_request.md"):
            self.assertIn("SECURITY.md", read(".github", "ISSUE_TEMPLATE", template))
        pr = read(".github", "pull_request_template.md")
        for line in ("Standard library only", "Deterministic", "Fail closed", "comes with a test", "audit format"):
            self.assertIn(line, pr)
        self.assertIn("* @sarojsanjel", read(".github", "CODEOWNERS"))
        dependabot = read(".github", "dependabot.yml")
        self.assertIn('package-ecosystem: "github-actions"', dependabot)


class ReadmeNumbersTest(unittest.TestCase):
    def printed(self, code):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(code, {})
        return buf.getvalue()

    def test_secret_arguments_block_prints_what_the_readme_says(self):
        section = read("README.md").split("## Secret arguments", 1)[1].split("\n## ", 1)[0]
        code, shown = re.findall(r"```(?:python)?\n(.*?)```", section, flags=re.S)
        self.assertEqual(self.printed(code), shown)

    def test_approver_check_block_prints_what_the_readme_says(self):
        section = read("README.md").split("## Agent proposes, human confirms", 1)[1].split("\n## ", 1)[0]
        blocks = re.findall(r"```(?:python)?\n(.*?)```", section, flags=re.S)
        code, shown = blocks[-2], blocks[-1]
        self.assertIn("approver_check", code)
        self.assertEqual(self.printed(code), shown)

    def test_package_line_count_matches_the_readme(self):
        import glob
        files = glob.glob(os.path.join(ROOT, "oathline", "*.py"))
        lines = 0
        for f in files:
            with open(f, encoding="utf-8") as fh:
                lines += sum(1 for _ in fh)
        m = re.search(r"package is (\d+) lines of Python in (\d+) files", read("README.md"))
        self.assertIsNotNone(m)
        self.assertEqual((int(m.group(1)), int(m.group(2))), (lines, len(files)))

    def test_test_count_matches_the_readme(self):
        count = unittest.TestLoader().discover(os.path.join(ROOT, "tests")).countTestCases()
        readme = read("README.md")
        stated = re.findall(r"has (\d+) tests", readme) + re.findall(r"The (\d+) tests need no network", readme)
        self.assertEqual(stated, [str(count)] * 2)


if __name__ == "__main__":
    unittest.main()

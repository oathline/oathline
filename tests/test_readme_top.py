"""The ten-line example at the top of README.md runs exactly as printed and prints the expected output."""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class ReadmeTopExampleTest(unittest.TestCase):
    def test_the_ten_line_example_runs_and_prints_the_expected_output(self):
        readme = read("README.md")
        code = re.search(r"```python\n(.*?)```", readme, flags=re.S).group(1)
        self.assertLessEqual(len(code.strip().splitlines()), 10)
        expected = re.search(r"Expected output:\n\n```\n(.*?)```", readme, flags=re.S).group(1)
        run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.replace("\r\n", "\n"), expected)

    def test_the_readme_links_work_on_pypi_as_well_as_github(self):
        readme = read("README.md")
        relative = [m for m in re.findall(r"\]\(([^)]+)\)", readme)
                    if not m.startswith(("http://", "https://", "#", "mailto:"))]
        self.assertEqual(relative, [], "relative links do not resolve on PyPI")

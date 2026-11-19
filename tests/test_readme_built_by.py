"""Claude 01:15 Fri 20 Nov 2026: the README (also the PyPI page) says, in one line, how the package was built."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE = ("Built by Saroj Sanjel with AI coding agents: every change test-first, independently reviewed, "
        "approved by a human before release.")


class BuiltBy(unittest.TestCase):
    def test_the_line_is_in_the_readme_once_before_the_licence(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertEqual(readme.count(LINE), 1)
        self.assertLess(readme.index(LINE), readme.index("## Licence"))


if __name__ == "__main__":
    unittest.main()

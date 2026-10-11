"""Guard against Python 3.12+ f-string syntax in our Python 3.11 deployment tools."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class Python311CompatibilityTests(unittest.TestCase):
    def test_reconciler_no_backslashes_inside_fstring_expressions(self):
        # PEP 701 relaxed this rule in Python 3.12; Python 3.11 raises SyntaxError.
        source = (ROOT / "scripts" / "aurora-full-reconcile.py").read_text()
        tree = ast.parse(source)
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FormattedValue):
                expression = ast.get_source_segment(source, node.value) or ""
                if "\\" in expression:
                    violations.append((node.lineno, expression))
        self.assertEqual(violations, [], "Python 3.11 forbids backslashes inside f-string expressions")

    def test_project_get_conflict_message_preserved(self):
        import importlib.util
        import subprocess
        from unittest import mock
        target = ROOT / "scripts" / "aurora-full-reconcile.py"
        spec = importlib.util.spec_from_file_location("aurora_reconciler_compat_v91", target)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with mock.patch.object(mod, "cmd", return_value=subprocess.CompletedProcess([], 1, "", "X Conflict")):
            with self.assertRaisesRegex(RuntimeError, "conflict=True.*refusing project creation"):
                mod._project_detail_exists()


if __name__ == "__main__":
    unittest.main()

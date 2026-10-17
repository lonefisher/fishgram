import ast
from pathlib import Path
import re
import unittest


class UpgradeWorkflowGateTests(unittest.TestCase):
    def test_manual_validation_before_daily_enable_and_main_only(self):
        workflow = (Path(__file__).resolve().parents[1]
                    / '.github/workflows/upstream-candidate.yml').read_text()
        guard = re.search(r'^    if: (.+)$', workflow, re.MULTILINE).group(1)
        for ref in ('refs/heads/main', 'refs/heads/feature'):
            for event in ('workflow_dispatch', 'schedule', 'push', 'pull_request'):
                for enabled in ('', 'false', 'true'):
                    with self.subTest(ref=ref, event=event, enabled=enabled):
                        expression = guard
                        for name, value in (('github.ref', ref),
                                            ('github.event_name', event),
                                            ('vars.UPGRADE_APP_ENABLED', enabled)):
                            expression = expression.replace(name, repr(value))
                        expression = expression.replace('&&', ' and ').replace('||', ' or ')
                        tree = ast.parse(expression, mode='eval')
                        self.assertTrue(all(isinstance(node, (ast.Expression, ast.BoolOp,
                            ast.And, ast.Or, ast.Compare, ast.Eq, ast.Constant))
                            for node in ast.walk(tree)))
                        actual = eval(compile(tree, '<workflow gate>', 'eval'),
                                      {'__builtins__': {}}, {})
                        expected = ref == 'refs/heads/main' and (
                            event == 'workflow_dispatch' or
                            (event == 'schedule' and enabled == 'true'))
                        self.assertEqual(actual, expected)

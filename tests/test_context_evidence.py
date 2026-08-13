from pathlib import Path
import tempfile
import unittest

from rotbot.contexts import evidence


class ProjectDevelopmentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, content):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def inspect(self):
        return evidence.inspect_project_development_evidence(
            self.project, ("github.com/example/project",), "example"
        )

    def test_recursive_structure_ast_and_argparse_commands(self):
        self.write("src/example/core.py", "class Engine:\n    pass\n\ndef run():\n    pass\n")
        self.write(
            "src/example/parser.py",
            "def configure(commands):\n    commands.add_parser('debug')\n"
        )

        inspected = self.inspect()

        self.assertIn("src/example/core.py", inspected.structure)
        self.assertTrue(any("class Engine" in fact for fact in inspected.implementation_facts))
        self.assertTrue(any("function run" in fact for fact in inspected.implementation_facts))
        self.assertIn("debug", inspected.cli_commands)

    def test_ast_inspection_never_executes_source(self):
        marker = self.project / "executed"
        self.write(
            "main.py",
            f"open({str(marker)!r}, 'w').write('bad')\n\ndef main():\n    pass\n"
        )

        inspected = self.inspect()

        self.assertFalse(marker.exists())
        self.assertTrue(any("function main" in fact for fact in inspected.implementation_facts))

    def test_sensitive_symlink_generated_and_dependency_artifacts_are_excluded(self):
        self.write("src/real.py", "def safe():\n    pass\n")
        self.write("src/private.key", "private")
        self.write("src/rotbot_context_dump.txt", "generated")
        self.write("src/runtime.log", "generated")
        self.write("node_modules/pkg/index.js", "generated")
        outside = Path(self.temporary.name) / "outside.py"
        outside.write_text("def leaked(): pass", encoding="utf-8")
        (self.project / "src" / "linked.py").symlink_to(outside)

        inspected = self.inspect()
        rendered = repr(inspected)

        self.assertIn("src/real.py", rendered)
        for excluded in (
            "private.key", "rotbot_context_dump", "runtime.log", "node_modules",
            "linked.py", "leaked"
        ):
            self.assertNotIn(excluded, rendered)

    def test_readme_is_supporting_aggressively_bounded_and_does_not_dominate(self):
        self.write(
            "README.md",
            "# Example\n\nShort purpose.\n\n## Installation\n" + "setup details\n" * 1000
        )
        self.write("src/main.py", "def current_feature():\n    pass\n")

        inspected = self.inspect()
        identity = evidence.render_identity_evidence(inspected)
        state = evidence.render_state_evidence(inspected)

        self.assertLessEqual(
            len((inspected.documentation_intro or "").encode("utf-8")),
            evidence.MAX_DOCUMENTATION_BYTES
        )
        self.assertIn("SUPPORTING DOCUMENTATION", identity)
        self.assertIn("may lag implementation", identity)
        self.assertNotIn("setup details", identity)
        self.assertNotIn("Short purpose", state)
        self.assertIn("current_feature", state)
        self.assertLess(len(identity.encode("utf-8")), len(state.encode("utf-8")) + 2000)

    def test_identity_and_state_views_are_distinct_and_bounded(self):
        for index in range(100):
            self.write(
                f"src/module_{index}.py",
                f"class Component{index}:\n    pass\n\ndef feature_{index}():\n    pass\n"
            )
        inspected = self.inspect()

        identity = evidence.render_identity_evidence(inspected)
        state = evidence.render_state_evidence(inspected)

        self.assertNotEqual(identity, state)
        self.assertIn("PROJECT IDENTITY EVIDENCE", identity)
        self.assertIn("CURRENT IMPLEMENTATION EVIDENCE", state)
        self.assertLessEqual(
            len(identity.encode("utf-8")), evidence.MAX_IDENTITY_EVIDENCE_BYTES
        )
        self.assertLessEqual(
            len(state.encode("utf-8")), evidence.MAX_STATE_EVIDENCE_BYTES
        )
        self.assertLessEqual(
            len(identity.encode("utf-8")) + len(state.encode("utf-8")), 8_500
        )

    def test_non_python_project_retains_manifests_structure_and_entrypoints(self):
        self.write("package.json", '{"name": "example"}')
        self.write("src/index.js", "export function start() {}")

        inspected = self.inspect()
        state = evidence.render_state_evidence(inspected)

        self.assertEqual(inspected.project_type, "JavaScript/TypeScript application")
        self.assertIn("package.json", inspected.manifests)
        self.assertIn("src/index.js", inspected.entrypoints)
        self.assertIn("src/index.js", state)


if __name__ == "__main__":
    unittest.main()

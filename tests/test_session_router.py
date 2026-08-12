from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.session import router, shell


class InputRouterTests(unittest.TestCase):
    def route(self, line, executables=()):
        def which(name, path=None):
            return f"/bin/{name}" if name in executables else None

        with patch.object(shell.shutil, "which", side_effect=which):
            return router.route_input(line)

    def test_builtins_have_highest_implicit_priority(self):
        for command in ("cd /tmp", "pwd", "history", "status", "clear", "exit"):
            with self.subTest(command=command):
                self.assertEqual(self.route(command).kind, "builtin")

    def test_exact_and_malformed_rot_namespaces_stay_rot(self):
        self.assertEqual(self.route("git status", {"git"}).kind, "rot")
        self.assertEqual(self.route("git pusj", {"git"}).kind, "rot")
        self.assertEqual(self.route("context inspekt").kind, "rot")

    def test_executable_and_shell_builtin_route_to_shell(self):
        self.assertEqual(self.route("ls -lah", {"ls"}).kind, "shell")
        self.assertEqual(self.route("python --version", {"python"}).kind, "shell")
        self.assertEqual(self.route("echo hello").kind, "shell")

    def test_natural_language_and_overrides_are_deterministic(self):
        self.assertEqual(
            self.route("why is the resolver designed this way?"),
            router.Route("ai", "why is the resolver designed this way?")
        )
        self.assertEqual(
            self.route("? find a better structure", {"find"}),
            router.Route("ai", "find a better structure")
        )
        self.assertEqual(
            self.route("!git push"), router.Route("shell", "git push")
        )

    def test_conservative_first_token_typo_only_suggests(self):
        route = self.route("gti status")

        self.assertEqual(route.kind, "error")
        self.assertIn("Did you mean: git status?", route.value)


class ShellExecutionTests(unittest.TestCase):
    def test_shell_uses_session_cwd_environment_and_user_shell(self):
        environment = {"PATH": "/bin", "SHELL": "/bin/bash", "ROT_TEST": "yes"}
        with patch.dict(shell.os.environ, environment, clear=True), patch.object(
            shell.subprocess, "run"
        ) as run:
            run.return_value.returncode = 7
            result = shell.run_shell("printf test", Path("/work"))

        self.assertEqual(result, 7)
        self.assertEqual(run.call_args.args[0], "printf test")
        self.assertEqual(run.call_args.kwargs["cwd"], Path("/work"))
        self.assertEqual(run.call_args.kwargs["env"], environment)
        self.assertEqual(run.call_args.kwargs["executable"], "/bin/bash")
        self.assertTrue(run.call_args.kwargs["shell"])

    def test_pipes_and_redirection_use_real_shell_syntax(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = shell.run_shell(
                "printf 'alpha\\nbeta\\n' | grep beta > result.txt", root
            )

            self.assertEqual(result, 0)
            self.assertEqual(
                (root / "result.txt").read_text(encoding="utf-8"), "beta\n"
            )


if __name__ == "__main__":
    unittest.main()

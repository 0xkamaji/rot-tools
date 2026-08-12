import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rotbot.session import router, shell


class InputRouterTests(unittest.TestCase):
    def route(self, line, executables=()):
        def which(name, path=None):
            return f"/bin/{name}" if name in executables else None

        with patch.object(shell.shutil, "which", side_effect=which), patch.object(
            router, "available_executables", return_value=tuple(executables)
        ):
            return router.route_input(line)

    def test_builtins_have_highest_implicit_priority(self):
        for command in ("cd /tmp", "pwd", "history", "status", "clear", "exit"):
            with self.subTest(command=command):
                self.assertEqual(self.route(command).kind, "builtin")

    def test_exact_and_malformed_rot_namespaces_stay_rot(self):
        self.assertEqual(self.route("git status", {"git"}).kind, "rot")
        self.assertEqual(self.route("git pusj", {"git"}).kind, "rot")
        self.assertEqual(self.route("context inspekt").kind, "rot")
        self.assertEqual(self.route("ai sessions", {"ai"}).kind, "rot")
        self.assertEqual(self.route("ai sesions", {"ai"}).kind, "rot")

    def test_executable_and_shell_builtin_route_to_shell(self):
        self.assertEqual(self.route("ls -lah", {"ls"}).kind, "shell")
        self.assertEqual(self.route("python --version", {"python"}).kind, "shell")
        self.assertEqual(self.route("echo hello").kind, "shell")

    def test_shell_shape_flags_paths_pipes_and_redirects(self):
        cases = (
            ("ls -lah", {"ls"}),
            ('rg "foo" .', {"rg"}),
            ("python script.py", {"python"}),
            ('find . -name "*.py"', {"find"}),
            ("time python foo.py", {"time", "python"}),
            ("find . | head -20", {"find", "head"}),
            ("find . > result.txt", {"find"})
        )
        for line, executables in cases:
            with self.subTest(line=line):
                self.assertEqual(self.route(line, executables).kind, "shell")

    def test_english_like_executable_with_prose_shape_routes_to_ai(self):
        cases = (
            "find me a better design",
            "sort this architecture differently",
            "time for a different approach",
            "why is this difficult?",
            "explain the resolver",
            "this architecture feels weird"
        )
        executables = {"find", "sort", "time", "why", "explain"}
        for line in cases:
            with self.subTest(line=line):
                self.assertEqual(self.route(line, executables).kind, "ai")

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
        self.assertIn("Did you mean `git status`?", route.value)

    def test_shell_typos_suggest_real_executables_without_execution(self):
        for line, expected, executables in (
            ("ct rotbot.py", "cat rotbot.py", {"cat", "cut"}),
            ("grpe foo", "grep foo", {"grep"}),
            ("pyton --version", "python --version", {"python"}),
            ("systmctl status caddy", "systemctl status caddy", {"systemctl"})
        ):
            with self.subTest(line=line):
                route = self.route(line, executables)
                self.assertEqual(route.kind, "error")
                self.assertIn(f"Did you mean `{expected}`?", route.value)

    def test_weak_matches_fall_through_to_ai(self):
        self.assertEqual(
            self.route("architecture feels complicated", {"arch"}).kind,
            "ai"
        )

    def test_dynamic_path_executable_discovery_is_path_keyed(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "newtool"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            shell.available_executables.cache_clear()
            with patch.dict(os.environ, {"PATH": temporary}, clear=True):
                self.assertIn("newtool", shell.available_executables(temporary))
                self.assertEqual(router.route_input("newtool --version").kind, "shell")


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

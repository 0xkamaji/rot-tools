import argparse
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from rotbot import __main__ as rotbot
from rotbot.cli import parser as command_parser
from rotbot.integrations.signalrot import commands as signalrot


class Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        return False


class SignalRotStatusTests(unittest.TestCase):
    def run_status(self, urlopen_result):
        with patch.object(
            signalrot,
            "_repo_path",
            return_value=Path("/signalrot")
        ), patch.object(
            signalrot,
            "_validate_repo",
            return_value=True
        ), patch.object(
            signalrot,
            "urlopen",
            side_effect=urlopen_result
            if isinstance(urlopen_result, Exception)
            else None,
            return_value=urlopen_result
            if not isinstance(urlopen_result, Exception)
            else None
        ), patch.object(signalrot, "rot_say") as rot_say:
            result = signalrot.sr_status(argparse.Namespace())

        return result, "\n".join(call.args[0] for call in rot_say.call_args_list)

    def test_healthy_site_returns_zero_and_displays_status(self):
        result, output = self.run_status(Response(200))

        self.assertEqual(result, 0)
        self.assertIn("SIGNAL ROT STATUS", output)
        self.assertIn("State:    ONLINE", output)
        self.assertIn("HTTP:     200", output)

    def test_unreachable_site_returns_one_and_displays_offline_status(self):
        result, output = self.run_status(URLError("connection refused"))

        self.assertEqual(result, 1)
        self.assertIn("SIGNAL ROT STATUS", output)
        self.assertIn("State:    OFFLINE", output)
        self.assertIn("connection refused", output)

    def test_http_failure_returns_one_and_displays_error_status(self):
        error = HTTPError(
            "https://signalrot.net",
            503,
            "Service Unavailable",
            None,
            None
        )
        self.addCleanup(error.close)
        result, output = self.run_status(error)

        self.assertEqual(result, 1)
        self.assertIn("State:    ERROR", output)
        self.assertIn("HTTP:     503", output)

    def test_unsuccessful_status_response_returns_one(self):
        result, output = self.run_status(Response(503))

        self.assertEqual(result, 1)
        self.assertIn("State:    ERROR", output)
        self.assertIn("HTTP:     503", output)

    def test_missing_http_status_returns_two(self):
        result, output = self.run_status(Response(None))

        self.assertEqual(result, 2)
        self.assertIn("invalid HTTP status", output)

    def test_missing_configuration_returns_two_without_http_check(self):
        with patch.object(
            signalrot,
            "_repo_path",
            return_value=None
        ), patch.object(signalrot, "_validate_repo") as validate, patch.object(
            signalrot,
            "urlopen"
        ) as urlopen:
            result = signalrot.sr_status(argparse.Namespace())

        self.assertEqual(result, 2)
        validate.assert_not_called()
        urlopen.assert_not_called()

    def test_invalid_repository_returns_two_without_http_check(self):
        repository = Path("/signalrot")
        with patch.object(
            signalrot,
            "_repo_path",
            return_value=repository
        ), patch.object(
            signalrot,
            "_validate_repo",
            return_value=False
        ) as validate, patch.object(signalrot, "urlopen") as urlopen:
            result = signalrot.sr_status(argparse.Namespace())

        self.assertEqual(result, 2)
        validate.assert_called_once_with(repository)
        urlopen.assert_not_called()

    def test_status_code_survives_parser_and_main_dispatch(self):
        arguments = command_parser.parse_args(["sr", "status"])
        self.assertIs(arguments.func, command_parser.sr_status)

        with patch.object(
            signalrot,
            "_repo_path",
            return_value=Path("/signalrot")
        ), patch.object(
            signalrot,
            "_validate_repo",
            return_value=True
        ), patch.object(
            signalrot,
            "urlopen",
            side_effect=URLError("offline")
        ), patch.object(signalrot, "rot_say"), patch.object(
            rotbot,
            "parse_args",
            return_value=arguments
        ):
            result = rotbot.main()

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()

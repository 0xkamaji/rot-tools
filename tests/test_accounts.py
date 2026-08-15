import os
from pathlib import Path
import stat
import tempfile
import unittest

from rotbot.contexts import accounts


class AccountLoadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write(self, content):
        path = self.directory / "accounts.toml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_missing_accounts_file_returns_none(self):
        self.assertIsNone(accounts.load_accounts(self.directory))

    def test_valid_accounts_file_is_parsed(self):
        self.write(
            "[git]\n"
            'name = "Ada Lovelace"\n'
            'email = "ada@example.invalid"\n'
            "[github]\n"
            'username = "adacoder"\n'
            'default_visibility = "private"\n'
        )

        parsed = accounts.load_accounts(self.directory)

        self.assertEqual(parsed.git_name, "Ada Lovelace")
        self.assertEqual(parsed.git_email, "ada@example.invalid")
        self.assertEqual(parsed.github_username, "adacoder")
        self.assertEqual(parsed.github_default_visibility, "private")

    def test_git_only_accounts_parse(self):
        self.write(
            "[git]\n"
            'name = "Ada Lovelace"\n'
            'email = "ada@example.invalid"\n'
        )

        parsed = accounts.load_accounts(self.directory)

        self.assertEqual(parsed.git_name, "Ada Lovelace")
        self.assertEqual(parsed.git_email, "ada@example.invalid")
        self.assertEqual(parsed.github_username, "")
        self.assertEqual(parsed.github_default_visibility, "")

    def test_github_only_accounts_parse(self):
        self.write(
            "[github]\n"
            'username = "adacoder"\n'
            'default_visibility = "public"\n'
        )

        parsed = accounts.load_accounts(self.directory)

        self.assertEqual(parsed.git_name, "")
        self.assertEqual(parsed.github_username, "adacoder")
        self.assertEqual(parsed.github_default_visibility, "public")

    def test_empty_file_loads_as_empty_accounts(self):
        self.write("")

        parsed = accounts.load_accounts(self.directory)

        self.assertEqual(parsed.git_name, "")
        self.assertEqual(parsed.github_username, "")

    def test_unknown_keys_are_ignored(self):
        self.write(
            'token = "should-not-leak"\n'
            "[git]\n"
            'name = "Ada Lovelace"\n'
        )

        parsed = accounts.load_accounts(self.directory)

        self.assertEqual(parsed.git_name, "Ada Lovelace")
        self.assertEqual(parsed.github_username, "")

    def test_invalid_toml_raises(self):
        self.write("[git\nname = unclosed")

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)

    def test_non_table_document_raises(self):
        self.write('git = "not a table of tables"\n')

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)

    def test_git_section_not_a_table_raises(self):
        self.write('[git]\nname = "Ada"\n[github]\nusername = 42\n')

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)

    def test_github_section_not_a_table_raises(self):
        self.write('[github]\nusername = ["a", "b"]\n')

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)

    def test_control_characters_are_rejected(self):
        for content in (
            '[git]\nname = "Ada\u0007"\n',
            '[github]\nusername = "ada\u007f"\n'
        ):
            with self.subTest(content=content):
                self.write(content)
                with self.assertRaises(accounts.AccountError):
                    accounts.load_accounts(self.directory)

    def test_email_requires_an_at_sign(self):
        self.write('[git]\nemail = "not-an-email"\n')

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)

    def test_invalid_visibility_is_rejected(self):
        self.write('[github]\ndefault_visibility = "secret"\n')

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)

    def test_symlinked_accounts_file_is_rejected(self):
        target = Path(self.temporary_directory.name) / "elsewhere"
        target.write_text("[git]\nname = \"Ada\"\n", encoding="utf-8")
        link = self.directory / "accounts.toml"
        link.symlink_to(target)

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)

    def test_directory_named_accounts_toml_is_rejected(self):
        (self.directory / "accounts.toml").mkdir()

        with self.assertRaises(accounts.AccountError):
            accounts.load_accounts(self.directory)


class AccountWritingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_written_accounts_round_trip(self):
        original = accounts.AccountFile(
            git_name="Ada Lovelace",
            git_email="ada@example.invalid",
            github_username="adacoder",
            github_default_visibility="private"
        )

        accounts.write_accounts(self.directory, original)

        loaded = accounts.load_accounts(self.directory)
        self.assertEqual(loaded, original)

    def test_written_file_uses_restrictive_permissions(self):
        if os.name == "nt":
            self.skipTest("POSIX permissions are not portable on Windows")
        accounts.write_accounts(
            self.directory,
            accounts.AccountFile(
                git_name="Ada", git_email="ada@example.invalid"
            )
        )

        mode = stat.S_IMODE(
            (self.directory / "accounts.toml").stat().st_mode
        )
        self.assertEqual(mode, 0o600)

    def test_write_creates_missing_directories(self):
        destination = self.directory / "nested" / "user-root"

        accounts.write_accounts(
            destination,
            accounts.AccountFile(
                git_name="Ada", git_email="ada@example.invalid"
            )
        )

        self.assertTrue((destination / "accounts.toml").is_file())

    def test_write_validates_visibility(self):
        with self.assertRaises(accounts.AccountError):
            accounts.write_accounts(
                self.directory,
                accounts.AccountFile(
                    git_name="Ada",
                    git_email="ada@example.invalid",
                    github_default_visibility="secret"
                )
            )

    def test_write_validates_email(self):
        with self.assertRaises(accounts.AccountError):
            accounts.write_accounts(
                self.directory,
                accounts.AccountFile(
                    git_name="Ada", git_email="not-an-email"
                )
            )

    def test_written_content_contains_no_secret_keys(self):
        account = accounts.AccountFile(
            git_name="Ada",
            git_email="ada@example.invalid",
            github_username="adacoder",
            github_default_visibility="private"
        )

        accounts.write_accounts(self.directory, account)

        content = (self.directory / "accounts.toml").read_text(encoding="utf-8")
        for forbidden in (
            "token", "secret", "password", "passphrase", "apikey", "key =", "cookie"
        ):
            self.assertNotIn(forbidden, content.lower(), forbidden)


class AccountRenderingTests(unittest.TestCase):
    def test_render_accounts_produces_toml_tables(self):
        rendered = accounts.render_accounts(
            accounts.AccountFile(
                git_name="Ada Lovelace",
                git_email="ada@example.invalid",
                github_username="adacoder",
                github_default_visibility="private"
            )
        )

        self.assertEqual(
            rendered,
            '[git]\n'
            'name = "Ada Lovelace"\n'
            'email = "ada@example.invalid"\n'
            "\n"
            '[github]\n'
            'username = "adacoder"\n'
            'default_visibility = "private"\n'
        )

    def test_render_accounts_with_git_only(self):
        rendered = accounts.render_accounts(
            accounts.AccountFile(git_name="Ada", git_email="ada@example.invalid")
        )

        self.assertEqual(
            rendered,
            '[git]\n'
            'name = "Ada"\n'
            'email = "ada@example.invalid"\n'
        )

    def test_render_accounts_with_nothing_returns_empty(self):
        self.assertEqual(accounts.render_accounts(accounts.AccountFile()), "")


if __name__ == "__main__":
    unittest.main()
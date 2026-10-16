import unittest
from scripts.scan_public_repository import problem


class PublicRepoScanTests(unittest.TestCase):
    def test_private_extensions(self):
        self.assertEqual(problem("docs/source.pdf", b"PDF"), "BANNED_FILE_TYPE")

    def test_private_runtime_paths(self):
        self.assertEqual(problem("private/config.txt", b"hello"), "PRIVATE_RUNTIME_PATH")

    def test_private_key_pattern(self):
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        self.assertEqual(problem("notes.txt", marker.encode()), "POSSIBLE_SECRET_PATTERN")

    def test_no_real_session_cookie(self):
        cookie = "session_cookie" + "='abcdefghijklmnopqrstuvwxyz12345'"
        self.assertEqual(problem("notes.txt", cookie.encode()), "POSSIBLE_SECRET_PATTERN")

    def test_safe_example_passes(self):
        self.assertIsNone(problem("examples/profile.example.json",
                                  b'{"origin": "https://cms.example.invalid"}'))

    def test_binary_blocked(self):
        self.assertEqual(problem("notes.txt", b"foo\0bar"), "UNEXPECTED_BINARY_FILE")


if __name__ == "__main__":
    unittest.main()

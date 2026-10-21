"""A stack's compose file from a repository (gitsource.py): the address per forge, what is refused, and
that a token is never in an error."""
import io
import unittest
import urllib.error

import gitsource


class Opener:
    def __init__(self, body=b"services: {}\n", error=None):
        self.body, self.error, self.seen = body, error, None

    def __call__(self, req, timeout=None):
        self.seen = req
        if self.error:
            raise self.error
        return io.BytesIO(self.body)


class Url(unittest.TestCase):
    def test_per_forge(self):
        self.assertEqual(gitsource.raw_url("https://github.com/o/r", "main", "a/compose.yaml"),
                         "https://raw.githubusercontent.com/o/r/main/a/compose.yaml")
        self.assertEqual(gitsource.raw_url("https://git.example/pyke/infra.git", "dev", "c.yml"),
                         "https://git.example/pyke/infra/raw/branch/dev/c.yml")
        self.assertEqual(gitsource.raw_url("https://gitlab.example/g/sub/r", "main", "c.yml"),
                         "https://gitlab.example/g/sub/r/-/raw/main/c.yml")

    def test_plain_input_only(self):
        for repo, branch, path in (("ftp://x/o/r", "main", "c.yml"), ("https://x/o", "main", "c.yml"),
                                   ("file:///etc", "main", "passwd"), ("https://x/o/r", "../x", "c.yml"),
                                   ("https://x/o/r", "main", "../../etc/passwd"), ("https://x/o/r", "main", "/etc/passwd"),
                                   ("https://x/o/r", "main", "a//b"), ("https://x/o/r", "main", "a b"),
                                   ("https://x/o/r?x=1", "main", "c.yml"), ("https://u:p@x/o/r", "main", "c.yml")):
            with self.assertRaises(ValueError, msg=(repo, branch, path)):
                gitsource.raw_url(repo, branch, path)


class Fetch(unittest.TestCase):
    def test_text_and_token_header(self):
        op = Opener()
        self.assertEqual(gitsource.fetch("https://g.example/o/r", "main", "c.yml", "tok-12345678", opener=op), "services: {}\n")
        self.assertEqual(op.seen.get_header("Authorization"), "Bearer tok-12345678")
        op = Opener()
        gitsource.fetch("https://g.example/o/r", "main", "c.yml", opener=op)
        self.assertIsNone(op.seen.get_header("Authorization"))

    def test_errors_never_hold_the_token(self):
        token = "secret-token-1234"
        for err in (urllib.error.HTTPError("https://g.example/?t=" + token, 404, "nf", {}, None), urllib.error.URLError("boom " + token)):
            with self.assertRaises(ValueError) as cm:
                gitsource.fetch("https://g.example/o/r", "main", "c.yml", token, opener=Opener(error=err))
            self.assertNotIn(token, str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            gitsource.fetch("https://g.example/o/r", "main", "c.yml", "not a token!", opener=Opener())
        self.assertNotIn("not a token!", str(cm.exception))

    def test_size_and_text(self):
        with self.assertRaises(ValueError):
            gitsource.fetch("https://g.example/o/r", "main", "c.yml", opener=Opener(b"x" * (49 << 10)))
        with self.assertRaises(ValueError):
            gitsource.fetch("https://g.example/o/r", "main", "c.yml", opener=Opener(b"\xff\xfe\x00"))


if __name__ == "__main__":
    unittest.main()

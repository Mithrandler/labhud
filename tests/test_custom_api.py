"""Any JSON API as a source, without code: picked values and a list."""

import unittest

from sources import custom_json as cj

ANSWER = {"data": [{"stats": {"total": 7, "ok": 6}}], "issues": [{"title": "Fix it", "state": "open"},
                                                                  {"title": "Done", "state": "closed"}]}


class CustomApi(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(cj.path_get(ANSWER, "data.0.stats.total"), 7)
        self.assertEqual(cj.path_get(ANSWER, "issues.-1.title"), "Done")
        self.assertIsNone(cj.path_get(ANSWER, "data.5.stats"))
        self.assertIsNone(cj.path_get(ANSWER, "issues.title"))

    def test_shape(self):
        got = cj.shape(ANSWER, {"total": "data.0.stats.total", "ok": "data.0.stats.ok"}, "issues", "title", "state")
        self.assertEqual(got, {"total": 7, "ok": 6, "list": [{"name": "Fix it", "value": "open"},
                                                             {"name": "Done", "value": "closed"}]})

    def test_whole_object_or_refusal(self):
        self.assertEqual(cj.shape({"a": 1}), {"a": 1})
        with self.assertRaises(ValueError):
            cj.shape([1, 2])
        self.assertEqual(cj.shape([{"n": "x", "v": 1}], list_path="", pick={"first": "0.n"}), {"first": "x"})

    def test_settings(self):
        self.assertEqual(cj.parse_pick("a=x.y, b = z"), {"a": "x.y", "b": "z"})
        with self.assertRaises(SystemExit):
            cj.parse_pick("nopath")
        self.assertEqual(cj.parse_headers("X-Api-Key: k1; Accept: application/json"),
                         {"X-Api-Key": "k1", "Accept": "application/json"})


if __name__ == "__main__":
    unittest.main()

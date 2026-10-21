"""Routes added by the modules in api/ (routes.py): their guards, and one that is there."""

import json
import os
import unittest

os.environ.setdefault("LABHUD_DEMO", "1")
import routes  # noqa: E402
from tests.test_smoke import DemoServer  # noqa: E402


class Registry(unittest.TestCase):
    def test_guards(self):
        with self.assertRaises(ValueError):
            routes.route("GET", "/api/x/y", guard="manage")
        with self.assertRaises(ValueError):
            routes.route("POST", "/api/x/y", guard="public")
        with self.assertRaises(ValueError):
            routes.route("POST", "/not/api")

    def test_twice(self):
        routes.route("POST", "/api/test/twice")(lambda h, b: (200, {}))
        self.addCleanup(routes.ROUTES.pop, ("POST", "/api/test/twice"))
        with self.assertRaises(ValueError):
            routes.route("POST", "/api/test/twice")

    def test_loaded(self):
        import server
        self.assertIn("brand", server.ROUTE_MODULES)
        self.assertEqual(routes.find("GET", "/api/brand/about")[1], "public")


class Served(DemoServer):
    def test_public_route(self):
        code, _, body = self.get("/api/brand/about")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["name"], "LabHUD")

    def test_get_route_is_not_a_post_route(self):
        code = self.post_json("/api/brand/about", {})[0]
        self.assertEqual(code, 403)


if __name__ == "__main__":
    unittest.main()

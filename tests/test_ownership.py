import unittest
from pathlib import Path

from cloud_agent_poc.ownership import DEFAULT_USER_ID, normalize_user_id


class OwnershipContractTests(unittest.TestCase):
    def test_user_id_is_sanitized_for_headers_and_workspace_paths(self) -> None:
        self.assertEqual(normalize_user_id("Luca"), "Luca")
        self.assertEqual(normalize_user_id("Josephine Smith"), "Josephine_Smith")
        self.assertEqual(normalize_user_id("../bad/user"), "bad_user")
        self.assertEqual(normalize_user_id(""), DEFAULT_USER_ID)

    def test_user_facing_session_store_methods_scope_by_user_id(self) -> None:
        source = Path("src/cloud_agent_poc/session_store.py").read_text()

        self.assertIn("INSERT INTO sessions (id, user_id)", source)
        self.assertIn("sessions.user_id = %s", source)
        self.assertIn("WHERE id = %s\n                      AND user_id = %s", source)
        self.assertIn("AND (%s::text IS NULL OR user_id = %s)", source)
        self.assertIn("user_id=user_id", source)

    def test_web_forwards_user_ownership_to_session_layer(self) -> None:
        source = Path("src/cloud_agent_poc/web.py").read_text()

        self.assertIn('alias="X-User-Id"', source)
        self.assertIn("normalize_user_id", source)
        self.assertIn("user_id=user_id", source)
        self.assertIn("user_id=normalize_user_id(user_id)", source)

    def test_workspace_path_is_user_scoped(self) -> None:
        source = Path("src/cloud_agent_poc/sandbox_app.py").read_text()
        orchestrator = Path("src/cloud_agent_poc/brain/orchestrator.py").read_text()

        self.assertIn('f"users/{user_id}/{run_id}"', source)
        self.assertIn("normalize_user_id", source)
        self.assertIn("metadata={\"workspace_path\": workspace.path, \"user_id\": user_id}", orchestrator)


if __name__ == "__main__":
    unittest.main()

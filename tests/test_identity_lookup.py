"""Offline tests for the root-to-identity authorization helper.

Uses a fake Jac UserManager: no real auth server, Postgres, or MongoDB access.
"""
import sys
import types
import unittest
from unittest.mock import Mock

from services import identity


class IdentityLookupTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            k: sys.modules.get(k) for k in (
                "jaclang", "jaclang.server", "jaclang.server.identity",
                "jaclang.server.identity.user_manager"
            )
        }
        for name in self.saved:
            if name not in sys.modules:
                module = types.ModuleType(name)
                module.__path__ = []
                sys.modules[name] = module
        self.user_manager_module = sys.modules["jaclang.server.identity.user_manager"]

    def tearDown(self):
        for name, previous in self.saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def manager(self, users):
        self.user_manager_module.UserManager = lambda: Mock(
            list_all_users=lambda limit, offset: users[offset:offset+limit]
        )

    def test_owner_username_from_authenticated_root(self):
        self.manager([{
            "root_id": "abc-def", "status": "active",
            "identities": [{"type": "username", "value_normalized": "OWNER@example.edu"}]
        }])
        self.assertEqual(identity.email_for_root("abcdef"), "owner@example.edu")
        self.assertEqual(identity.email_for_root("some-other-root"), "")

    def test_email_identity_takes_priority(self):
        self.manager([{
            "root_id": "abc-def", "identities": [
                {"type": "username", "value_raw": "username@example.edu"},
                {"type": "email", "value_raw": "Preferred@Example.EDU"},
            ]
        }])
        self.assertEqual(identity.email_for_root("abc-def"), "preferred@example.edu")

    def test_disabled_user_fails_closed(self):
        self.manager([{
            "root_id": "abc-def", "status": "disabled",
            "identities": [{"type": "email", "value_raw": "owner@example.edu"}]
        }])
        self.assertEqual(identity.email_for_root("abc-def"), "")

    def test_multiple_pages(self):
        users = [{"root_id": str(i), "identities": []} for i in range(200)]
        users.append({
            "root_id": "target", "identities": [
                {"type": "email", "value_normalized": "staff@umich.edu"}
            ]
        })
        self.manager(users)
        self.assertEqual(identity.email_for_root("target"), "staff@umich.edu")

    def test_storage_failure_fails_closed(self):
        def fail():
            raise RuntimeError("DB unavailable")
        self.user_manager_module.UserManager = fail
        self.assertEqual(identity.email_for_root("abc-def"), "")

    def test_guest_and_missing_root(self):
        self.assertEqual(identity.email_for_root(""), "")
        self.assertEqual(identity.email_for_root(None), "")


if __name__ == "__main__":
    unittest.main()

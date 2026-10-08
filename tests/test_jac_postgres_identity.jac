"""Exercise the REAL Jac Postgres-backed UserManager in an ephemeral CI runner.

No live MongoDB credentials and no user data from production are needed.
"""
import importlib;
import from uuid { uuid4 }

glob auth_identity: any = importlib.import_module("services.identity"),
     usermod: any = importlib.import_module("jaclang.server.identity.user_manager");

with entry {
    manager = usermod.UserManager();
    addr = "ci-" + str(uuid4()).replace("-", "") + "@example.invalid";
    created = manager.create_user(addr, "CI-only-strong-password-123");
    assert created.get("user_id"), str(created);
    root_id = manager.get_root_id(str(created["user_id"]));
    assert root_id, "Jac did not issue a root id for the test account";
    assert auth_identity.email_for_root(root_id) == addr, "Root-to-email lookup failed";
    assert auth_identity.email_for_root("00000000-1111-2222-3333-444444444444") == "";
    print("PASS: Jac Postgres user, root id, and email lookup");
}

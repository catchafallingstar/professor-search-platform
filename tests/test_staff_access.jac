import importlib;
glob access: any = importlib.import_module("services.access");
import from unittest.mock { patch, MagicMock }

with entry {
    account = {"root_id": "abc-def", "identities": [{"type": "username", "value_normalized": "bxybai@umich.edu"}]};
    manager = MagicMock();
    manager.list_all_users.return_value = [account];
    with patch.object(access, "_is_guest", return_value=False), patch.object(access, "jid", return_value="abcdef"), patch.object(access, "UserManager", return_value=manager) {
        assert access.caller_email() == "bxybai@umich.edu";
        me = access.whoami("attacker@example.com");
        assert me.is_staff and me.is_owner;
        assert me.email == "bxybai@umich.edu";
        manager.list_all_users.return_value = [{"root_id": "abcdef", "identities": [{"type": "email", "value_normalized": "user@example.com"}]}];
        with patch.object(access, "_staff", return_value=[]) {
            me = access.whoami("bxybai@umich.edu");
            assert not me.is_staff and not me.is_owner;
            assert me.email == "user@example.com";
        }
        with patch.object(access, "_staff", return_value=["user@example.com"]) {
            me = access.whoami();
            assert me.is_staff;
        }
        manager.list_all_users.return_value = [];
        assert access.caller_email() == "";
    }
    with patch.object(access, "_is_guest", return_value=True) {
        assert access.caller_email() == "";
    }
    print("PASS: owner, staff, ordinary user, unknown identity, guest, and spoofed email");
}

import importlib;
glob access: any = importlib.import_module("services.access");
import from unittest.mock { patch }

with entry {
    with patch.object(access, "_is_guest", return_value=False),
         patch.object(access, "jid", return_value="abcdef"),
         patch.object(access.ident, "email_for_root", return_value="bxybai@umich.edu") as identity_lookup {
        me = access.whoami("attacker@example.com");
        assert me.is_staff and me.is_owner;
        assert me.email == "bxybai@umich.edu";
        identity_lookup.assert_called_with("abcdef");

        identity_lookup.return_value = "user@example.com";
        with patch.object(access, "_staff", return_value=[]) {
            me = access.whoami("bxybai@umich.edu");
            assert not me.is_staff and not me.is_owner;
            assert me.email == "user@example.com";
        }
        with patch.object(access, "_staff", return_value=["user@example.com"]) {
            me = access.whoami();
            assert me.is_staff and not me.is_owner;
        }

        identity_lookup.return_value = "";
        assert not access.whoami().is_staff;
    }
    with patch.object(access, "_is_guest", return_value=True) {
        assert access.caller_email() == "";
    }
    print("PASS: owner, staff, ordinary user, unknown identity, guest, spoofed email");
}

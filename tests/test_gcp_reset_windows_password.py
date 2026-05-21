import importlib.util
import pathlib
import sys
import types
import unittest


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "plugins/modules/gcp_reset_windows_password.py"


def load_module():
    basic = types.ModuleType("ansible.module_utils.basic")
    basic.AnsibleModule = object
    sys.modules.setdefault("ansible", types.ModuleType("ansible"))
    sys.modules.setdefault("ansible.module_utils", types.ModuleType("ansible.module_utils"))
    sys.modules["ansible.module_utils.basic"] = basic

    spec = importlib.util.spec_from_file_location("gcp_reset_windows_password", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResetWindowsPasswordCommandTests(unittest.TestCase):
    def test_omits_user_flag_when_user_is_not_set(self):
        module = load_module()

        cmd = module.build_reset_windows_password_cmd(
            instance="win-01",
            project="project-01",
            zone="us-east4-a",
            user=None,
        )

        self.assertNotIn("--user", cmd)
        self.assertEqual(cmd[:4], ["gcloud", "compute", "reset-windows-password", "win-01"])

    def test_includes_user_flag_when_user_is_set(self):
        module = load_module()

        cmd = module.build_reset_windows_password_cmd(
            instance="win-01",
            project="project-01",
            zone="us-east4-a",
            user="custom_admin",
        )

        self.assertIn("--user", cmd)
        self.assertEqual(cmd[cmd.index("--user") + 1], "custom_admin")


if __name__ == "__main__":
    unittest.main()

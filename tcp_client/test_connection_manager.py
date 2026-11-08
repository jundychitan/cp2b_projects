import contextlib
import io
import unittest
from unittest import mock

import connection_manager as manager


class EthernetInterfaceTests(unittest.TestCase):
    def test_mac_lookup_defaults_to_eth0(self):
        with mock.patch("builtins.open", mock.mock_open(
                read_data="02:ab:cd:ef:12:34\n")) as address_file:
            self.assertEqual(manager.get_mac_address(), "02:AB:CD:EF:12:34")
        address_file.assert_called_once_with("/sys/class/net/eth0/address")

    def test_app_state_defaults_to_eth0(self):
        with mock.patch.object(manager, "get_mac_address",
                               return_value="02:AB:CD:EF:12:34") as get_mac:
            state = manager.AppState()
        get_mac.assert_called_once_with("eth0")
        self.assertEqual(state.mac_address, "02:AB:CD:EF:12:34")

    def test_app_state_preserves_interface_override(self):
        with mock.patch.object(manager, "get_mac_address") as get_mac:
            manager.AppState(interface="end0")
        get_mac.assert_called_once_with("end0")

    def test_cli_defaults_and_override(self):
        for arguments, expected in [([], "eth0"),
                                    (["--interface", "end0"], "end0")]:
            with self.subTest(arguments=arguments):
                # Stop startup before config loading, threads, or networking.
                with mock.patch.object(manager, "AppState",
                                       side_effect=RuntimeError("stop")) as state:
                    with mock.patch.object(manager.sys, "argv",
                                           ["connection_manager.py"] + arguments):
                        with self.assertRaisesRegex(RuntimeError, "stop"):
                            manager.main()
                state.assert_called_once_with(interface=expected)

    def test_cli_help_documents_eth0(self):
        output = io.StringIO()
        with mock.patch.object(manager.sys, "argv",
                               ["connection_manager.py", "--help"]):
            with contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as result:
                    manager.main()
        self.assertEqual(result.exception.code, 0)
        self.assertIn("default: eth0", output.getvalue())


if __name__ == "__main__":
    unittest.main()

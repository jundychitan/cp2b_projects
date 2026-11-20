"""Run with: python3 -B -m unittest discover -s . -p test_lte_ecm.py -v"""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

SPEC = importlib.util.spec_from_file_location("lte_ecm", Path(__file__).with_name("lte-ecm.py"))
lte = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lte)


class ModeTests(unittest.TestCase):
    def test_parse_modes(self):
        for mode in (0, 1, 2, 3):
            self.assertEqual(lte.usbnet_mode(f'\r\n+QCFG: "usbnet", {mode}\r\nOK\r\n'), mode)

    def test_bad_mode_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot parse"):
            lte.usbnet_mode("ERROR")

    def test_wait_timeout(self):
        with patch.object(lte.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                lte.wait_for(lambda: False, 1, "test device")

    def test_wait_returns_result(self):
        self.assertEqual(lte.wait_for(lambda: "usb0", 1, "test device"), "usb0")

    def test_find_usb_parent(self):
        with tempfile.TemporaryDirectory() as root:
            parent = Path(root) / "usb"
            leaf = parent / "interface" / "ttyUSB2"
            leaf.mkdir(parents=True)
            (parent / "idVendor").write_text("2c7c")
            (parent / "idProduct").write_text("0901")
            self.assertEqual(lte.usb_parent(leaf), parent.resolve())

    def test_no_usb_parent(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, "USB parent"):
                lte.usb_parent(root)


class ModemTests(unittest.TestCase):
    def setUp(self):
        self.stack = []
        for name in ("at_port", "modem_usb", "wait_for"):
            p = patch.object(lte, name)
            self.stack.append(p)
            setattr(self, name, p.start())
            self.addCleanup(p.stop)
        self.parent = MagicMock()
        self.modem_usb.return_value = self.parent
        self.port = self.at_port.return_value.__enter__.return_value
        self.config = dict(lte.DEFAULTS)
        sleep_patch = patch.object(lte.time, "sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def response(self, mode=1, identity="Quectel EC200U", contexts="", connected=1):
        def command(text, **kwargs):
            if text in ("ATI", "AT+CGMM", "AT+GMM"):
                return identity + "\r\nOK\r\n"
            if text == 'AT+QCFG="usbnet"':
                return f'+QCFG: "usbnet",{mode}\r\nOK\r\n'
            if text == "AT+CGDCONT?":
                return contexts
            if text == "AT+QNETDEVCTL?":
                return f"+QNETDEVCTL: 3,1,1,{connected}\r\nOK\r\n"
            return "OK\r\n"
        self.port.command.side_effect = command

    def test_already_ecm_does_not_write_or_reset(self):
        self.response()
        self.assertEqual(lte.prepare_modem(self.config), self.parent)
        self.assertEqual(self.port.command.call_args_list,
                         [call("AT+CGMM", timeout=3), call('AT+QCFG="usbnet"'),
                          call("AT+QNETDEVCTL?")])

    def test_wrong_identity_is_not_modified(self):
        self.response(identity="Unrelated modem")
        with self.assertRaisesRegex(RuntimeError, "No supported EC200/EC25"):
            lte.prepare_modem(self.config)
        self.assertEqual(self.port.command.call_count, 6)
        self.assertTrue(all(c.args[0] in ("AT+CGMM", "AT+GMM", "ATI")
                            for c in self.port.command.call_args_list))

    def test_mode_change_is_verified_after_reset(self):
        modes = iter((0, 1))
        self.port.command.side_effect = lambda text, **kwargs: (
            "Quectel EC200A" if text == "ATI" else
            f'+QCFG: "usbnet",{next(modes)}' if text == 'AT+QCFG="usbnet"' else
            "+QNETDEVCTL: 3,1,1,1" if text == "AT+QNETDEVCTL?" else "OK"
        )
        lte.prepare_modem(self.config)
        self.port.command.assert_any_call('AT+QCFG="usbnet",1')
        self.port.command.assert_any_call("AT+CFUN=1,1", reset=True)
        self.assertEqual(self.modem_usb.call_count, 2)

    def test_mode_not_retained_fails(self):
        self.response(mode=0)
        with self.assertRaisesRegex(RuntimeError, "did not retain"):
            lte.prepare_modem(self.config)

    def test_model_only_ec200_reply_accepted(self):
        self.response(identity="EC200A")
        lte.prepare_modem(self.config)
        self.port.command.assert_any_call('AT+QCFG="usbnet"')

    def test_ec25_skips_ec200_data_call_commands(self):
        self.response(identity="Quectel EC25-E")
        lte.prepare_modem(self.config)
        self.port.command.assert_any_call('AT+QCFG="usbnet"')
        self.assertFalse(any("QNETDEVCTL" in c.args[0]
                             for c in self.port.command.call_args_list))

    def test_matching_apn_is_not_rewritten(self):
        self.config["apn"] = "internet"
        self.response(contexts='+CGDCONT: 1,"IP","internet","0.0.0.0"\r\nOK')
        lte.prepare_modem(self.config)
        self.assertNotIn(call('AT+CGDCONT=1,"IP","internet"'),
                         self.port.command.call_args_list)

    def test_apn_and_dial_are_explicit(self):
        self.config.update(apn="internet", dial_command="AT+QNETDEVCTL=1,1,1")
        self.response(connected=0)
        lte.prepare_modem(self.config)
        self.port.command.assert_any_call('AT+CGDCONT=1,"IP","internet"')
        self.port.command.assert_any_call("AT+QNETDEVCTL=1,1,1", timeout=45)

    def test_default_activation_for_disconnected_modem(self):
        self.response(connected=0)
        lte.prepare_modem(self.config)
        self.port.command.assert_any_call("AT+QNETDEVCTL=3,1,1", timeout=45)

    def test_legacy_empty_command_uses_default(self):
        self.config["dial_command"] = ""
        self.response(connected=0)
        lte.prepare_modem(self.config)
        self.port.command.assert_any_call("AT+QNETDEVCTL=3,1,1", timeout=45)

    def test_bad_apn_is_rejected(self):
        self.config["apn"] = 'bad"\rAT'
        self.response()
        with self.assertRaisesRegex(RuntimeError, "APN"):
            lte.prepare_modem(self.config)


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "eth": {"connection.id": "Wired connection 1",
                    "connection.type": "802-3-ethernet",
                    "connection.interface-name": "eth0", "ipv4.route-metric": "200"},
            "lte": {"connection.id": "LTE", "connection.type": "802-3-ethernet",
                    "connection.interface-name": "usb0", "GENERAL.DEVICES": "usb0"},
        }
        self.nm_patch = patch.object(lte, "nm")
        self.nm = self.nm_patch.start()
        self.addCleanup(self.nm_patch.stop)
        self.nm.side_effect = self.respond
        routes_patch = patch.object(lte, "verify_routes")
        self.verify_routes = routes_patch.start()
        self.addCleanup(routes_patch.stop)

    def respond(self, *args, **kwargs):
        if args == ("-g", "UUID", "connection", "show"):
            return "\n".join(self.data)
        if args[:1] == ("-g",) and args[2:5] == ("connection", "show", "uuid"):
            return self.data[args[5]].get(args[1], "")
        if args[:1] == ("-g",) and args[2:4] == ("device", "show"):
            if args[1] == "GENERAL.NM-MANAGED":
                return "yes"
            if args[1] == "GENERAL.CON-UUID":
                return "eth" if args[4] == "eth0" else "lte"
            if args[1] == "IP4.GATEWAY":
                return "192.168.225.1"
            if args[1] == "GENERAL.HWADDR":
                return "00:11:22:33:44:55"
        if args[:2] == ("connection", "modify"):
            self.data[args[3]].update(dict(zip(args[4::2], args[5::2])))
            return ""
        if args[:2] == ("connection", "add"):
            self.data["new"] = {"connection.id": "LTE", "connection.type": "802-3-ethernet"}
            return ""
        if args == ("-g", "connection.uuid", "connection", "show", "id", "LTE"):
            return "new"
        if args[:2] in (("device", "reapply"), ("--wait", "60")):
            return ""
        raise AssertionError("Unexpected nmcli arguments: " + repr(args))

    def test_correct_properties_are_not_written(self):
        self.assertFalse(lte.set_properties("eth", {"ipv4.route-metric": "200"}))
        self.assertFalse(any(c.args[:2] == ("connection", "modify")
                             for c in self.nm.call_args_list))

    def test_only_different_properties_are_written(self):
        self.assertTrue(lte.set_properties("eth", {
            "ipv4.route-metric": "200", "connection.autoconnect": "yes"}))
        self.nm.assert_any_call("connection", "modify", "uuid", "eth",
                                "connection.autoconnect", "yes")

    def test_ethernet_reapply_without_down(self):
        self.data["eth"]["ipv4.route-metric"] = "999"
        lte.configure_ethernet("eth0")
        self.assertEqual(self.data["eth"]["ipv4.route-metric"], "200")
        self.verify_routes.assert_called_once_with("eth0", 200)
        self.nm.assert_any_call("device", "reapply", "eth0")
        self.assertFalse(any("down" in c.args for c in self.nm.call_args_list))

    def test_disconnected_unbound_ethernet_profile(self):
        self.data["eth"]["connection.interface-name"] = ""
        original = self.respond
        self.nm.side_effect = lambda *args, **kwargs: (
            "--" if args == ("-g", "GENERAL.CON-UUID", "device", "show", "eth0")
            else original(*args, **kwargs)
        )
        lte.configure_ethernet("eth0")

    def test_unmanaged_ethernet_is_not_migrated(self):
        self.nm.return_value = "no"
        self.nm.side_effect = None
        with self.assertRaisesRegex(RuntimeError, "not managed"):
            lte.configure_ethernet("eth0")
        self.assertEqual(self.nm.call_count, 1)

    def test_existing_lte_is_reused_and_idempotent(self):
        lte.configure_lte("usb0", 60)
        self.assertEqual(self.data["lte"]["ipv4.route-metric"], "100")
        self.verify_routes.assert_called_once_with("usb0", 100, required=True)
        self.nm.reset_mock()
        lte.configure_lte("usb0", 60)
        self.assertFalse(any(c.args[:2] in (("connection", "add"), ("connection", "modify"))
                             for c in self.nm.call_args_list))

    def test_missing_lte_is_created(self):
        del self.data["lte"]
        lte.configure_lte("usb0", 60)
        self.assertEqual(self.data["new"]["ipv4.route-metric"], "100")
        self.verify_routes.assert_called_once_with("usb0", 100, required=True)
        self.nm.assert_any_call("--wait", "60", "connection", "up", "uuid", "new",
                                "ifname", "usb0", timeout=70)

    def test_duplicate_lte_fails(self):
        self.data["other"] = dict(self.data["lte"])
        with self.assertRaisesRegex(RuntimeError, "Multiple connections"):
            lte.configure_lte("usb0", 60)

    def test_gsm_lte_not_overwritten(self):
        self.data["lte"]["connection.type"] = "gsm"
        with self.assertRaisesRegex(RuntimeError, "not an Ethernet"):
            lte.configure_lte("usb0", 60)

    def test_unrelated_interface_not_overwritten(self):
        self.data["lte"]["connection.interface-name"] = "eth0"
        with patch.object(lte.Path, "exists", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "another present"):
                lte.configure_lte("usb0", 60)

    def test_existing_eth2_metric_repaired_without_modem(self):
        self.data["lte"].update({
            "connection.interface-name": "eth2", "GENERAL.DEVICES": "eth2",
            "ipv4.route-metric": "200",
        })
        with patch.object(lte, "prepare_modem") as modem:
            self.assertTrue(lte.configure_existing_lte_metric("eth0"))
        modem.assert_not_called()
        self.assertEqual(self.data["lte"]["ipv4.route-metric"], "100")
        self.nm.assert_any_call("device", "reapply", "eth2")
        self.verify_routes.assert_called_once_with("eth2", 100)

    def test_inactive_lte_metric_saved_without_activation(self):
        self.data["lte"]["GENERAL.DEVICES"] = "--"
        self.assertTrue(lte.configure_existing_lte_metric("eth0"))
        self.assertEqual(self.data["lte"]["ipv4.route-metric"], "100")
        self.verify_routes.assert_not_called()
        self.assertFalse(any("reapply" in c.args or "up" in c.args
                             for c in self.nm.call_args_list))

    def test_missing_lte_metric_profile_not_created_by_guessing(self):
        del self.data["lte"]
        self.assertFalse(lte.configure_existing_lte_metric("eth0"))
        self.assertFalse(any(c.args[:2] == ("connection", "add")
                             for c in self.nm.call_args_list))

    def test_metric_repair_refuses_primary_ethernet_profile(self):
        self.data["lte"]["GENERAL.DEVICES"] = "eth0"
        with self.assertRaisesRegex(RuntimeError, "primary Ethernet"):
            lte.configure_existing_lte_metric("eth0")
        self.assertFalse(any(c.args[:2] == ("connection", "modify")
                             for c in self.nm.call_args_list))

    def test_live_verification_failure_keeps_persistent_metric(self):
        self.verify_routes.side_effect = RuntimeError("route override")
        with self.assertRaisesRegex(RuntimeError, "route override"):
            lte.configure_existing_lte_metric("eth0")
        self.assertEqual(self.data["lte"]["ipv4.route-metric"], "100")

    def test_missing_gateway_is_failure(self):
        original = self.respond
        self.nm.side_effect = lambda *args, **kwargs: (
            "" if args[:2] == ("-g", "IP4.GATEWAY") else original(*args, **kwargs)
        )
        with self.assertRaisesRegex(RuntimeError, "no IPv4 gateway"):
            lte.configure_lte("usb0", 60)

    def test_unbound_profile_active_on_ethernet_not_overwritten(self):
        self.data["lte"]["connection.interface-name"] = ""
        self.data["lte"]["GENERAL.DEVICES"] = "eth0"
        with self.assertRaisesRegex(RuntimeError, "active on another"):
            lte.configure_lte("usb0", 60)


class IdentityTests(unittest.TestCase):
    def test_supported_formats(self):
        for response, expected in (
            ("EC200A\r\nOK", "EC200A"),
            ("Quectel\r\nEC200U\r\nRevision: something\r\nOK", "EC200U"),
            ('+CGMM: "EC200A-CN"\r\nOK', "EC200A-CN"),
            ("Model: EC25-E\r\nOK", "EC25-E"),
            ("quectel ec200u\r\nOK", "EC200U"),
        ):
            with self.subTest(response=response):
                self.assertEqual(lte.model_from_response(response), expected)

    def test_unrelated_or_firmware_only_text_not_accepted(self):
        for response in ("OK", "Quectel EG25-G", "Revision: EC200A12345", "AT+CGMM"):
            self.assertIsNone(lte.model_from_response(response))

    def test_gmm_fallback_when_cgmm_unsupported(self):
        port = MagicMock()
        port.command.side_effect = [RuntimeError("unsupported"), "EC200A\r\nOK"]
        self.assertEqual(lte.identify_modem(port, "/dev/ttyUSB2"), "EC200A")
        port.command.assert_any_call("AT+GMM", timeout=3)

    def test_ati_fallback(self):
        port = MagicMock()
        port.command.side_effect = ["OK", "OK", "Quectel EC200U\r\nOK"]
        self.assertEqual(lte.identify_modem(port, "/dev/ttyUSB2"), "EC200U")

    def test_incomplete_boot_reply_retried(self):
        port = MagicMock()
        port.command.side_effect = ["OK", "OK", "OK", "EC200A\r\nOK"]
        with patch.object(lte.time, "sleep"):
            self.assertEqual(lte.identify_modem(port, "/dev/ttyUSB2"), "EC200A")

    def test_unknown_identity_fails_without_configuring_modem(self):
        port = MagicMock()
        port.command.return_value = "Unrelated modem\r\nOK"
        with patch.object(lte.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "No modem settings were changed"):
                lte.identify_modem(port, "/dev/ttyUSB2")
        self.assertEqual(port.command.call_count, 6)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.calls = MagicMock()
        for name in ("configure_existing_lte_metric", "configure_ethernet",
                     "prepare_modem", "configure_lte", "wait_for"):
            p = patch.object(lte, name)
            mock = p.start()
            self.addCleanup(p.stop)
            setattr(self, name, mock)
            self.calls.attach_mock(mock, name)
        self.config = {**lte.DEFAULTS, "ethernet": "eth0"}
        self.configure_existing_lte_metric.return_value = True
        self.wait_for.return_value = "eth2"

    def test_both_metrics_attempted_before_failed_identity(self):
        self.prepare_modem.side_effect = RuntimeError("identity rejected")
        with self.assertRaisesRegex(RuntimeError, "identity rejected"):
            lte.run(self.config)
        self.assertEqual(self.calls.mock_calls[:3], [
            call.configure_existing_lte_metric("eth0"),
            call.configure_ethernet("eth0"), call.prepare_modem(self.config),
        ])
        self.configure_lte.assert_not_called()

    def test_ethernet_failure_does_not_prevent_modem_setup(self):
        self.configure_ethernet.side_effect = RuntimeError("Ethernet unavailable")
        with self.assertRaisesRegex(RuntimeError, "Ethernet unavailable"):
            lte.run(self.config)
        self.prepare_modem.assert_called_once_with(self.config)
        self.configure_lte.assert_called_once_with("eth2", 60)

    def test_route_only_never_opens_modem(self):
        lte.run(self.config, routes_only=True)
        self.configure_existing_lte_metric.assert_called_once_with("eth0")
        self.configure_ethernet.assert_called_once_with("eth0")
        self.prepare_modem.assert_not_called()
        self.configure_lte.assert_not_called()

    def test_route_only_reports_missing_profile(self):
        self.configure_existing_lte_metric.return_value = False
        with self.assertRaisesRegex(RuntimeError, "No existing LTE profile"):
            lte.run(self.config, routes_only=True)
        self.configure_ethernet.assert_called_once_with("eth0")
        self.prepare_modem.assert_not_called()

    def test_full_success(self):
        lte.run(self.config)
        self.configure_lte.assert_called_once_with("eth2", 60)


class DataCallTests(unittest.TestCase):
    def test_connected_state_requires_correct_context(self):
        self.assertTrue(lte.data_call_connected("+QNETDEVCTL: 3,1,1,1\r\nOK", 1))
        self.assertFalse(lte.data_call_connected("+QNETDEVCTL: 3,2,1,1\r\nOK", 1))
        self.assertFalse(lte.data_call_connected("+QNETDEVCTL: 3,1,1,0\r\nOK", 1))

    def test_bad_query_does_not_guess_state(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot parse"):
            lte.data_call_connected("OK", 1)

    def test_activation_waits_until_connected(self):
        port = MagicMock()
        port.command.side_effect = [
            "+QNETDEVCTL: 0,0,0,0\r\nOK", "OK",
            "+QNETDEVCTL: 3,1,1,0\r\nOK", "+QNETDEVCTL: 3,1,1,1\r\nOK",
        ]
        with patch.object(lte.time, "sleep"):
            lte.activate_data_call(port, "AT+QNETDEVCTL=3,1,1", 10)
        self.assertEqual(port.command.call_count, 4)

    def test_connected_call_not_activated_again(self):
        port = MagicMock()
        port.command.return_value = "+QNETDEVCTL: 3,1,1,1\r\nOK"
        lte.activate_data_call(port, "AT+QNETDEVCTL=3,1,1", 10)
        port.command.assert_called_once_with("AT+QNETDEVCTL?")

    def test_activation_times_out(self):
        port = MagicMock()
        port.command.return_value = "+QNETDEVCTL: 3,1,1,0\r\nOK"
        with patch.object(lte.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                lte.activate_data_call(port, "AT+QNETDEVCTL=3,1,1", 1)


class RouteTests(unittest.TestCase):
    def test_live_metric_verified(self):
        with patch.object(lte.subprocess, "run") as run:
            run.return_value.stdout = '[{"dst":"default","metric":100}]'
            lte.verify_routes("usb0", 100, required=True)

    def test_explicit_wrong_metric_detected(self):
        with patch.object(lte.subprocess, "run") as run:
            run.return_value.stdout = '[{"dst":"default","metric":999}]'
            with self.assertRaisesRegex(RuntimeError, "explicit ipv4.routes"):
                lte.verify_routes("usb0", 100, required=True)

    def test_lte_default_route_required(self):
        with patch.object(lte.subprocess, "run") as run:
            run.return_value.stdout = "[]"
            with self.assertRaisesRegex(RuntimeError, "No IPv4 default route"):
                lte.verify_routes("usb0", 100, required=True)
            lte.verify_routes("eth0", 200)


if __name__ == "__main__":
    unittest.main()

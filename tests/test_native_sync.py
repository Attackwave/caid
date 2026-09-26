from dataclasses import replace
import unittest

from caid_chat.native_sync import ACTION, describe_outcome, invoke_kicad_update
from caid_chat.net_sync import plan_sync
from test_net_sync import SCHEMATIC, STATE


class NativeSyncTests(unittest.TestCase):
    def test_action_uses_same_client_and_restores_timeout(self):
        class Socket:
            send_timeout = 2000
            recv_timeout = 2000

        class Ipc:
            connected = True
            _timeout_ms = 2000
            _conn = Socket()

        class Client:
            _client = Ipc()

            def run_action(self, name):
                self.action = name
                self.timeout_during_action = self._client._conn.recv_timeout
                return type("Response", (), {"status": 1})()

        client = Client()
        invoke_kicad_update(client)
        self.assertEqual(client.action, ACTION)
        self.assertEqual(client.timeout_during_action, 600_000)
        self.assertEqual(client._client._conn.recv_timeout, 2000)

    def test_report_distinguishes_applied_from_cancelled(self):
        before = plan_sync(STATE, SCHEMATIC)
        after = replace(before, board_sha256="updated", changes=(),
                        board_footprints=(("U1", "Other:Footprint"),),
                        board_pad_nets=(("U1", "1", "/VCC"), ("U1", "2", "")))
        report = describe_outcome(before, after)
        self.assertIn("2 → 0", report)
        self.assertIn("U1: Package_DIP:DIP-8 → Other:Footprint", report)
        self.assertIn("U1.1: ∅ → /VCC", report)
        self.assertIn("unverändert", describe_outcome(before, before, "de"))


if __name__ == "__main__":
    unittest.main()

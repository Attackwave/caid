"""Electrical invariants for the review-only ROM adapter net model."""

import unittest

from scripts.build_rom42_draft import REFERENCE, make_design


class Rom42DraftTests(unittest.TestCase):
    def test_bus_mapping_and_programming_interlock(self):
        design = make_design()
        nets = {net["name"]: {(n["ref"], str(n["pin"])) for n in net["nodes"]}
                for net in design["nets"]}
        flash_pin = {name: pin for pin, name in
                     REFERENCE["prototype_architecture"]["flash"]["pinout_48"].items()}

        for index in range(18):
            self.assertIn(("U1", flash_pin[f"A{index}"]), nets[f"FLASH_A{index}"])
            self.assertIn(("J2", str(5 + index if index < 8 else
                                      14 + index - 8 if index < 16 else
                                      23 + index - 16)), nets[f"FLASH_A{index}"])
        for index in range(16):
            self.assertIn(("J2", str(32 + index)), nets[f"FLASH_D{index}"])

        self.assertEqual(nets["FLASH_WE_N"], {("U10", "4"), ("U1", "11")})
        self.assertIn(("U10", "2"), nets["HOST_ON"])
        self.assertIn(("U10", "1"), nets["PROG_WE_N"])
        self.assertIn(("U8", "1"), nets["PROG_ACTIVE"])
        for ref in ("U2", "U3", "U4", "U5", "U6"):
            self.assertIn((ref, "22"), nets["PROG_ACTIVE"])

        self.assertIn(("D1", "2"), nets["HOST_5V"])
        self.assertIn(("D2", "2"), nets["PROG_5V"])
        self.assertIn(("D1", "1"), nets["REG_IN"])
        self.assertIn(("D2", "1"), nets["REG_IN"])


if __name__ == "__main__":
    unittest.main()

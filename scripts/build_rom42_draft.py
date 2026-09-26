"""Build the review-only A500+ ROM adapter input model and local symbols.

The resulting design.json is consumed by CAID's checked schematic/PCB writer.
This script does not certify dimensions, timing or safe operation in hardware.
"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "tmp" / "hardware" / "FlashROM42_Prototype"
REFERENCE = json.loads((ROOT / "caid_chat" / "rom_reference.json").read_text(encoding="utf-8"))


def symbol(name, pins):
    # One graphical unit; numbered pins come directly from the source table.
    height = max(len(pins) // 2, 4)
    lines = [f'  (symbol "{name}"',
             '    (property "Reference" "U" (at 0 5.08 0) (effects (font (size 1.27 1.27))))',
             f'    (property "Value" "{name}" (at 0 2.54 0) (effects (font (size 1.27 1.27))))',
             '    (symbol "' + name + '_0_1"',
             f'      (rectangle (start -10.16 {height*2.54/2:.2f}) (end 10.16 {-height*2.54/2:.2f})'
             ' (stroke (width 0.254) (type default)) (fill (type background))))',
             '    (symbol "' + name + '_1_1"']
    split = (len(pins) + 1) // 2
    display_pins = pins[:split] + list(reversed(pins[split:]))
    for index, (number, pin_name, kind) in enumerate(display_pins):
        left = index < split
        row = index if left else index - split
        x = -12.7 if left else 12.7
        angle = 0 if left else 180
        y = (split - 1) * 1.27 - row * 2.54
        lines.append(f'      (pin {kind} line (at {x} {y:.2f} {angle}) (length 2.54)'
                     f' (name "{pin_name}" (effects (font (size 1.27 1.27))))'
                     f' (number "{number}" (effects (font (size 1.27 1.27)))))')
    lines.extend(('    )', '  )'))
    return "\n".join(lines)


def write_symbols():
    flash = REFERENCE["prototype_architecture"]["flash"]["pinout_48"]
    flash_pins = []
    for number, name in flash.items():
        kind = "passive"
        if name.startswith("A") or name in ("WE#", "RST#", "WP#", "CE#", "OE#"):
            kind = "input"
        elif name.startswith("DQ"):
            kind = "bidirectional"
        elif name in ("VDD", "VSS"):
            kind = "power_in"
        elif name == "RY/BY#":
            kind = "output"
        elif name == "NC":
            kind = "no_connect"
        flash_pins.append((number, name, kind))
    socket = [(number, name.replace("/", "~"), "passive")
              for number, name in REFERENCE["user_pinout_42"].items()]
    inverter = [("1", "NC", "no_connect"), ("2", "A", "input"),
                ("3", "GND", "power_in"), ("4", "Y", "output"),
                ("5", "VCC", "power_in")]
    orgate = [("1", "A", "input"), ("2", "B", "input"),
              ("3", "GND", "power_in"), ("4", "Y", "output"),
              ("5", "VCC", "power_in")]
    mux = [(str(i), n, "power_in" if n in ("GND", "VCC") else
            "output" if n.endswith("Y") else "input") for i, n in enumerate(
        ["A/B", "1A", "1B", "1Y", "2A", "2B", "2Y", "GND",
         "3Y", "3B", "3A", "4Y", "4B", "4A", "G", "VCC"], 1)]
    regulator = [("1", "IN", "power_in"), ("2", "GND", "power_in"),
                 ("3", "EN", "input"), ("4", "NC", "no_connect"),
                 ("5", "OUT", "power_out")]
    symbols = [symbol("SST39VF1601C_TSOP48", flash_pins),
               symbol("AMIGA_ROM42", socket),
               symbol("SN74LVC1G04_DBV", inverter),
               symbol("SN74LVC1G32_DBV", orgate),
               symbol("SN74LVC157A_PW", mux),
               symbol("TLV75533PDBV", regulator)]
    (SOURCE / "ROM42.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20251024) (generator "caid") (generator_version "0.16")\n' +
        "\n".join(symbols) + "\n)\n", encoding="utf-8")
    (SOURCE / "sym-lib-table").write_text(
        '(sym_lib_table (lib (name "ROM42")(type "KiCad")'
        '(uri "${KIPRJMOD}/ROM42.kicad_sym")(options "")(descr "Verified pin-number drafts")))\n',
        encoding="utf-8")


def make_design():
    parts = []
    nets = {}
    def part(ref, symbol_id, value, footprint, side="TOP"):
        i = len(parts)
        parts.append({"ref": ref, "symbol": symbol_id, "value": value,
                      "footprint": footprint, "side": side,
                      "x_mm": 38.1 + i % 7 * 71.12, "y_mm": 55.88 + i // 7 * 76.2,
                      "pcb_x_mm": 40 + i % 7 * 30, "pcb_y_mm": 40 + i // 7 * 35})
    def join(name, ref, pin):
        nets.setdefault(name, []).append({"ref": ref, "pin": str(pin)})
    def connect(name, *nodes):
        for ref, pin in nodes:
            join(name, ref, pin)

    part("J1", "ROM42:AMIGA_ROM42", "Amiga A500+ Rev8 ROM socket",
         "Package_DIP:DIP-42_W15.24mm", "BOTTOM")
    part("U1", "ROM42:SST39VF1601C_TSOP48", "SST39VF1601C-70-4I-EKE",
         "ROM42:TSOP-I-48_12x20mm_P0.5mm")
    for ref in ("U2", "U3", "U4", "U5", "U6"):
        part(ref, "Logic_LevelTranslator:SN74LVC8T245", "SN74LVC8T245PWR",
             "Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm", "BOTTOM")
    part("J2", "Connector_Generic:Conn_01x50", "Programmer FFC 50 0.5mm",
         "Connector_FFC-FPC:Hirose_FH12-50S-0.5SH_1x50-1MP_P0.50mm_Horizontal")
    parts[-1]["pcb_x_mm"] = 250
    parts[-1]["pcb_y_mm"] = 40
    part("U7", "ROM42:TLV75533PDBV", "TLV75533PDBVR",
         "Package_TO_SOT_SMD:SOT-23-5", "BOTTOM")
    part("U8", "ROM42:SN74LVC157A_PW", "SN74LVC157APWR",
         "Package_SO:TSSOP-16_4.4x5mm_P0.65mm", "BOTTOM")
    part("U9", "ROM42:SN74LVC1G04_DBV", "SN74LVC1G04DBVR",
         "Package_TO_SOT_SMD:SOT-23-5", "BOTTOM")
    part("U10", "ROM42:SN74LVC1G32_DBV", "SN74LVC1G32DBVR",
         "Package_TO_SOT_SMD:SOT-23-5", "BOTTOM")
    part("SW1", "Switch:SW_SPDT", "ROM bank select (power off only)",
         "Button_Switch_SMD:SW_SPDT_PCM12", "TOP")
    for ref in ("D1", "D2"):
        part(ref, "Device:D_Schottky", "SS14",
             "Diode_SMD:D_SMA", "BOTTOM")
    for i, val in enumerate(["10k", "10k", "20k", "33k", "10k", "10k"], 1):
        part(f"R{i}", "Device:R", val,
             "Resistor_SMD:R_0603_1608Metric", "BOTTOM")
    for i in range(1, 14):
        part(f"C{i}", "Device:C", "1u" if i >= 12 else "100n",
             "Capacitor_SMD:C_0603_1608Metric", "BOTTOM")

    positions = {
        "J1": (76.2, 139.7), "U1": (190.5, 139.7),
        "U2": (304.8, 76.2), "U3": (393.7, 76.2), "U4": (482.6, 76.2),
        "U5": (304.8, 177.8), "U6": (393.7, 177.8),
        "J2": (76.2, 355.6), "U7": (190.5, 304.8),
        "U8": (279.4, 304.8), "U9": (368.3, 304.8),
        "U10": (457.2, 304.8), "SW1": (190.5, 381.0),
        "D1": (279.4, 381.0), "D2": (368.3, 381.0),
    }
    for i in range(1, 7):
        positions[f"R{i}"] = (190.5 + (i-1) % 4 * 88.9, 444.5 + (i-1) // 4 * 38.1)
    for i in range(1, 14):
        positions[f"C{i}"] = (76.2 + (i-1) % 7 * 63.5, 508.0 + (i-1) // 7 * 38.1)
    for component in parts:
        component["x_mm"], component["y_mm"] = positions[component["ref"]]

    socket_pin = {name.replace("/", "~"): n for n, name in REFERENCE["user_pinout_42"].items()}
    flash_pin = {name: n for n, name in REFERENCE["prototype_architecture"]["flash"]["pinout_48"].items()}
    for i in range(18):
        ref = ("U2", "U3", "U4")[i // 8]
        channel = i % 8
        net = f"HOST_A{i}"
        connect(net, ("J1", socket_pin[f"A{i}"]), (ref, 21-channel))
        net = f"FLASH_A{i}"
        connect(net, (ref, 3+channel), ("U1", flash_pin[f"A{i}"]))
        # Address bus is shared only on the 3.3 V side. Programmer is high-Z in read mode.
        ffc_pin = 5+i if i < 8 else 14+i-8 if i < 16 else 23+i-16
        join(net, "J2", ffc_pin)
    for channel, signal, socket_name, ffc_pin in (
            (2, "CE_N", "~CE", 27), (3, "OE_N", "~OE", 28)):
        connect(f"HOST_{signal}", ("J1", socket_pin[socket_name]), ("U4", 21-channel))
        connect(f"FLASH_{signal}", ("U4", 3+channel), ("U1", flash_pin[signal.replace("_N", "#")]),
                ("J2", ffc_pin))
    for i in range(16):
        ref = "U5" if i < 8 else "U6"
        channel = i % 8
        connect(f"FLASH_D{i}", ("U1", flash_pin[f"DQ{i}"]), (ref, 3+channel), ("J2", 32+i))
        socket_name = f"D{i}" if i < 15 else "D15~A-1"
        connect(f"HOST_D{i}", (ref, 21-channel), ("J1", socket_pin[socket_name]))
    connect("FLASH_A18", ("U1", flash_pin["A18"]), ("U8", 4))
    connect("FLASH_A19", ("U1", flash_pin["A19"]), ("U8", 7))
    connect("BANK_A18", ("SW1", 2), ("U8", 2))
    connect("PROG_A18", ("J2", 25), ("U8", 3))
    connect("PROG_A19", ("J2", 26), ("U8", 6))
    connect("PROG_MODE_N", ("J2", 4), ("U9", 2), ("R1", 2))
    connect("PROG_ACTIVE", ("U9", 4), ("U8", 1), *((ref, 22) for ref in ("U2", "U3", "U4", "U5", "U6")))
    connect("PROG_WE_N", ("J2", 29), ("U10", 1), ("R2", 2))
    connect("HOST_ON", ("R3", 2), ("R4", 1), ("U10", 2), ("J2", 50))
    connect("FLASH_WE_N", ("U10", 4), ("U1", flash_pin["WE#"]))
    connect("FLASH_CE_N", ("R5", 2))
    connect("FLASH_OE_N", ("R6", 2))

    connect("HOST_5V", ("J1", socket_pin["VCC"]), ("D1", 2), ("R3", 1),
            *((ref, pin) for ref in ("U2", "U3", "U4", "U5", "U6") for pin in (23, 24)))
    connect("PROG_5V", ("J2", 2), ("D2", 2))
    connect("REG_IN", ("D1", 1), ("D2", 1), ("U7", 1), ("U7", 3), ("C12", 1))
    connect("3V3", ("U7", 5), ("U1", flash_pin["VDD"]),
            ("U1", flash_pin["WP#"]), ("U1", flash_pin["RST#"]),
            ("SW1", 1), ("R1", 1), ("R2", 1), ("R5", 1), ("R6", 1),
            ("U8", 16), ("U9", 5), ("U10", 5), ("C13", 1),
            *((ref, 1) for ref in ("U2", "U3", "U4", "U5", "U6")),
            *((f"C{i}", 1) for i in range(1, 6)))
    connect("GND", ("J1", 12), ("J1", 31),
            ("U1", 27), ("U1", 46),
            ("U7", 2), ("U8", 8), ("U8", 15), ("U9", 3), ("U10", 3),
            ("SW1", 3), ("R4", 2), ("C12", 2), ("C13", 2),
            *((ref, pin) for ref in ("U2", "U3", "U4", "U5", "U6") for pin in (11, 12, 13)),
            *(("J2", pin) for pin in (1, 3, 13, 22, 30, 31, 48, 49)),
            *((f"C{i}", 2) for i in range(1, 12)))
    for i in range(6, 12):
        join("HOST_5V", f"C{i}", 1)
    for ref, direction in (("U2", "GND"), ("U3", "GND"), ("U4", "GND"),
                           ("U5", "3V3"), ("U6", "3V3")):
        join(direction, ref, 2)
    # Tie unused mux inputs to a defined level; outputs remain unconnected.
    for pin in (5, 10, 11, 13, 14):
        join("GND", "U8", pin)

    no_connects = [{"ref": "J1", "pin": p} for p in (1, 32, 42)]
    no_connects += [{"ref": "U1", "pin": 15}, {"ref": "U8", "pin": 9},
                    {"ref": "U8", "pin": 12}]
    no_connects += [{"ref": "U4", "pin": p} for p in (7, 8, 9, 10, 14, 15, 16, 17)]
    return {"name": "FlashROM42_Prototype", "components": parts,
            "nets": [{"name": name, "nodes": nodes} for name, nodes in nets.items()],
            "no_connects": no_connects}


def main():
    SOURCE.mkdir(parents=True, exist_ok=True)
    write_symbols()
    footprint_dir = SOURCE / "ROM42.pretty"
    footprint_dir.mkdir(exist_ok=True)
    # The provisional TSOP footprint is kept with the versioned test project.
    (SOURCE / "fp-lib-table").write_text(
        '(fp_lib_table (lib (name "ROM42")(type "KiCad")'
        '(uri "${KIPRJMOD}/ROM42.pretty")(options "")(descr "Provisional 48-pin TSOP")))\n',
        encoding="utf-8")
    spec = make_design()
    (SOURCE / "design.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    (SOURCE / "FlashROM42_Prototype.kicad_pro").write_text("{}\n", encoding="utf-8")
    print(SOURCE, len(spec["components"]), "components", len(spec["nets"]), "nets")


if __name__ == "__main__":
    main()

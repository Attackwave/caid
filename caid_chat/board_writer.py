"""KiCad's pcbnew process: populate a new, unrouted PCB from validated design data."""

import json
from pathlib import Path
import sys


def write_board(payload, destination):
    import pcbnew

    board = pcbnew.BOARD()
    nets = {}
    for name in sorted(set(payload["connections"].values())):
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
        nets[name] = net
    for index, item in enumerate(payload["components"]):
        footprint = pcbnew.FootprintLoad(item["directory"], item["name"])
        if footprint is None:
            raise ValueError("KiCad could not load footprint for " + item["ref"])
        footprint.SetFPIDAsString(item["footprint_id"])
        footprint.SetReference(item["ref"])
        footprint.SetValue(item["value"])
        x = item.get("pcb_x_mm", 35 + (index % 4) * 35)
        y = item.get("pcb_y_mm", 35 + (index // 4) * 35)
        point = pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))
        footprint.SetPosition(point)
        footprint.SetPath(pcbnew.KIID_PATH("/" + item["uuid"]))
        board.Add(footprint)
        if item["side"] == "BOTTOM":
            footprint.Flip(point, pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        for pad in footprint.Pads():
            name = payload["connections"].get(item["ref"] + "." + pad.GetNumber())
            if name:
                pad.SetNet(nets[name])
    outline = payload.get("outline")
    if outline:
        shape = pcbnew.PCB_SHAPE(board)
        shape.SetShape(pcbnew.SHAPE_T_RECT)
        shape.SetLayer(pcbnew.Edge_Cuts)
        shape.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(20), pcbnew.FromMM(20)))
        shape.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(20 + outline["width_mm"]),
                                      pcbnew.FromMM(20 + outline["height_mm"])))
        board.Add(shape)
    pcbnew.SaveBoard(str(destination), board)
    saved = pcbnew.LoadBoard(str(destination))
    if saved is None:
        raise RuntimeError("KiCad could not reopen the generated PCB")
    actual = {}
    for footprint in saved.GetFootprints():
        point = footprint.GetPosition()
        box = footprint.GetBoundingBox(False)
        actual[footprint.GetReference()] = {
            "side": "BOTTOM" if footprint.GetLayerName() == "B.Cu" else "TOP",
            "x_mm": pcbnew.ToMM(point.x), "y_mm": pcbnew.ToMM(point.y),
            "bounds_mm": [pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop()),
                          pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom())],
        }
    edges = [item for item in saved.GetDrawings()
             if item.GetLayer() == pcbnew.Edge_Cuts]
    actual_outline = None
    if len(edges) == 1 and edges[0].GetShape() in (pcbnew.SHAPE_T_RECT, pcbnew.SHAPE_T_RECTANGLE):
        start, end = edges[0].GetStart(), edges[0].GetEnd()
        actual_outline = {"width_mm": abs(pcbnew.ToMM(end.x - start.x)),
                          "height_mm": abs(pcbnew.ToMM(end.y - start.y))}
    print("CAID_BOARD_MANIFEST=" + json.dumps({"components": actual,
                                                "outline": actual_outline,
                                                "edge_items": len(edges)}))


if __name__ == "__main__":
    source = Path(sys.argv[1])
    write_board(json.loads(source.read_text(encoding="utf-8")), Path(sys.argv[2]))

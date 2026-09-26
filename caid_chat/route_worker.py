"""Conservative grid router for one net in an isolated KiCad PCB copy.

Run with KiCad's bundled Python. Geometry filtering is deliberately conservative;
the caller must compare KiCad DRC before and after accepting the output.
"""

import heapq
import json
import math
from pathlib import Path
import sys


GRID_MM = 0.25
MAX_VISITS = 250_000


def board_outline(board, pcbnew):
    outline = pcbnew.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(outline, False) or not outline.OutlineCount():
        raise ValueError("Routing needs a closed Edge.Cuts outline")
    return outline, _bbox_mm(outline.BBox(), pcbnew)


def enabled_layers(board, pcbnew, names):
    layers = []
    for name in names:
        if name == "F.Cu":
            layer = pcbnew.F_Cu
        elif name == "B.Cu":
            layer = pcbnew.B_Cu
        elif name.startswith("In") and name.endswith(".Cu"):
            layer = getattr(pcbnew, name.replace(".", "_"), None)
        else:
            layer = None
        if layer is None or not board.IsLayerEnabled(layer):
            raise ValueError(f"Copper layer unavailable: {name}")
        layers.append(layer)
    return layers


def _bbox_mm(box, pcbnew):
    return (pcbnew.ToMM(box.GetLeft()), pcbnew.ToMM(box.GetTop()),
            pcbnew.ToMM(box.GetRight()), pcbnew.ToMM(box.GetBottom()))


def _mark_rect(blocked, rect, layer, origin, count, inflation):
    ox, oy = origin
    nx, ny = count
    x1, y1, x2, y2 = rect
    left = max(0, math.floor((x1 - inflation - ox) / GRID_MM))
    right = min(nx - 1, math.ceil((x2 + inflation - ox) / GRID_MM))
    top = max(0, math.floor((y1 - inflation - oy) / GRID_MM))
    bottom = min(ny - 1, math.ceil((y2 + inflation - oy) / GRID_MM))
    for x in range(left, right + 1):
        for y in range(top, bottom + 1):
            blocked.add((x, y, layer))


def _mark_segment(blocked, a, b, width, layer, origin, count, inflation):
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    steps = max(1, math.ceil(length / (GRID_MM / 2)))
    for index in range(steps + 1):
        ratio = index / steps
        x = a[0] + (b[0] - a[0]) * ratio
        y = a[1] + (b[1] - a[1]) * ratio
        _mark_rect(blocked, (x, y, x, y), layer, origin, count, inflation + width / 2)


def _point(pad, pcbnew):
    point = pad.GetPosition()
    return pcbnew.ToMM(point.x), pcbnew.ToMM(point.y)


def _grid(point, origin):
    return round((point[0] - origin[0]) / GRID_MM), round((point[1] - origin[1]) / GRID_MM)


def _coord(index, origin):
    return origin[0] + index[0] * GRID_MM, origin[1] + index[1] * GRID_MM


def _path(board, pcbnew, pads, net_name, layer_ids, contract):
    outline, (left, top, right, bottom) = board_outline(board, pcbnew)
    edge = contract["limits_mm"]["edge_clearance_mm"]
    width = contract["limits_mm"]["track_width_mm"]
    outline.Deflate(pcbnew.FromMM(edge + width / 2 + GRID_MM),
                    pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
    origin = (left, top)
    count = (math.floor((right - left) / GRID_MM) + 1,
             math.floor((bottom - top) / GRID_MM) + 1)
    if min(count) < 2 or count[0] * count[1] * len(layer_ids) > 2_000_000:
        raise ValueError("Board grid is empty or too large for this routing pass")
    clearance = contract["limits_mm"]["clearance_mm"]
    inflation = clearance + width / 2 + GRID_MM / 2
    blocked = set()
    via_blocked = set()
    for x in range(count[0]):
        for y in range(count[1]):
            position = pcbnew.VECTOR2I(pcbnew.FromMM(left + x * GRID_MM),
                                       pcbnew.FromMM(top + y * GRID_MM))
            if not outline.Contains(position):
                for layer_index in range(len(layer_ids)):
                    blocked.add((x, y, layer_index))
    for pad in board.GetPads():
        rect = _bbox_mm(pad.GetBoundingBox(), pcbnew)
        for layer_index, layer_id in enumerate(layer_ids):
            if pad.GetLayerSet().Contains(layer_id):
                if pad.GetNetname() != net_name:
                    _mark_rect(blocked, rect, layer_index, origin, count, inflation)
                _mark_rect(via_blocked, rect, layer_index, origin, count,
                           clearance + (contract["limits_mm"]["via_diameter_mm"] or 0) / 2)
    for item in board.GetTracks():
        if isinstance(item, pcbnew.PCB_VIA):
            position = item.GetPosition()
            point = (pcbnew.ToMM(position.x), pcbnew.ToMM(position.y))
            rect = (point[0], point[1], point[0], point[1])
            for layer_index in range(len(layer_ids)):
                if item.GetNetname() != net_name:
                    _mark_rect(blocked, rect, layer_index, origin, count,
                               inflation + pcbnew.ToMM(item.GetWidth()) / 2)
                _mark_rect(via_blocked, rect, layer_index, origin, count,
                           clearance + pcbnew.ToMM(item.GetWidth()) / 2)
        elif item.GetNetname() != net_name:
            for layer_index, layer_id in enumerate(layer_ids):
                if item.GetLayer() == layer_id:
                    a, b = item.GetStart(), item.GetEnd()
                    _mark_segment(blocked, (pcbnew.ToMM(a.x), pcbnew.ToMM(a.y)),
                                  (pcbnew.ToMM(b.x), pcbnew.ToMM(b.y)),
                                  pcbnew.ToMM(item.GetWidth()), layer_index, origin, count, inflation)
    for zone in board.Zones():
        if zone.GetNetname() == net_name:
            continue
        for layer_index, layer_id in enumerate(layer_ids):
            if not zone.GetLayerSet().Contains(layer_id):
                continue
            if not zone.HasFilledPolysForLayer(layer_id):
                raise ValueError("Fill copper zones before routing")
            polygon = pcbnew.SHAPE_POLY_SET(zone.GetFilledPolysList(layer_id))
            polygon.Inflate(pcbnew.FromMM(inflation),
                            pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.01))
            x1, y1, x2, y2 = _bbox_mm(polygon.BBox(), pcbnew)
            for x in range(max(0, math.floor((x1 - left) / GRID_MM)),
                           min(count[0], math.ceil((x2 - left) / GRID_MM) + 1)):
                for y in range(max(0, math.floor((y1 - top) / GRID_MM)),
                               min(count[1], math.ceil((y2 - top) / GRID_MM) + 1)):
                    position = pcbnew.VECTOR2I(pcbnew.FromMM(left + x * GRID_MM),
                                               pcbnew.FromMM(top + y * GRID_MM))
                    if polygon.Contains(position):
                        blocked.add((x, y, layer_index))
    starts = []
    goals = set()
    for index, pad in enumerate(pads):
        xy = _grid(_point(pad, pcbnew), origin)
        if not (0 <= xy[0] < count[0] and 0 <= xy[1] < count[1]):
            raise ValueError("Pad centre is outside routeable board area")
        for layer_index, layer_id in enumerate(layer_ids):
            if pad.GetLayerSet().Contains(layer_id):
                state = (*xy, layer_index)
                if index == 0:
                    starts.append(state)
                else:
                    goals.add(state)
    if not starts or not goals:
        raise ValueError("Pad has no access on the allowed routing layers")
    target_xy = {(x, y) for x, y, _ in goals}
    def heuristic(state):
        return min((abs(state[0] - x) + abs(state[1] - y)) * 10 for x, y in target_xy)
    queue = []
    distance = {}
    parent = {}
    for state in starts:
        distance[state] = 0
        heapq.heappush(queue, (heuristic(state), 0, state))
    neighbors = ((1, 0, 10), (-1, 0, 10), (0, 1, 10), (0, -1, 10),
                 (1, 1, 14), (1, -1, 14), (-1, 1, 14), (-1, -1, 14))
    visited = 0
    while queue and visited < MAX_VISITS:
        _estimate, cost, state = heapq.heappop(queue)
        if cost != distance[state]:
            continue
        visited += 1
        if state in goals:
            result = [state]
            while state in parent:
                state = parent[state]
                result.append(state)
            result.reverse()
            return result, origin, visited
        x, y, layer = state
        for dx, dy, step_cost in neighbors:
            next_state = (x + dx, y + dy, layer)
            if not (0 <= next_state[0] < count[0] and 0 <= next_state[1] < count[1]):
                continue
            if next_state in blocked or (dx and dy and
                    ((x + dx, y, layer) in blocked or (x, y + dy, layer) in blocked)):
                continue
            new_cost = cost + step_cost
            if new_cost < distance.get(next_state, 10**18):
                distance[next_state] = new_cost
                parent[next_state] = state
                heapq.heappush(queue, (new_cost + heuristic(next_state), new_cost, next_state))
        if len(layer_ids) > 1 and not any((x, y, index) in via_blocked or
                                          (x, y, index) in blocked for index in range(len(layer_ids))):
            for new_layer in range(len(layer_ids)):
                if new_layer == layer:
                    continue
                next_state = (x, y, new_layer)
                new_cost = cost + 100
                if new_cost < distance.get(next_state, 10**18):
                    distance[next_state] = new_cost
                    parent[next_state] = state
                    heapq.heappush(queue, (new_cost + heuristic(next_state), new_cost, next_state))
    raise ValueError(f"No route found for {net_name} within {visited} grid states")


def _simplify(path):
    if len(path) < 3:
        return path
    result = [path[0]]
    for index in range(1, len(path) - 1):
        before, current, after = path[index - 1:index + 2]
        if before[2] == current[2] == after[2] and (
                current[0] - before[0], current[1] - before[1]) == (
                after[0] - current[0], after[1] - current[1]):
            continue
        result.append(current)
    result.append(path[-1])
    return result


def _route_pair(board, pcbnew, pads, net_name, layers, contract):
    route, origin, visited = _path(board, pcbnew, pads, net_name, layers, contract)
    route = _simplify(route)
    net = pads[0].GetNet()
    width = pcbnew.FromMM(contract["limits_mm"]["track_width_mm"])
    vias = 0
    segments = 0
    for a, b in zip(route, route[1:]):
        if a[2] != b[2]:
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(_coord(a, origin)[0]),
                                           pcbnew.FromMM(_coord(a, origin)[1])))
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            via.SetWidth(pcbnew.FromMM(contract["limits_mm"]["via_diameter_mm"]))
            via.SetDrill(pcbnew.FromMM(contract["limits_mm"]["via_drill_mm"]))
            via.SetNet(net)
            board.Add(via)
            vias += 1
            continue
        start, end = _coord(a, origin), _coord(b, origin)
        if a == route[0]:
            start = _point(pads[0], pcbnew)
        if b == route[-1]:
            end = _point(pads[1], pcbnew)
        if start == end:
            continue
        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(start[0]), pcbnew.FromMM(start[1])))
        track.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(end[0]), pcbnew.FromMM(end[1])))
        track.SetWidth(width)
        track.SetLayer(layers[a[2]])
        track.SetNet(net)
        board.Add(track)
        segments += 1
    if not segments:
        raise ValueError("No track segments were created")
    return segments, vias, visited


def _pad_clusters(board, pcbnew, pads):
    """Group pads using KiCad's existing copper connectivity."""
    connectivity = board.GetConnectivity()
    connectivity.RecalculateRatsnest()
    by_id = {pad.m_Uuid.AsString(): pad for pad in pads}
    remaining = set(by_id)
    clusters = []
    while remaining:
        first_id = next(iter(remaining))
        connected = {item.m_Uuid.AsString() for item in
                     connectivity.GetConnectedItems(by_id[first_id])
                     if isinstance(item, pcbnew.PAD)}
        ids = (connected & remaining) or {first_id}
        clusters.append([by_id[identifier] for identifier in ids])
        remaining -= ids
    return clusters


def route_one(source, destination, net_name, contract):
    import pcbnew
    board = pcbnew.LoadBoard(str(source))
    if board is None:
        raise ValueError("KiCad could not open the source PCB")
    if board.GetCopperLayerCount() != max(2, contract["requested_layers"]):
        raise ValueError("PCB copper count differs from routing contract")
    layers = enabled_layers(board, pcbnew, contract["routing_layers"])
    for item in board.GetTracks():
        if isinstance(item, pcbnew.PCB_VIA):
            if contract["requested_layers"] == 1:
                raise ValueError("Existing via conflicts with one-sided routing")
        elif item.GetLayer() not in layers:
            raise ValueError("Existing track uses a layer excluded by the routing contract")
    pads = [pad for pad in board.GetPads() if pad.GetNetname() == net_name]
    if not 2 <= len(pads) <= 32:
        raise ValueError(f"Routing currently supports 2 to 32 pads per net; {net_name} has {len(pads)}")
    clusters = _pad_clusters(board, pcbnew, pads)
    if len(clusters) < 2:
        raise ValueError("Net pads are already connected")
    connected = list(clusters.pop(0))
    remaining = [pad for cluster in clusters for pad in cluster]
    cluster_by_pad = {pad.m_Uuid.AsString(): cluster for cluster in clusters for pad in cluster}
    segments = vias = visited = 0
    while remaining:
        pairs = []
        for first in connected:
            a = _point(first, pcbnew)
            for second in remaining:
                b = _point(second, pcbnew)
                pairs.append((math.hypot(a[0] - b[0], a[1] - b[1]), first, second))
        _distance, first, second = min(pairs, key=lambda item: item[0])
        new_segments, new_vias, new_visited = _route_pair(
            board, pcbnew, [first, second], net_name, layers, contract)
        segments += new_segments
        vias += new_vias
        visited += new_visited
        joined = cluster_by_pad[second.m_Uuid.AsString()]
        connected.extend(joined)
        joined_ids = {pad.m_Uuid.AsString() for pad in joined}
        remaining = [pad for pad in remaining if pad.m_Uuid.AsString() not in joined_ids]
    pcbnew.SaveBoard(str(destination), board)
    return {"net": net_name, "pads": len(pads), "segments": segments, "vias": vias, "visited": visited,
            "source": str(source), "destination": str(destination)}


def candidate_nets(source):
    import pcbnew
    board = pcbnew.LoadBoard(str(source))
    if board is None:
        raise ValueError("KiCad could not open the source PCB")
    groups = {}
    for pad in board.GetPads():
        if pad.GetNetname():
            groups.setdefault(pad.GetNetname(), []).append(pad)
    candidates = []
    for name, pads in groups.items():
        if 2 <= len(pads) <= 32 and len(_pad_clusters(board, pcbnew, pads)) > 1:
            a, b = (_point(pad, pcbnew) for pad in pads[:2])
            candidates.append((len(pads), math.hypot(a[0] - b[0], a[1] - b[1]), name))
    return [name for _pads, _distance, name in sorted(candidates)]


def saved_board_snapshot(source):
    """Read the saved PCB that the router will actually modify."""
    import pcbnew
    board = pcbnew.LoadBoard(str(source))
    if board is None:
        raise ValueError("KiCad could not open the source PCB")
    try:
        outline = list(board_outline(board, pcbnew)[1])
    except ValueError:
        outline = None
    tracks = list(board.GetTracks())
    vias = [item for item in tracks if isinstance(item, pcbnew.PCB_VIA)]
    footprints = [{"ref": item.GetReference(),
                   "bbox_mm": list(_bbox_mm(item.GetBoundingBox(False), pcbnew))}
                  for item in board.GetFootprints()]
    return {"copper_layers": board.GetCopperLayerCount(), "outline_mm": outline,
            "counts": {"vias": len(vias)},
            "track_layers": sorted({pcbnew.LayerName(item.GetLayer()) for item in tracks
                                    if not isinstance(item, pcbnew.PCB_VIA)}),
            "footprints": footprints, "truncated": False}


if __name__ == "__main__":
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if payload.get("action") == "list":
        print("CAID_CANDIDATES=" + json.dumps(candidate_nets(Path(payload["source"]))))
    elif payload.get("action") == "inspect":
        print("CAID_SNAPSHOT=" + json.dumps(saved_board_snapshot(Path(payload["source"]))))
    else:
        result = route_one(Path(payload["source"]), Path(payload["destination"]),
                           payload["net"], payload["contract"])
        print("CAID_ROUTE=" + json.dumps(result))

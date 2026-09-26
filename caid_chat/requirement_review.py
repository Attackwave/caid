"""Deterministic comparison of machine-checkable requirements with a design model."""

from math import isclose


def review_spec(brief, spec, board_size=None, board_size_mode="exact"):
    rows = []
    components = {item.get("ref"): item for item in spec.get("components", [])
                  if isinstance(item, dict)}
    checks = [(topic, record) for topic, record in brief.get("requirements", {}).items()]
    if board_size is not None:
        checks.append(("PCB size / Platinengröße", {"value": str(board_size),
                       "status": "provided", "check": {"kind": "board_size", "mode": board_size_mode,
                                                       **board_size}}))
    for topic, record in checks:
        check = record.get("check")
        if not check or record["status"] in {"open", "conflict"}:
            rows.append({"topic": topic, "status": "unchecked",
                         "detail": "No automatic design check" if not check else "Requirement unresolved"})
            continue
        kind = check.get("kind")
        if kind == "board_size":
            actual = spec.get("board")
            expected = (check.get("width_mm"), check.get("height_mm"))
            mode = check.get("mode", "exact")
            valid_expected = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                                 for value in expected)
            valid_actual = isinstance(actual, dict) and all(
                isinstance(actual.get(key), (int, float)) and not isinstance(actual[key], bool) and
                actual[key] > 0 for key in ("width_mm", "height_mm"))
            dimensions = (actual.get("width_mm"), actual.get("height_mm")) if valid_actual else ()
            if mode == "maximum":
                passed = (valid_expected and valid_actual and
                          any(all(measured[index] <= expected[index] + 0.001 for index in (0, 1))
                              for measured in (dimensions, dimensions[::-1])))
            elif mode == "exact":
                passed = (valid_expected and valid_actual and
                          any(all(isclose(measured[index], expected[index], abs_tol=0.001)
                                  for index in (0, 1))
                              for measured in (dimensions, dimensions[::-1])))
            else:
                passed = False
            detail = (f"{'Maximum' if mode == 'maximum' else 'Expected'} "
                      f"{expected[0]} × {expected[1]} mm; actual "
                      f"{actual.get('width_mm')} × {actual.get('height_mm')} mm"
                      if isinstance(actual, dict) else f"{'Maximum' if mode == 'maximum' else 'Expected'} "
                      f"{expected[0]} × {expected[1]} mm; no board outline")
        elif kind == "component_side":
            ref, expected = check.get("ref"), check.get("side")
            component = components.get(ref)
            actual = component.get("side", "TOP") if component else None
            passed = actual == expected
            detail = f"{ref}: expected {expected}, actual {actual or 'missing'}"
        else:
            rows.append({"topic": topic, "status": "unchecked",
                         "detail": f"Unsupported check: {kind}"})
            continue
        rows.append({"topic": topic, "status": "pass" if passed else "fail", "detail": detail})
    return rows


def mismatches(rows):
    return [row for row in rows if row["status"] == "fail"]

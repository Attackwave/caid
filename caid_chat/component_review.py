"""Bounded component evidence review for generated KiCad designs."""

import math
from urllib.parse import urlsplit


def review_components(spec, resolved):
    """Record missing evidence and enforce explicit pin and body-height constraints."""
    mechanical = spec.get("mechanical", {})
    if not isinstance(mechanical, dict) or set(mechanical) - {"body_clearance_mm"}:
        raise ValueError("Invalid mechanical limits")
    clearance = mechanical.get("body_clearance_mm", {})
    if not isinstance(clearance, dict) or set(clearance) - {"TOP", "BOTTOM"} or any(
            type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 100
            for value in clearance.values()):
        raise ValueError("Invalid TOP/BOTTOM body clearance")
    result = []
    for ref, item in resolved.items():
        part = item["source"].get("part", {})
        if not isinstance(part, dict) or set(part) - {
                "mpn", "datasheet_url", "package", "pin_map", "body_height_mm", "height_source"}:
            raise ValueError(f"Invalid part evidence for {ref}")
        for field in ("mpn", "datasheet_url", "package", "height_source"):
            value = part.get(field, "")
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError(f"Invalid {field} for {ref}")
        url = part.get("datasheet_url", "")
        if url:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError(f"Datasheet URL must use HTTPS for {ref}")
        pin_map = part.get("pin_map", {})
        if (not isinstance(pin_map, dict) or len(pin_map) > 300 or
                any(not isinstance(pin, str) or not isinstance(name, str) or
                    len(name) > 80 or pin not in item["pins"] for pin, name in pin_map.items())):
            raise ValueError(f"Part pin map contains unknown symbol pins for {ref}")
        height = part.get("body_height_mm")
        if height is not None and (type(height) not in (int, float) or
                                   not math.isfinite(height) or not 0 < height <= 100):
            raise ValueError(f"Invalid body height for {ref}")
        limit = clearance.get(item["side"])
        if height is not None and limit is not None and height > limit:
            raise ValueError(f"{ref} body height {height:g} mm exceeds {item['side']} clearance {limit:g} mm")
        issues = []
        if not part.get("mpn"):
            issues.append("exact manufacturer part number missing")
        if not url:
            issues.append("datasheet URL missing")
        if not part.get("package"):
            issues.append("package drawing identifier missing")
        missing_pins = sorted(set(item["pins"]) - set(pin_map))
        if missing_pins:
            issues.append("datasheet pin map missing symbol pins: " + ", ".join(missing_pins[:12]))
        symbol_names = item.get("pin_names", {})
        name_differences = [pin for pin, name in pin_map.items()
                            if symbol_names.get(pin) and symbol_names[pin].casefold() != name.casefold()]
        if name_differences:
            issues.append("datasheet pin names differ from KiCad symbol at pins: " +
                          ", ".join(sorted(name_differences)[:12]))
        if limit is not None and height is None:
            issues.append(f"body height for {item['side']} clearance not supplied")
        if height is not None and not part.get("height_source"):
            issues.append("body height source missing")
        result.append({"ref": ref, "side": item["side"],
                       "mpn": part.get("mpn", ""), "datasheet_url": url,
                       "package": part.get("package", ""), "body_height_mm": height,
                       "body_clearance_mm": limit, "documented_pins": len(pin_map),
                       "symbol_pins": len(item["pins"]),
                       "symbol_name_differences": name_differences,
                       "issues": issues,
                       "status": "documented" if not issues else "needs_review",
                       "physical_verification": "required"})
    return result

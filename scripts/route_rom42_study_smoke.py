"""Try one DRC-checked net on a separate copy of the ROM42 layout study."""

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from caid_chat.route_project import route_project  # noqa: E402
from caid_chat.routing import default_contract, set_layers, set_limits  # noqa: E402


def main():
    project = Path(sys.argv[1])
    net = sys.argv[2] if len(sys.argv) > 2 else None
    contract = set_limits(set_layers(default_contract(), 2),
                          ["0.2", "0.2", "0.6", "0.3", "0.5"])
    result = route_project(project, project.name + ".kicad_pcb",
                           contract, net_name=net, max_nets=20)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""PostToolUse: dziennik audytowy polecen dotykajacych chmury (ppcall, pac, az, PowerShell Az)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

RELEVANT = re.compile(r"ppcall|(^|[\s/\\])pac(\.exe)?\s|(^|[\s/\\])az(\.cmd)?\s|-Az|PowerApp|PnP", re.I)


def main() -> None:
    try:
        data = json.load(sys.stdin)
        cmd = str((data.get("tool_input") or {}).get("command", ""))
        if RELEVANT.search(cmd + " "):
            resp = data.get("tool_response")
            C.audit({"typ": "polecenie", "narzedzie": data.get("tool_name"), "sesja": data.get("session_id"),
                     "cwd": data.get("cwd"), "polecenie": cmd[:1000],
                     "wynik": (json.dumps(resp, ensure_ascii=False)[:300] if resp is not None else None)})
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()

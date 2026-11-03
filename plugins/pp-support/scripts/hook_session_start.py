"""SessionStart: zapis konfiguracji z ustawien pluginu i kontrola wymagan."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def main() -> None:
    try:
        try:
            data = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            data = {}
        cfg = C.config_from_env()
        C.save_config(cfg)
        C.prune_sessions()
        if not C.session_active(data.get("session_id"), data.get("cwd") or "."):
            sys.exit(0)  # rozmowa poza dyzurem: plugin nic nie dopisuje do kontekstu
        missing = [t for t in ("az", "pac") if not shutil.which(t)]
        lines = [
            "[pp-support] Plugin wsparcia automatyzacji Power Platform jest aktywny.",
            "Tryb pracy: diagnoza tylko odczytem; zmiany wylacznie jako kroki planu zaakceptowanego przez "
            "pracownika (ppcall exec). Szczegoly w skillu pp-support:zgloszenie.",
        ]
        if missing:
            lines.append("Brakujace narzedzia: " + ", ".join(missing) + ". Uruchom /pp-support:sprawdz-dostepy.")
        if not cfg["dataverse_urls"]:
            lines.append("Nie skonfigurowano adresow Dataverse (ustawienia pluginu).")
        lines.extend("Blad ustawien pluginu: " + p for p in C.config_problems(cfg))
        print("\n".join(lines))
    except Exception as e:  # noqa: BLE001
        print(f"[pp-support] Blad inicjalizacji: {e}")
    sys.exit(0)


if __name__ == "__main__":
    main()

"""UserPromptSubmit: rejestracja akceptacji i odwolan.

Ten hook widzi tylko to, co wpisal czlowiek. Dlatego akceptacja powstaje tutaj,
a nie w narzedziach Claude'a: model nie ma drogi, zeby ja wytworzyc.

Format akceptacji (wpisuje pracownik):
    AKCEPTUJĘ <ID> <skrot-planu>
    Przyczyna: <wlasnymi slowami, min. 30 znakow>
    Naprawa: <wlasnymi slowami, min. 30 znakow>

Odwolanie:
    ODWOŁUJĘ <ID>
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

ACCEPT_RE = re.compile(rf"^\s*AKCEPTUJ[EĘ]\s+({C.ID_RE})\s+([0-9a-f]{{8,12}})\s*$", re.I | re.M)
ACCEPT_START_RE = re.compile(r"^\s*AKCEPTUJ[EĘ]\b", re.I)
REVOKE_RE = re.compile(rf"^\s*ODWO[LŁ]UJ[EĘ]\s+({C.ID_RE})\s*$", re.I | re.M)
MIN_LEN = 30


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def field(prompt: str, name: str) -> str:
    m = re.search(rf"^\s*{name}\s*:\s*(.+?)(?=^\s*\w+\s*:|\Z)", prompt, re.I | re.M | re.S)
    return m.group(1).strip() if m else ""


def copied_from_plan(restatement: str, plan_text: str) -> bool:
    """Odrzuca przepisanie zdania z planu. Ma powstac wlasne sformulowanie."""
    r = norm(restatement)
    p = norm(plan_text)
    if len(r) >= 20 and r in p:
        return True
    words = r.split()
    for i in range(0, max(len(words) - 7, 0) + 1):  # 8 kolejnych slow z planu = kopia
        chunk = " ".join(words[i:i + 8])
        if len(words) >= 8 and chunk in p:
            return True
    return False


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def context(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": text}}, ensure_ascii=False))
    sys.exit(0)


def handle(prompt: str, cwd: str) -> None:
    rv = REVOKE_RE.search(prompt)
    if rv:
        removed = C.revoke_approval(rv.group(1))
        C.audit({"typ": "odwolanie", "id": rv.group(1), "przez": "czlowiek"})
        context(f"[pp-support] Pracownik odwolal akceptacje zgloszenia {rv.group(1)}"
                + ("" if removed else " (nie bylo aktywnej)") + ". Nie wykonuj zadnych krokow.")
    if not ACCEPT_START_RE.search(prompt):
        sys.exit(0)
    m = ACCEPT_RE.search(prompt)
    if not m:
        block("Nie rozpoznano akceptacji. Pierwsza linia: AKCEPTUJĘ <ID> <skrot>, "
              "potem linie 'Przyczyna:' i 'Naprawa:' napisane wlasnymi slowami.")
    incident_id, given_hash = m.group(1), m.group(2).lower()
    pp = C.plan_path(cwd, incident_id)
    if not pp.exists():
        block(f"Nie ma planu {pp}. Akceptacja dotyczy planu w katalogu biezacej sesji.")
    text = pp.read_text(encoding="utf-8")
    errs, steps = C.check_plan(text)
    if errs:
        block("Plan nie przechodzi walidacji, nie mozna go zaakceptowac: " + "; ".join(errs))
    current = C.plan_hash(text)
    if not current.startswith(given_hash):
        block(f"Skrot {given_hash} nie odpowiada aktualnej wersji planu. Plan mogl sie zmienic. "
              "Popros Claude'a o ppcall plan-check i przeczytaj plan ponownie.")
    cause, fix = field(prompt, "Przyczyna"), field(prompt, "Naprawa")
    missing = [n for n, v in (("Przyczyna", cause), ("Naprawa", fix)) if len(v) < MIN_LEN]
    if missing:
        block("Akceptacja wymaga opisania wlasnymi slowami (min. "
              f"{MIN_LEN} znakow): {', '.join(missing)}. Chodzi o potwierdzenie, ze rozumiesz problem.")
    copied = [n for n, v in (("Przyczyna", cause), ("Naprawa", fix)) if copied_from_plan(v, text)]
    if copied:
        block(f"Pole {', '.join(copied)} jest przepisane z planu. Opisz to wlasnymi slowami.")
    ttl = int(C.load_config().get("approval_ttl_min") or 60)
    t = C.now()
    approval = {
        "id": incident_id, "hash": current, "uzytkownik": C.who(), "czas": t.isoformat(),
        "wygasa": (t + dt.timedelta(minutes=ttl)).isoformat(), "przyczyna_slowami_pracownika": cause,
        "naprawa_slowami_pracownika": fix, "liczba_krokow": len(steps), "wykonane": [],
    }
    C.write_approval(approval)
    C.audit({"typ": "akceptacja", "id": incident_id, "hash": current, "krokow": len(steps)})
    context(
        f"[pp-support] Pracownik zaakceptowal plan {incident_id} (skrot {current}), waznosc {ttl} min, "
        f"krokow: {len(steps)}.\n"
        f"Jego rozumienie przyczyny: {cause}\nJego rozumienie naprawy: {fix}\n"
        "ZANIM wykonasz krok 1: porownaj to z diagnoza. Jesli pracownik rozumie przyczyne lub naprawe "
        "inaczej niz plan, NIE wykonuj, wyjasnij roznice i zaproponuj ODWOŁUJĘ. "
        "Jesli zgodne, wykonuj kroki po kolei: ppcall exec <ID> <nr>, po kazdym weryfikacja odczytem.")


def main() -> None:
    try:
        data = json.load(sys.stdin)
        handle(str(data.get("prompt", "")), data.get("cwd") or ".")
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        prompt_text = ""
        try:
            prompt_text = str(data.get("prompt", ""))  # type: ignore[name-defined]
        except Exception:
            pass
        if ACCEPT_START_RE.search(prompt_text):
            block(f"pp-support: blad przy rejestracji akceptacji ({e}). Akceptacja nie zostala zapisana.")
        sys.exit(0)


if __name__ == "__main__":
    main()

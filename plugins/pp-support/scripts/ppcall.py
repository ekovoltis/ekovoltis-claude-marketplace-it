"""ppcall: jedyna droga Claude'a do Azure, Power Platform, Dataverse, SharePoint i Fabric.

Odczyt:      ppcall get <url> [--out plik] [--max-kb N]
Dostepy:     ppcall dostepy
Rejestr:     ppcall rejestr <fraza...>
Plan:        ppcall plan-new <ID> | plan-check <ID> | status <ID>
Wykonanie:   ppcall exec <ID> <nr-kroku>     (tylko po akceptacji czlowieka)
Odwolanie:   ppcall revoke <ID>
Konfig:      ppcall config

Tokeny sa pobierane wewnatrz procesu i nigdy nie sa wypisywane.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "skills" / "zgloszenie" / "szablon-planu.md"
REGISTRY = Path(__file__).resolve().parent.parent / "rejestr" / "automatyzacje-snapshot.json"


def out(msg: str = "") -> None:
    print(msg, flush=True)


def fail(msg: str, code: int = 1) -> None:
    print(f"BLAD: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------- tokeny i HTTP

def _az() -> str:
    exe = shutil.which("az")
    if not exe:
        raise RuntimeError("Nie znaleziono Azure CLI (az). Zainstaluj i zaloguj sie: az login")
    return exe


def get_token(resource: str) -> str:
    try:
        r = subprocess.run([_az(), "account", "get-access-token", "--resource", resource,
                            "--query", "accessToken", "-o", "tsv"],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError("az nie odpowiedzial w 60 s")
    if r.returncode != 0 or not r.stdout.strip():
        msg = (r.stderr or "").strip().splitlines()
        raise RuntimeError("az nie wydal tokenu dla " + resource + ": " + (msg[-1] if msg else "brak szczegolow"))
    return r.stdout.strip()


def http(method: str, url: str, body=None, timeout: int = 90) -> tuple[int, bytes]:
    resource = C.resource_for_url(url)
    if not resource:
        raise RuntimeError(f"Host spoza listy dozwolonych, token nie zostanie wyslany: {url}")
    token = get_token(resource)
    data = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json",
               "OData-MaxVersion": "4.0", "OData-Version": "4.0"}
    if "sharepoint.com" in url and "/_api/" in url:
        headers["Accept"] = "application/json;odata=nometadata"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _short(payload: bytes, max_kb: int) -> str:
    text = payload.decode("utf-8", errors="replace")
    try:
        text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
    except Exception:
        pass
    limit = max_kb * 1024
    if len(text) > limit:
        text = text[:limit] + f"\n... [obcieto, calosc {len(payload)} B, uzyj --out]"
    return text


# ---------------------------------------------------------------- komendy

def cmd_get(args: list[str]) -> None:
    if not args:
        fail("Uzycie: ppcall get <url> [--out plik] [--max-kb N]")
    url, out_file, max_kb = args[0], None, 40
    rest = args[1:]
    while rest:
        flag = rest.pop(0)
        if flag == "--out" and rest:
            out_file = rest.pop(0)
        elif flag == "--max-kb" and rest:
            max_kb = int(rest.pop(0))
        else:
            fail(f"Nieznany parametr {flag}")
    try:
        status, payload = http("GET", url)
    except RuntimeError as e:
        fail(str(e))
    C.audit({"typ": "odczyt", "url": url, "status": status})
    out(f"HTTP {status}")
    if out_file:
        Path(out_file).write_bytes(payload)
        out(f"Zapisano {len(payload)} B do {out_file}")
    else:
        out(_short(payload, max_kb))
    if status >= 400:
        sys.exit(2)


def _count(payload: bytes) -> str:
    try:
        j = json.loads(payload)
    except Exception:
        return ""
    if isinstance(j, dict) and isinstance(j.get("value"), list):
        return f"{len(j['value'])} pozycji"
    for k in ("UserId", "displayName", "Title", "webUrl", "name"):
        if isinstance(j, dict) and j.get(k):
            return f"{k}={j[k]}"
    return ""


def cmd_dostepy(_args: list[str]) -> None:
    cfg = C.load_config()
    results: list[dict] = []

    def add(obszar, test, ok, szczegoly, podpowiedz=""):
        results.append({"obszar": obszar, "test": test, "ok": ok,
                        "szczegoly": szczegoly, "podpowiedz": podpowiedz})

    # Azure CLI
    az = shutil.which("az")
    if not az:
        add("Azure", "Azure CLI", False, "brak az w PATH", "Zainstaluj Azure CLI i wykonaj az login")
    else:
        r = subprocess.run([az, "account", "show", "--query", "{u:user.name,t:tenantId,s:name}", "-o", "json"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            j = json.loads(r.stdout)
            add("Azure", "az account show", True, f"{j.get('u')} / tenant {j.get('t')} / sub {j.get('s')}")
        else:
            add("Azure", "az account show", False, "niezalogowany", "Wykonaj az login (robi to czlowiek)")

    # PAC CLI
    pac = shutil.which("pac")
    if not pac:
        add("Power Platform", "PAC CLI", False, "brak pac w PATH",
            "dotnet tool install --global Microsoft.PowerApps.CLI.Tool")
    else:
        r = subprocess.run([pac, "auth", "list"], capture_output=True, text=True)
        active = [l for l in r.stdout.splitlines() if "*" in l]
        add("Power Platform", "pac auth list", r.returncode == 0 and bool(active),
            (active[0].strip() if active else "brak aktywnego profilu"),
            "" if active else "pac auth create --environment <url Dataverse> (robi to czlowiek)")

    def probe(obszar, test, url, hint):
        try:
            status, payload = http("GET", url)
            ok = 200 <= status < 300
            add(obszar, test, ok, f"HTTP {status} {_count(payload) if ok else _short(payload, 1)[:300]}",
                "" if ok else hint)
        except RuntimeError as e:
            add(obszar, test, False, str(e), hint)

    probe("Azure", "ARM: lista subskrypcji",
          "https://management.azure.com/subscriptions?api-version=2022-12-01",
          "Brak roli Reader na subskrypcjach z rozwiazaniami")
    probe("Power Platform", "BAP: srodowiska widoczne dla uzytkownika",
          "https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/environments?api-version=2020-10-01",
          "Sprawdz dostep do srodowisk albo audience tokenu (service.powerapps.com)")
    envs = cfg.get("pp_environment_ids") or []
    if not envs:
        add("Power Automate", "lista flow", False, "nie skonfigurowano pp_environment_ids",
            "Uzupelnij ID srodowisk w konfiguracji pluginu")
    for env in envs:
        probe("Power Automate", f"flow w srodowisku {env}",
              f"https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/{env}/flows?api-version=2016-11-01",
              "Uzytkownik musi byc wspolwlascicielem flow albo adminem srodowiska")
    urls = cfg.get("dataverse_urls") or []
    if not urls:
        add("Dataverse", "WhoAmI", False, "nie skonfigurowano dataverse_urls",
            "Uzupelnij adresy Dataverse w konfiguracji pluginu")
    for u in urls:
        probe("Dataverse", f"WhoAmI {u}", f"{u}/api/data/v9.2/WhoAmI",
              "Brak uzytkownika aplikacyjnego/roli w srodowisku")
        probe("Dataverse", f"flow w rozwiazaniach {u}",
              f"{u}/api/data/v9.2/workflows?$select=name,statecode&$filter=category%20eq%205&$top=5",
              "Rola bez odczytu tabeli workflow")
        probe("Dataverse", f"historia uruchomien (flowruns) {u}",
              f"{u}/api/data/v9.2/flowruns?$select=name,status,starttime&$top=5&$orderby=starttime%20desc",
              "Tabela flowruns moze byc niedostepna w tym srodowisku albo wylaczona")
    host = cfg.get("sharepoint_host") or ""
    site = (cfg.get("sharepoint_site") or "").strip()
    if host:
        graph_site = f"https://graph.microsoft.com/v1.0/sites/{host}" + (f":{site}" if site else "")
        probe("SharePoint", "Graph: witryna", graph_site,
              "Token Azure CLI moze nie miec zakresu Sites.Read.All, rozwazyc wlasna rejestracje aplikacji")
        probe("SharePoint", "REST: witryna",
              f"https://{host}{site}/_api/web?$select=Title,Url",
              "Azure CLI moze nie miec zgody na zasob SharePoint, rozwazyc wlasna rejestracje aplikacji")
    probe("Fabric", "lista obszarow roboczych", "https://api.fabric.microsoft.com/v1/workspaces",
          "Brak roli w obszarach roboczych Fabric albo zablokowane API dla Azure CLI")
    for ws in cfg.get("fabric_workspace_ids") or []:
        probe("Fabric", f"elementy obszaru {ws}", f"https://api.fabric.microsoft.com/v1/workspaces/{ws}/items",
              "Brak roli w tym obszarze roboczym")

    width = max(len(r["test"]) for r in results) + 2
    out(f"{'':2}{'OBSZAR':16}{'TEST':{width}}WYNIK")
    for r in results:
        mark = "OK " if r["ok"] else "NIE"
        out(f"  {r['obszar']:16}{r['test']:{width}}{mark}  {r['szczegoly']}")
        if not r["ok"] and r["podpowiedz"]:
            out(f"  {'':16}{'':{width}}     -> {r['podpowiedz']}")
    report = Path.cwd() / f"dostepy-{C.now().strftime('%Y%m%d-%H%M%S')}.json"
    report.write_text(json.dumps({"czas": C.now().isoformat(), "uzytkownik": C.who(), "wyniki": results},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    C.audit({"typ": "test-dostepow", "ok": sum(r["ok"] for r in results), "razem": len(results)})
    out(f"\nRaport: {report}")


def cmd_rejestr(args: list[str]) -> None:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    words = [w.lower() for w in args]
    out(f"Migawka rejestru z {data['stan_na']}. {data['uwaga']}")
    hits = []
    for r in data["pozycje"]:
        hay = json.dumps(r, ensure_ascii=False).lower()
        if all(w in hay for w in words):
            hits.append(r)
    for r in hits[:15]:
        out("-" * 60)
        for k in ("prefix", "akronim", "nazwa", "status", "rozwiazanie", "trigger", "systemy",
                  "developer", "wlasciciel", "dokumentacja", "opis", "komentarz", "instrukcja"):
            v = r.get(k)
            if v not in (None, "", []):
                out(f"{k:12}: {', '.join(v) if isinstance(v, list) else v}")
    out("-" * 60)
    out(f"Znaleziono {len(hits)}" + (" (pokazano 15)" if len(hits) > 15 else ""))


def cmd_plan_new(args: list[str]) -> None:
    if len(args) != 1 or not C.valid_id(args[0]):
        fail("Uzycie: ppcall plan-new <ID>  (litery, cyfry, - i _, 3 do 40 znakow)")
    d = C.incident_dir(Path.cwd(), args[0])
    d.mkdir(parents=True, exist_ok=True)
    notes = d / "notatki.md"
    if not notes.exists():
        notes.write_text(f"# Zgloszenie {args[0]}: notatki z diagnozy\n\n"
                         "Kazdy wpis: czas, co sprawdzono (URL lub polecenie), wynik, wniosek.\n"
                         "Oznaczaj FAKT albo HIPOTEZA.\n", encoding="utf-8")
    plan = d / "plan.md"
    if not plan.exists():
        plan.write_text(TEMPLATE.read_text(encoding="utf-8").replace("<ID>", args[0]), encoding="utf-8")
    C.audit({"typ": "nowe-zgloszenie", "id": args[0]})
    out(f"Utworzono {d}")


def cmd_plan_check(args: list[str]) -> None:
    if len(args) != 1:
        fail("Uzycie: ppcall plan-check <ID>")
    pp = C.plan_path(Path.cwd(), args[0])
    if not pp.exists():
        fail(f"Brak {pp}")
    text = pp.read_text(encoding="utf-8")
    errs, steps = C.check_plan(text)
    if errs:
        out("Plan NIE jest gotowy do akceptacji:")
        for e in errs:
            out(f"  - {e}")
        sys.exit(2)
    h = C.plan_hash(text)
    out(f"Plan poprawny. Skrot planu: {h}")
    for s in steps:
        out(f"  Krok {s['nr']}: {s['opis']}\n          {C.step_summary(s)}")
    out("\nAkceptacje wpisuje pracownik, wlasnymi slowami, w oknie rozmowy:")
    out(f"  AKCEPTUJĘ {args[0]} {h}\n  Przyczyna: <jak rozumiesz przyczyne>\n  Naprawa: <co dokladnie zostanie zrobione>")


def cmd_status(args: list[str]) -> None:
    if len(args) != 1:
        fail("Uzycie: ppcall status <ID>")
    pp = C.plan_path(Path.cwd(), args[0])
    appr = C.read_approval(args[0])
    out(f"Plan: {'jest' if pp.exists() else 'brak'}")
    if pp.exists():
        text = pp.read_text(encoding="utf-8")
        errs, steps = C.check_plan(text)
        out(f"Skrot aktualnego planu: {C.plan_hash(text)}; krokow: {len(steps)}; bledow walidacji: {len(errs)}")
    if not appr:
        out("Akceptacja: brak")
        return
    out(f"Akceptacja: skrot {appr['hash']}, {appr['uzytkownik']}, {appr['czas']}, wygasa {appr['wygasa']}")
    out(f"Wykonane kroki: {appr.get('wykonane', [])}")
    if appr.get("zatrzymano"):
        out(f"ZATRZYMANO na kroku {appr['zatrzymano']}")


def cmd_exec(args: list[str]) -> None:
    if len(args) != 2 or not args[1].isdigit():
        fail("Uzycie: ppcall exec <ID> <nr-kroku>")
    incident_id, nr = args[0], int(args[1])
    ok, reason, step = C.can_execute(Path.cwd(), incident_id, nr)
    if not ok:
        C.audit({"typ": "odmowa-wykonania", "id": incident_id, "krok": nr, "powod": reason})
        fail(reason, 3)
    appr = C.read_approval(incident_id)
    log = C.incident_dir(Path.cwd(), incident_id) / "wykonanie.jsonl"
    entry = {"czas": C.now().isoformat(), "uzytkownik": C.who(), "krok": nr, "opis": step["opis"],
             "operacja": C.step_summary(step), "skrot_planu": appr["hash"]}
    success = False
    try:
        if step["typ"] == "http":
            status, payload = http(step["metoda"].upper(), step["url"], step.get("body"))
            entry.update({"status": status, "odpowiedz": _short(payload, 4)})
            success = 200 <= status < 300
            out(f"HTTP {status}\n{_short(payload, 20)}")
        else:
            exe = shutil.which(step["argv"][0])
            if not exe:
                raise RuntimeError(f"Nie znaleziono {step['argv'][0]}")
            r = subprocess.run([exe, *step["argv"][1:]], capture_output=True, text=True, timeout=600)
            entry.update({"kod": r.returncode, "stdout": r.stdout[-4000:], "stderr": r.stderr[-2000:]})
            success = r.returncode == 0
            out(r.stdout[-8000:])
            if r.stderr:
                out(r.stderr[-2000:])
    except Exception as e:  # noqa: BLE001
        entry["wyjatek"] = str(e)
        out(f"Wyjatek: {e}")
    entry["sukces"] = success
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if success:
        appr.setdefault("wykonane", []).append(nr)
    else:
        appr["zatrzymano"] = nr
    C.write_approval(appr)
    C.audit({"typ": "wykonanie", "id": incident_id, "krok": nr, "sukces": success, "operacja": entry["operacja"]})
    if not success:
        out("\nKROK NIEUDANY. Wykonanie planu zatrzymane. Nie improwizuj: wroc do diagnozy z pracownikiem.")
        sys.exit(4)
    out(f"\nKrok {nr} wykonany. Zweryfikuj efekt odczytem zanim przejdziesz dalej.")


def cmd_revoke(args: list[str]) -> None:
    if len(args) != 1:
        fail("Uzycie: ppcall revoke <ID>")
    removed = C.revoke_approval(args[0])
    C.audit({"typ": "odwolanie", "id": args[0], "przez": "ppcall"})
    out("Akceptacja odwolana." if removed else "Nie bylo aktywnej akceptacji.")


def cmd_config(_args: list[str]) -> None:
    out(json.dumps(C.load_config(), ensure_ascii=False, indent=2))


COMMANDS = {
    "get": cmd_get, "dostepy": cmd_dostepy, "rejestr": cmd_rejestr,
    "plan-new": cmd_plan_new, "plan-check": cmd_plan_check, "status": cmd_status,
    "exec": cmd_exec, "revoke": cmd_revoke, "config": cmd_config,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        out(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])

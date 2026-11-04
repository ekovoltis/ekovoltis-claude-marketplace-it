"""ppcall: jedyna droga Claude'a do Azure, Power Platform, Dataverse, SharePoint i Fabric.

Odczyt:      ppcall get <url> [--out plik] [--max-kb N] [--all [--max-pages N]]
Przebiegi:   ppcall runs <env-id> <flow> --out plik.jsonl [--od RRRR-MM-DD] [--do RRRR-MM-DD] [--max N]
                         [--akcje] [--wejscia A,B] [--wyjscia A,B]   (trigger = wyzwalacz)
             ppcall runs <adres Dataverse> <workflowid> --out ...    (srodowisko ustali ppcall)
Weryfikacja: ppcall verify <ID>
Dostepy:     ppcall dostepy
Rejestr:     ppcall rejestr <fraza...> | ppcall rejestr --flow <adres Dataverse> <workflowid>
Plan:        ppcall plan-new <ID> | plan-check <ID> | status <ID>
Wykonanie:   ppcall exec <ID> <nr-kroku>     (tylko po akceptacji czlowieka)
Odwolanie:   ppcall revoke <ID>
Konfig:      ppcall konfiguracja   (albo config; tylko odczyt)

Tokeny sa pobierane wewnatrz procesu i nigdy nie sa wypisywane.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import shutil
import stat
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.client import InvalidURL
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "skills" / "zgloszenie" / "szablon-planu.md"
REGISTRY = Path(__file__).resolve().parent.parent / "rejestr" / "automatyzacje-snapshot.json"


for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def out(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except OSError:
        # Konsola Windows potrafi odrzucic zapis (Errno 22); plik --out jest juz wtedy zapisany.
        try:
            sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        except OSError:
            pass


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


def encode_url(url: str) -> str:
    """Spacje i polskie znaki w adresie (np. w $filter) koduje procentowo, reszte zostawia."""
    return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Dane logowania Salesbooka nie moga pojsc za przekierowaniem na inny adres."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _find_action(actions: dict, name: str):
    for key, action in (actions or {}).items():
        if key == name:
            return action
        if not isinstance(action, dict):
            continue
        nested = [action.get("actions"), (action.get("else") or {}).get("actions"),
                  (action.get("default") or {}).get("actions")]
        nested += [(c or {}).get("actions") for c in (action.get("cases") or {}).values()]
        for block in nested:
            found = _find_action(block or {}, name)
            if found is not None:
                return found
    return None


_SALESBOOK_AUTH: dict = {}


def salesbook_auth() -> str:
    """Naglowek Basic dla API Salesbooka z akcji HTTP flow integracji (decyzja z 2026-11-03).
    Pobierany w tym procesie i nigdzie nie wypisywany."""
    if "value" in _SALESBOOK_AUTH:
        return _SALESBOOK_AUTH["value"]
    src = C.salesbook_auth_source(C.load_config().get("salesbook_auth_flow") or "")
    if not src:
        raise RuntimeError("Odczyt z Salesbooka jest wylaczony: brak ustawienia salesbook_auth_flow.")
    dv, wf, action_name = src
    status, payload = http("GET", f"{dv}/api/data/v9.2/workflows({wf})?$select=clientdata")
    if status != 200:
        raise RuntimeError(f"Nie mozna odczytac flow z danymi logowania Salesbooka: HTTP {status}")
    definition = json.loads(json.loads(payload)["clientdata"])
    action = _find_action(definition["properties"]["definition"]["actions"], action_name)
    auth = ((action or {}).get("inputs") or {}).get("authentication") or {}
    if str(auth.get("type", "")).lower() != "basic" or not auth.get("username") or not auth.get("password"):
        raise RuntimeError(f"Akcja {action_name} nie ma danych logowania Basic.")
    token = base64.b64encode(f"{auth['username']}:{auth['password']}".encode("utf-8")).decode("ascii")
    _SALESBOOK_AUTH["value"] = f"Basic {token}"
    return _SALESBOOK_AUTH["value"]


def _salesbook_get(url: str, timeout: int) -> tuple[int, bytes]:
    """GET do Salesbooka: dane logowania tylko pod skonfigurowany adres API, bez przekierowan."""
    api = (C.load_config().get("salesbook_api_url") or "").rstrip("/")
    if not api:
        raise RuntimeError("Odczyt z Salesbooka jest wylaczony: brak ustawienia salesbook_api_url.")
    send = {"Accept": "application/json"}
    target, base = urllib.parse.urlparse(url), urllib.parse.urlparse(api)
    segments = urllib.parse.unquote(target.path).split("/")
    if ".." in segments or "." in segments:
        raise RuntimeError("Salesbook: sciezka z '..' albo '.' jest odrzucana.")
    if ((target.hostname or "").lower() == (base.hostname or "").lower()
            and target.path.startswith(base.path.rstrip("/") + "/")):
        send["Authorization"] = salesbook_auth()
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, method="GET", headers=send), timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def http(method: str, url: str, body=None, timeout: int = 90, headers: dict | None = None) -> tuple[int, bytes]:
    url = encode_url(url)
    send = {"Accept": "application/json"}
    try:
        run_content = C.is_run_content_url(url)
        salesbook = C.is_salesbook_url(url)
        C.resource_for_url(url)
    except ValueError as e:
        raise RuntimeError(f"Niepoprawny adres URL ({e})")
    if salesbook:
        if method != "GET" or body is not None:
            raise RuntimeError("Salesbook: dozwolony tylko odczyt (GET).")
        try:
            return _salesbook_get(url, timeout)
        except (ValueError, InvalidURL) as e:
            raise RuntimeError(f"Niepoprawny adres URL ({e})")
    if run_content:
        # Podpis SAS jest w adresie: token nie jest potrzebny i nie moze tam trafic.
        if method != "GET":
            raise RuntimeError("Tresc przebiegu flow mozna tylko odczytac (GET).")
    else:
        resource = C.resource_for_url(url)
        if not resource:
            raise RuntimeError(f"Host spoza listy dozwolonych, token nie zostanie wyslany: {url}")
        bearer = f"Bearer {get_token(resource)}"
        send.update({"OData-MaxVersion": "4.0", "OData-Version": "4.0"})
        if "sharepoint.com" in url and "/_api/" in url:
            send["Accept"] = "application/json;odata=nometadata"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        send["Content-Type"] = "application/json; charset=utf-8"
    send.update(headers or {})
    try:
        req = urllib.request.Request(url, data=data, method=method, headers=send)
        if not run_content:
            # Naglowek "unredirected": przy przekierowaniu na inny adres token nie idzie dalej.
            req.add_unredirected_header("Authorization", bearer)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (ValueError, InvalidURL) as e:
        raise RuntimeError(f"Niepoprawny adres URL ({e}): {C.redact_url(url)}")


def redact_payload(payload: bytes) -> bytes:
    """Odpowiedz bez hasel (np. danych logowania w definicji flow). Pliki binarne bez zmian."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload
    try:
        data = json.loads(text)
    except ValueError:
        red = C.redact_text(text)
        return payload if red == text else red.encode("utf-8")
    redacted = C.redact_secrets(data)
    return payload if redacted == data else json.dumps(redacted, ensure_ascii=False).encode("utf-8")


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

DATAVERSE_READ_HEADERS = {"Prefer": 'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'}


def read_headers(url: str) -> dict:
    """Odczyt z Dataverse z nazwami wartosci (lookupy, opcje), nie tylko GUID i liczbami."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return dict(DATAVERSE_READ_HEADERS) if C.DATAVERSE_HOST_RE.match(host) else {}


def get_all(url: str, max_pages: int) -> tuple[int, bytes]:
    """Laczy strony wyniku (@odata.nextLink albo nextLink) w jedna liste value."""
    host = urllib.parse.urlparse(url).hostname
    items, pages, next_url = [], 0, url
    while next_url and pages < max_pages:
        if urllib.parse.urlparse(next_url).hostname != host:
            raise RuntimeError("Nastepna strona wyniku wskazuje inny host, przerwano.")
        status, payload = http("GET", next_url, headers=read_headers(next_url))
        if status >= 400:
            return status, payload
        page = json.loads(payload)
        if not isinstance(page, dict):
            raise RuntimeError("Odpowiedz nie jest obiektem z lista value, --all nie ma czego laczyc.")
        items.extend(page.get("value") or [])
        pages += 1
        next_url = page.get("@odata.nextLink") or page.get("nextLink")
    result = {"value": items, "strony": pages, "niepelne": bool(next_url)}
    return 200, json.dumps(result, ensure_ascii=False).encode("utf-8")


def cmd_get(args: list[str]) -> None:
    if not args:
        fail("Uzycie: ppcall get <url> [--out plik] [--max-kb N] [--all [--max-pages N]]")
    url, out_file, max_kb, all_pages, max_pages = args[0], None, 40, False, 50
    rest = args[1:]
    while rest:
        flag = rest.pop(0)
        if flag == "--out" and rest:
            out_file = rest.pop(0)
        elif flag == "--max-kb" and rest:
            max_kb = int(rest.pop(0))
        elif flag == "--all":
            all_pages = True
        elif flag == "--max-pages" and rest:
            max_pages = int(rest.pop(0))
        else:
            fail(f"Nieznany parametr {flag}")
    if out_file is not None:
        problem = C.out_path_problem(Path.cwd(), out_file)
        if problem:
            C.audit({"typ": "odmowa-zapisu", "url": C.redact_url(url), "out": out_file, "powod": problem})
            fail(problem, 3)
    try:
        status, payload = get_all(url, max_pages) if all_pages else http("GET", url, headers=read_headers(url))
    except (RuntimeError, ValueError) as e:
        fail(str(e))
    C.audit({"typ": "odczyt", "url": C.redact_url(url), "status": status, "wszystkie_strony": all_pages})
    payload = redact_payload(payload)
    out(f"HTTP {status}")
    if out_file:
        Path(out_file).write_bytes(payload)
        out(f"Zapisano {len(payload)} B do {out_file}")
    else:
        out(_short(payload, max_kb))
    if status >= 400:
        sys.exit(2)


FLOW_API = "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments"


RUN_NAME_RE = re.compile(r"[A-Za-z0-9]{1,80}")


def _run_content(link) -> object:
    """Tresc z inputsLink/outputsLink. Tylko adres z podpisem SAS: taki nie dostaje tokenu."""
    uri = (link or {}).get("uri") if isinstance(link, dict) else None
    if not uri:
        return None
    if not C.is_run_content_url(uri):
        return {"blad": "adres spoza tresci przebiegu, pominieto"}
    try:
        status, payload = http("GET", uri)
    except RuntimeError as e:
        return {"blad": str(e)}
    if status != 200:
        return {"blad": f"HTTP {status}"}
    try:
        return C.redact_secrets(json.loads(payload))
    except ValueError:
        return C.redact_text(payload.decode("utf-8", errors="replace"))


def _run_record(run: dict, detail: dict | None, inputs: set, outputs: set) -> dict:
    props = (detail or run).get("properties", {})
    rec = {"name": run.get("name"), "startTime": props.get("startTime"), "endTime": props.get("endTime"),
           "status": props.get("status"), "error": C.redact_secrets(props.get("error"))}
    if detail is None:
        return rec
    steps = {"trigger": props.get("trigger") or {}, **(props.get("actions") or {})}
    rec["akcje"] = {}
    for name, a in steps.items():
        item = {"status": a.get("status"), "code": a.get("code"), "error": C.redact_secrets(a.get("error"))}
        if name in inputs:
            item["wejscia"] = _run_content(a.get("inputsLink"))
        if name in outputs:
            item["wyjscia"] = _run_content(a.get("outputsLink"))
        rec["akcje"][name] = item
    return rec


BAP_ENVIRONMENTS = ("https://api.bap.microsoft.com/providers/Microsoft.BusinessAppPlatform/environments"
                    "?api-version=2020-10-01")


def resolve_flow(dataverse: str, workflowid: str) -> tuple[str, str]:
    """Srodowisko Power Automate dla adresu Dataverse (BAP) i identyfikator flow w Flow API.
    Flow z rozwiazania moze miec w Flow API inny identyfikator niz workflowid: probujemy kolejno."""
    dataverse = dataverse.rstrip("/")
    if dataverse.lower() not in [u.rstrip("/").lower() for u in C.load_config().get("dataverse_urls") or []]:
        raise RuntimeError(f"{dataverse} nie jest na liscie dataverse_urls w ustawieniach pluginu.")
    status, payload = http("GET", BAP_ENVIRONMENTS)
    if status != 200:
        raise RuntimeError(f"Lista srodowisk (BAP): HTTP {status}")
    env = None
    for e in json.loads(payload).get("value") or []:
        url = (((e.get("properties") or {}).get("linkedEnvironmentMetadata") or {}).get("instanceUrl") or "")
        if url.rstrip("/").lower() == dataverse.lower():
            env = e.get("name")
            break
    if not env or not C.GUID_RE.fullmatch(str(env).replace("Default-", "")):
        raise RuntimeError(f"Nie znaleziono srodowiska Power Platform dla {dataverse}.")
    candidates = [workflowid]
    status, payload = http("GET", f"{dataverse}/api/data/v9.2/workflows({workflowid})"
                                  "?$select=name,workflowidunique,resourceid")
    if status == 200:
        rec = json.loads(payload)
        candidates += [str(rec[k]) for k in ("resourceid", "workflowidunique")
                       if rec.get(k) and C.GUID_RE.fullmatch(str(rec[k])) and str(rec[k]) not in candidates]
    for cand in candidates:
        st, _ = http("GET", f"{FLOW_API}/{env}/flows/{cand}?api-version=2016-11-01")
        if st == 200:
            return env, cand
    raise RuntimeError(f"Flow {workflowid} nie jest widoczny w Flow API srodowiska {env} "
                       f"(sprawdzone identyfikatory: {', '.join(candidates)}).")


def cmd_runs(args: list[str]) -> None:
    usage = ("Uzycie: ppcall runs <env-id> <flow> --out plik.jsonl [--od RRRR-MM-DD] [--do RRRR-MM-DD] [--max N] "
             "[--akcje] [--wejscia A,B] [--wyjscia A,B]\n"
             "   albo: ppcall runs <adres Dataverse> <workflowid> --out ... (srodowisko ustali ppcall)")
    if len(args) < 2 or not C.GUID_RE.fullmatch(args[1]):
        fail(usage)
    if args[0].lower().startswith("https://"):
        try:
            env, flow = resolve_flow(args[0], args[1])
        except (RuntimeError, ValueError) as e:
            fail(str(e))
        out(f"Srodowisko {env}, flow {flow}")
    elif C.GUID_RE.fullmatch(args[0].replace("Default-", "")):
        env, flow = args[0], args[1]
    else:
        fail(usage)
    rest = args[2:]
    out_file, since, until, limit, detail, inputs, outputs = None, "", "", 1000, False, set(), set()
    while rest:
        flag = rest.pop(0)
        if flag == "--out" and rest:
            out_file = rest.pop(0)
        elif flag == "--od" and rest:
            since = rest.pop(0)
        elif flag == "--do" and rest:
            until = rest.pop(0)
        elif flag == "--max" and rest:
            limit = int(rest.pop(0))
        elif flag == "--akcje":
            detail = True
        elif flag in ("--wejscia", "--wyjscia") and rest:
            names = {n.strip() for n in rest.pop(0).split(",") if n.strip()}
            (inputs if flag == "--wejscia" else outputs).update(names)
            detail = True
        else:
            fail(f"Nieznany parametr {flag}. {usage}")
    if not out_file:
        fail("ppcall runs wymaga --out (plik w katalogu zgloszenia).")
    problem = C.out_path_problem(Path.cwd(), out_file)
    if problem:
        C.audit({"typ": "odmowa-zapisu", "out": out_file, "powod": problem})
        fail(problem, 3)
    base = f"{FLOW_API}/{env}/flows/{flow}/runs"
    next_url, count = f"{base}?api-version=2016-11-01", 0
    partial = Path(out_file + ".czesc")
    try:
        with open(partial, "w", encoding="utf-8") as f:
            while next_url and count < limit:
                status, payload = http("GET", next_url)
                if status != 200:
                    fail(f"Lista przebiegow: HTTP {status} {_short(redact_payload(payload), 1)[:300]}", 2)
                page = json.loads(payload)
                for run in page.get("value") or []:
                    if not RUN_NAME_RE.fullmatch(str(run.get("name", ""))):
                        continue
                    start = (run.get("properties") or {}).get("startTime") or ""
                    if until and start[:10] > until:
                        continue
                    if since and start[:10] < since:
                        next_url = None  # przebiegi sa od najnowszego, dalej sa juz starsze
                        break
                    full = None
                    if detail:
                        s2, p2 = http("GET", f"{base}/{run['name']}?api-version=2016-11-01"
                                             "&$expand=properties/actions,properties/trigger")
                        full = json.loads(p2) if s2 == 200 else {"properties": {"blad_odczytu": s2}}
                    f.write(json.dumps(_run_record(run, full, inputs, outputs), ensure_ascii=False) + "\n")
                    count += 1
                    if count >= limit:
                        break
                else:
                    next_url = page.get("nextLink")
                    if next_url and not next_url.startswith(base + "?"):
                        fail("Nastepna strona przebiegow wskazuje inny adres, przerwano.", 2)
    except (RuntimeError, ValueError) as e:
        fail(str(e))
    os.replace(partial, out_file)
    C.audit({"typ": "odczyt-przebiegow", "srodowisko": env, "flow": flow, "przebiegi": count,
             "wejscia": sorted(inputs), "wyjscia": sorted(outputs)})
    out(f"Zapisano {count} przebiegow do {out_file}")


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

    problems = C.config_problems(cfg)
    for p in problems:
        add("Konfiguracja", "ustawienia pluginu", False, p, "Popraw ustawienie w /plugin i uruchom nowa sesje")

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
    # Przy blednych ustawieniach SharePoint test pokazalby blad uprawnien zamiast bledu konfiguracji.
    if host and not any(p.startswith("sharepoint") for p in problems):
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


def _registry_exact(r: dict, words: list[str]) -> int:
    """Ile slow trafia dokladnie w prefiks albo akronim (007 i AKDW przed 017 i AKDWT)."""
    keys = {str(r.get(k) or "").lower() for k in ("prefix", "akronim")}
    return sum(1 for w in words if w in keys)


def cmd_rejestr(args: list[str]) -> None:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if args[:1] == ["--flow"]:
        if len(args) != 3 or not C.GUID_RE.fullmatch(args[2]):
            fail("Uzycie: ppcall rejestr --flow <adres Dataverse> <workflowid>")
        dataverse = args[1].rstrip("/")
        if dataverse.lower() not in [u.rstrip("/").lower() for u in C.load_config().get("dataverse_urls") or []]:
            fail(f"{dataverse} nie jest na liscie dataverse_urls w ustawieniach pluginu.")
        try:
            status, payload = http("GET", f"{dataverse}/api/data/v9.2/workflows({args[2]})?$select=name")
        except RuntimeError as e:
            fail(str(e))
        if status != 200:
            fail(f"Odczyt nazwy flow: HTTP {status}")
        name = str(json.loads(payload).get("name") or "")
        m = re.match(r"\s*(?:\[[^\]]*\]\s*)?(\d{2,4})(?!\d)", name)
        out(f"Flow: {name}")
        if not m:
            fail("Nazwa flow nie zaczyna sie od numeru automatu, szukaj slowami: ppcall rejestr <slowa>")
        args = [m.group(1)]
    words = [w.lower() for w in args]
    out(f"Migawka rejestru z {data['stan_na']}. {data['uwaga']}")
    hits = []
    for r in data["pozycje"]:
        hay = json.dumps(r, ensure_ascii=False).lower()
        if all(w in hay for w in words):
            hits.append(r)
    hits.sort(key=lambda r: -_registry_exact(r, words))
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


def workflow_url(step: dict) -> str:
    return f"{str(step['srodowisko']).rstrip('/')}/api/data/v9.2/workflows({step['workflowid']})"


def read_workflow(step: dict) -> dict:
    url = workflow_url(step) + "?$select=name,clientdata,modifiedon,statecode,statuscode,ismanaged,category"
    status, payload = http("GET", url)
    if status != 200:
        raise RuntimeError(f"Odczyt flow {step['workflowid']}: HTTP {status} {_short(payload, 1)[:300]}")
    return json.loads(payload)


def reverse_step(step: dict) -> dict:
    return {"nr": 1, "opis": "Wycofanie: " + str(step.get("opis", "")), "typ": "flow-patch",
            "srodowisko": step["srodowisko"], "workflowid": step["workflowid"],
            "oczekiwany_modifiedon": "<modifiedon_po z wykonanie.jsonl>",
            "zmiany": [{"sciezka": c["sciezka"], "bylo": c["ma_byc"], "ma_byc": c["bylo"]}
                       for c in step["zmiany"]]}


def flow_patch_preview(step: dict) -> list[str]:
    """Sprawdza krok na aktualnej definicji i wypisuje roznice. Zwraca bledy."""
    try:
        workflow = read_workflow(step)
    except RuntimeError as e:
        return [str(e)]
    errs, _ = C.flow_patch_live_problems(step, workflow)
    out(f"  Flow: {workflow.get('name')} (modifiedon {workflow.get('modifiedon')}, statecode {workflow.get('statecode')})")
    for ch in step["zmiany"]:
        out(f"  - {C.flow_patch_label(ch['sciezka'])}\n      sciezka: {' / '.join(ch['sciezka'])}"
            f"\n      bylo:    {C.value_text(ch['bylo'], 4000)}\n      ma byc:  {C.value_text(ch['ma_byc'], 4000)}")
    if not errs:
        out("  Krok wycofania (nowy plan z nowa akceptacja, gdy bedzie potrzebny):")
        out("  " + json.dumps(reverse_step(step), ensure_ascii=False))
    return errs


def cmd_plan_check(args: list[str]) -> None:
    if len(args) != 1:
        fail("Uzycie: ppcall plan-check <ID>")
    pp = C.plan_path(Path.cwd(), args[0])
    if not pp.exists():
        fail(f"Brak {pp}")
    text = pp.read_text(encoding="utf-8")
    errs, steps = C.check_plan(text, pp.parent)
    if errs:
        out("Plan NIE jest gotowy do akceptacji:")
        for e in errs:
            out(f"  - {e}")
        sys.exit(2)
    h = C.plan_hash(text)
    live_errs = []
    for s in steps:
        out(f"  Krok {s['nr']}: {s['opis']}\n          {C.step_summary(s)}")
        if s["typ"] == "flow-patch":
            live_errs.extend(f"krok {s['nr']}: {e}" for e in flow_patch_preview(s))
        elif s["typ"] == "flow-definition":
            live_errs.extend(f"krok {s['nr']}: {e}" for e in flow_definition_preview(s, pp.parent))
    if live_errs:
        out("Plan NIE jest gotowy do akceptacji, aktualna definicja flow nie zgadza sie z planem:")
        for e in live_errs:
            out(f"  - {e}")
        sys.exit(2)
    out(f"Plan poprawny. Skrot planu: {h}")
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
        errs, steps = C.check_plan(text, pp.parent)
        out(f"Skrot aktualnego planu: {C.plan_hash(text)}; krokow: {len(steps)}; bledow walidacji: {len(errs)}")
    if not appr:
        out("Akceptacja: brak")
        return
    out(f"Akceptacja: skrot {appr['hash']}, {appr['uzytkownik']}, {appr['czas']}, wygasa {appr['wygasa']}")
    out(f"Wykonane kroki: {appr.get('wykonane', [])}")
    if appr.get("zatrzymano"):
        out(f"ZATRZYMANO na kroku {appr['zatrzymano']}")


def log_entry(incident_id: str, entry: dict) -> None:
    with open(C.incident_dir(Path.cwd(), incident_id) / "wykonanie.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def restore_definition(step: dict, before: dict, definition: dict) -> bool:
    """Przywraca definicje sprzed zapisu i sprawdza wynik. Nie rzuca wyjatku."""
    try:
        try:
            etag = read_workflow(step)["@odata.etag"]
        except Exception:  # noqa: BLE001
            etag = "*"
        status, _ = http("PATCH", workflow_url(step), {"clientdata": before["clientdata"]}, headers={"If-Match": etag})
        return status < 300 and not C.leaf_diff(definition, json.loads(read_workflow(step)["clientdata"]))
    except Exception:  # noqa: BLE001
        return False


def exec_flow_patch(incident_id: str, step: dict, entry: dict) -> bool:
    """Zapis definicji flow z kopia, If-Match i porownaniem po zapisie. Zwraca sukces."""
    before = read_workflow(step)
    errs, definition = C.flow_patch_live_problems(step, before)
    if errs:
        entry["odmowa"] = errs
        out("Flow nie zgadza sie z zaakceptowanym planem, nic nie zapisano:\n  - " + "\n  - ".join(errs))
        return False
    backup = backup_path(incident_id, step)
    write_backup(step, backup, before)
    entry.update({"kopia": str(backup), "modifiedon_przed": before.get("modifiedon"),
                  "zmiany": step["zmiany"]})
    changed = copy.deepcopy(definition)
    for ch in step["zmiany"]:
        C.set_at(changed, ch["sciezka"], ch["ma_byc"])
    log_entry(incident_id, {**entry, "etap": "zapis-rozpoczety"})
    status, payload = http("PATCH", workflow_url(step), {"clientdata": json.dumps(changed, ensure_ascii=False)},
                           headers={"If-Match": before["@odata.etag"]})
    entry["status"] = status
    if not 200 <= status < 300:
        entry["odpowiedz"] = _short(payload, 4)
        out(f"Zapis odrzucony: HTTP {status}\n{_short(payload, 4)}")
        return False
    declared = {tuple(ch["sciezka"]) for ch in step["zmiany"]}
    try:
        after = read_workflow(step)
        written = json.loads(after["clientdata"])
        diff = set(C.leaf_diff(definition, written))
        entry["modifiedon_po"] = after.get("modifiedon")
        unexpected = {d for d in diff if not any(d[:len(p)] == p for p in declared)}
        ok = not unexpected and all(C.canon(C.get_at(written, ch["sciezka"])) == C.canon(ch["ma_byc"])
                                    for ch in step["zmiany"])
        entry["nieoczekiwane_roznice"] = [" / ".join(d) for d in sorted(unexpected)][:50]
    except Exception as e:  # noqa: BLE001
        ok = False
        entry["wyjatek_weryfikacji"] = str(e)
    if not ok:
        entry["przywrocono"] = restore_definition(step, before, definition)
        out("Definicja po zapisie nie zgadza sie z planem albo nie dala sie sprawdzic"
            + (". Przywrocono kopie." if entry["przywrocono"] else f". PRZYWROCENIE NIEUDANE, kopia: {backup}"))
        return False
    # Zmiana stanu nie jest zmiana definicji: definicja zostaje, plan sie zatrzymuje, decyzje podejmuje czlowiek.
    if (after.get("statecode"), after.get("statuscode")) != (before.get("statecode"), before.get("statuscode")):
        entry["stan_flow"] = [before.get("statecode"), after.get("statecode")]
        out(f"Definicja zapisana, ale stan flow zmienil sie z {before.get('statecode')} na {after.get('statecode')}.")
        return False
    out(f"Zapisano {len(declared)} zmian(y) w {after.get('name')}. Porownanie po zapisie: tylko zadeklarowane sciezki. "
        f"Kopia: {backup}")
    return True


def backup_path(incident_id: str, step: dict) -> Path:
    """Nowy plik kopii; przy dwoch zapisach w tej samej sekundzie z numerem, nigdy nadpisanie."""
    folder = C.incident_dir(Path.cwd(), incident_id) / C.BACKUP_DIR_NAME
    base = f"{step['workflowid']}-{C.now().strftime('%Y%m%dT%H%M%SZ')}"
    path, n = folder / f"{base}.json", 1
    while path.exists():
        path, n = folder / f"{base}-{n}.json", n + 1
    return path


def write_backup(step: dict, backup: Path, before: dict) -> str:
    """Kopia definicji sprzed zapisu; zwraca jej skrot (z tych samych bajtow, ktore trafily do pliku)."""
    backup.parent.mkdir(parents=True, exist_ok=True)
    data = before["clientdata"].encode("utf-8")
    backup.write_bytes(data)
    os.chmod(backup, stat.S_IREAD)
    sha = C.hashlib.sha256(data).hexdigest()
    C.register_backup(step["workflowid"], backup, sha)
    return sha


def load_new_definition(step: dict, folder: Path) -> tuple[dict | None, bytes, str | None]:
    """Plik nowej definicji czytany raz: skrot liczony z tych samych bajtow, ktore ida do zapisu."""
    path = folder / step["plik"]
    try:
        raw = path.read_bytes()
    except OSError as e:
        return None, b"", f"nie mozna odczytac {step['plik']}: {e}"
    if C.hashlib.sha256(raw).hexdigest() != step.get("sha256"):
        return None, raw, f"plik {step['plik']} rozni sie od zaakceptowanego (inny skrot)"
    try:
        return json.loads(raw.decode("utf-8")), raw, None
    except (ValueError, UnicodeDecodeError):
        return None, raw, f"plik {step['plik']} nie jest poprawnym JSON"


def _restoring(step: dict) -> bool:
    return (str(step.get("plik", "")).replace("\\", "/").lower().startswith(C.BACKUP_DIR_NAME + "/")
            and C.is_registered_backup(step["workflowid"], str(step.get("sha256", ""))))


def describe_changes(changes: list[dict]) -> list[str]:
    lines = []
    for c in changes:
        where = c.get("zakres") or "glowny zakres"
        line = f"{c['zmiana']}: {c['akcja']} ({c.get('typ')}) w {where}"
        if c.get("z_zakresu") is not None:
            line += f", wczesniej w {c['z_zakresu'] or 'glowny zakres'}"
        if c.get("klucze"):
            line += f"; klucze: {', '.join(c['klucze'])}"
        if c.get("podglad"):
            line += f"\n      tresc: {c['podglad']}"
        lines.append(line)
    return lines


def flow_definition_preview(step: dict, folder: Path) -> list[str]:
    """Sprawdza nowa definicje wobec aktualnej i wypisuje zmiany akcji. Zwraca bledy."""
    new, _, problem = load_new_definition(step, folder)
    if problem:
        return [problem]
    try:
        workflow = read_workflow(step)
    except RuntimeError as e:
        return [str(e)]
    restoring = _restoring(step)
    errs, _, changes = C.flow_definition_live_problems(step, workflow, new, restoring)
    out(f"  Flow: {workflow.get('name')} (modifiedon {workflow.get('modifiedon')}, statecode {workflow.get('statecode')})")
    if restoring:
        out("  Plik to kopia zapisana przez ppcall exec: powrot do poprzedniej definicji.")
    for line in describe_changes(changes):
        out(f"  - {line}")
    if not errs:
        out("  Wycofanie (nowy plan z nowa akceptacja, gdy bedzie potrzebne): krok flow-definition z plikiem kopii, "
            "ktory ppcall exec poda po zapisie, np.")
        out("  " + json.dumps({"nr": 1, "opis": "Wycofanie: " + str(step.get("opis", "")), "typ": "flow-definition",
                               "srodowisko": step["srodowisko"], "workflowid": step["workflowid"],
                               "oczekiwany_modifiedon": "<modifiedon_po z wykonanie.jsonl>",
                               "plik": f"{C.BACKUP_DIR_NAME}/<kopia z wykonanie.jsonl>",
                               "sha256": "<sha256 kopii z wykonanie.jsonl>"}, ensure_ascii=False))
    return errs


def exec_flow_definition(incident_id: str, step: dict, entry: dict) -> bool:
    """Zapis pelnej definicji z pliku: kopia, If-Match, porownanie po zapisie, przy roznicy przywrocenie."""
    folder = C.incident_dir(Path.cwd(), incident_id)
    new, _, problem = load_new_definition(step, folder)
    if problem:
        entry["odmowa"] = [problem]
        out(f"{problem}. Nic nie zapisano.")
        return False
    before = read_workflow(step)
    errs, definition, changes = C.flow_definition_live_problems(step, before, new, _restoring(step))
    if errs:
        entry["odmowa"] = errs
        out("Flow albo plik nie zgadza sie z zaakceptowanym planem, nic nie zapisano:\n  - " + "\n  - ".join(errs))
        return False
    backup = backup_path(incident_id, step)
    sha = write_backup(step, backup, before)
    entry.update({"kopia": str(backup), "kopia_sha256": sha,
                  "modifiedon_przed": before.get("modifiedon"), "plik": step["plik"], "sha256": step["sha256"],
                  "zmiany_akcji": describe_changes(changes)})
    log_entry(incident_id, {**entry, "etap": "zapis-rozpoczety"})
    status, payload = http("PATCH", workflow_url(step), {"clientdata": json.dumps(new, ensure_ascii=False)},
                           headers={"If-Match": before["@odata.etag"]})
    entry["status"] = status
    if not 200 <= status < 300:
        entry["odpowiedz"] = _short(payload, 4)
        out(f"Zapis odrzucony: HTTP {status}\n{_short(payload, 4)}")
        return False
    reason = None
    try:
        after = read_workflow(step)
        entry["modifiedon_po"] = after.get("modifiedon")
        diff = C.leaf_diff(new, json.loads(after["clientdata"]))
        if diff:
            entry["nieoczekiwane_roznice"] = [" / ".join(d) for d in diff][:50]
            reason = "zapisana definicja rozni sie od pliku"
        elif (after.get("statecode"), after.get("statuscode")) != (before.get("statecode"), before.get("statuscode")):
            entry["stan_flow"] = [before.get("statecode"), after.get("statecode")]
            reason = f"stan flow zmienil sie z {before.get('statecode')} na {after.get('statecode')}"
    except Exception as e:  # noqa: BLE001
        entry["wyjatek_weryfikacji"] = str(e)
        reason = "nie dalo sie sprawdzic zapisu"
    if reason:
        entry["przywrocono"] = restore_definition(step, before, definition)
        out(f"Po zapisie: {reason}"
            + (". Przywrocono kopie." if entry["przywrocono"] else f". PRZYWROCENIE NIEUDANE, kopia: {backup}"))
        return False
    out(f"Zapisano nowa definicje {after.get('name')} ({len(changes)} zmian(y) akcji). Porownanie po zapisie: "
        f"zgodna z plikiem. Kopia: {backup} (sha256 {entry['kopia_sha256']})")
    return True


def cmd_exec(args: list[str]) -> None:
    if len(args) != 2 or not args[1].isdigit():
        fail("Uzycie: ppcall exec <ID> <nr-kroku>")
    incident_id, nr = args[0], int(args[1])
    ok, reason, step = C.can_execute(Path.cwd(), incident_id, nr)
    if not ok:
        C.audit({"typ": "odmowa-wykonania", "id": incident_id, "krok": nr, "powod": reason})
        fail(reason, 3)
    appr = C.read_approval(incident_id)
    entry = {"czas": C.now().isoformat(), "uzytkownik": C.who(), "krok": nr, "opis": step["opis"],
             "operacja": C.step_summary(step), "skrot_planu": appr["hash"]}
    success = False
    try:
        if step["typ"] == "http":
            status, payload = http(step["metoda"].upper(), step["url"], step.get("body"))
            entry.update({"status": status, "odpowiedz": _short(payload, 4)})
            success = 200 <= status < 300
            out(f"HTTP {status}\n{_short(payload, 20)}")
        elif step["typ"] == "flow-patch":
            success = exec_flow_patch(incident_id, step, entry)
        elif step["typ"] == "flow-definition":
            success = exec_flow_definition(incident_id, step, entry)
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
    log_entry(incident_id, entry)
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


def cmd_verify(args: list[str]) -> None:
    if len(args) != 1:
        fail("Uzycie: ppcall verify <ID>")
    pp = C.plan_path(Path.cwd(), args[0])
    if not pp.exists():
        fail(f"Brak {pp}")
    text = pp.read_text(encoding="utf-8")
    current = C.plan_hash(text)
    accepted = {(C.read_approval(args[0]) or {}).get("hash")}
    log = C.incident_dir(Path.cwd(), args[0]) / "wykonanie.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                accepted.add(json.loads(line).get("skrot_planu"))
            except ValueError:
                pass
    if current not in accepted:
        fail("Weryfikacja dziala tylko dla planu, ktory zaakceptowal pracownik (skrot planu sie nie zgadza). "
             "Zmiana odczytow weryfikacyjnych wymaga nowej akceptacji.", 3)
    checks, errs = C.parse_checks(text)
    if errs or not checks:
        fail("Sekcja 'Weryfikacja po naprawie' nie ma poprawnego bloku ```json z odczytami: " + "; ".join(errs))
    results = []
    for chk in checks:
        try:
            status, payload = http("GET", chk["url"])
            value = C.get_at(json.loads(payload), chk.get("sciezka") or []) if status == 200 else None
            expected = chk.get("oczekiwane")
            ok = status == chk.get("status", 200) and (
                "oczekiwane" not in chk or (type(value) is type(expected) and value == expected))
            res = {"opis": chk["opis"], "status": status, "wartosc": value, "ok": ok}
        except (RuntimeError, KeyError, ValueError) as e:
            res = {"opis": chk["opis"], "ok": False, "blad": str(e)}
        results.append(res)
        mark = "OK " if res["ok"] else "NIE"
        out(f"  {mark} {chk['opis']}: " + (res.get("blad") or f"HTTP {res['status']}, wartosc {res['wartosc']!r}"
                                         + ("" if res["ok"] else f", oczekiwano {chk.get('oczekiwane')!r}")))
    entry = {"czas": C.now().isoformat(), "uzytkownik": C.who(), "skrot_planu": current, "wyniki": results,
             "ok": all(r["ok"] for r in results)}
    with open(C.incident_dir(Path.cwd(), args[0]) / C.VERIFY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    C.audit({"typ": "weryfikacja", "id": args[0], "ok": entry["ok"]})
    if not entry["ok"]:
        out("\nWeryfikacja NIE przeszla. Wroc do diagnozy z pracownikiem.")
        sys.exit(5)
    out("\nWeryfikacja przeszla.")


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
    "exec": cmd_exec, "revoke": cmd_revoke, "config": cmd_config, "konfiguracja": cmd_config, "runs": cmd_runs,
    "verify": cmd_verify,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        out(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])

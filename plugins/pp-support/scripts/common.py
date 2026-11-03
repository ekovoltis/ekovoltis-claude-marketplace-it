"""Wspolna logika pluginu pp-support.

Uzywana przez hooki (straznik, akceptacja, audyt) i przez ppcall.
Kazda decyzja bezpieczenstwa jest liczona w obu miejscach (hook i ppcall),
zeby obejscie jednego nie wystarczylo do wykonania zmiany.
"""
from __future__ import annotations

import datetime as dt
import getpass
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR_NAME = "pp-support-state"
INCIDENTS_DIR_NAME = "zgloszenia"
ID_RE = r"[A-Za-z0-9][A-Za-z0-9_-]{2,39}"


# ---------------------------------------------------------------- sciezki

def state_base() -> Path:
    override = os.environ.get("PP_SUPPORT_STATE_DIR")  # tylko testy
    return Path(override) if override else Path.home() / ".claude" / STATE_DIR_NAME


def state_dir() -> Path:
    base = state_base()
    base.mkdir(parents=True, exist_ok=True)
    (base / "akceptacje").mkdir(exist_ok=True)
    return base


def incidents_root(cwd: str | Path) -> Path:
    """Katalog zgloszen: ustawienie incidents_dir albo <cwd>/zgloszenia."""
    custom = (load_config().get("incidents_dir") or "").strip()
    return Path(cwd) / Path(custom).expanduser() if custom else Path(cwd) / INCIDENTS_DIR_NAME


def incident_dir(cwd: str | Path, incident_id: str) -> Path:
    return incidents_root(cwd) / incident_id


def valid_id(incident_id: str) -> bool:
    return bool(re.fullmatch(ID_RE, incident_id or ""))


VERIFY_LOG = "weryfikacja.jsonl"
RESERVED_INCIDENT_FILES = {"plan.md", "wykonanie.jsonl", VERIFY_LOG}
BACKUP_DIR_NAME = "backup"


def out_path_problem(cwd: str | Path, path: str) -> str | None:
    """Cel `ppcall get --out`: tylko plik w <cwd>/zgloszenia/<ID>/. Sprawdzaja hook i ppcall."""
    if not path:
        return "Brak sciezki --out."
    if ":" in Path(path).name:  # strumienie NTFS (plik:strumien)
        return "--out: niedozwolona nazwa pliku."
    base = Path(cwd).expanduser().resolve()
    target = Path(path).expanduser()
    target = (target if target.is_absolute() else base / target).resolve()
    root = incidents_root(base).expanduser().resolve()
    where = f"--out: zapis tylko do pliku w katalogu zgloszenia ({root / '<ID>'})."
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return where
    if len(parts) < 2 or not valid_id(parts[0]):
        return where
    # Windows pomija koncowe kropki i spacje: "plan.md." to ten sam plik co plan.md
    if len(parts) == 2 and parts[1].rstrip(". ").lower() in RESERVED_INCIDENT_FILES:
        return "--out: plan.md, wykonanie.jsonl i weryfikacja.jsonl nie moga byc nadpisane odpowiedzia HTTP."
    if parts[1].rstrip(". ").lower() == BACKUP_DIR_NAME:
        return "--out: kopie definicji w backup/ zapisuje wylacznie ppcall exec."
    if any(p.lower() == ".claude" for p in parts):
        return "--out: zapis do katalogu .claude jest zablokowany."
    for protected in (state_base(), PLUGIN_ROOT):
        try:
            if target.is_relative_to(protected.expanduser().resolve()):
                return "--out: zapis do stanu akceptacji i kodu pluginu jest zablokowany."
        except OSError:
            return "--out: nie mozna zweryfikowac sciezki."
    return None


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def who() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "nieznany"


# ---------------------------------------------------------------- konfiguracja

DEFAULT_CONFIG = {
    "dataverse_urls": [],
    "pp_environment_ids": [],
    "sharepoint_host": "colpro.sharepoint.com",
    "sharepoint_site": "",
    "fabric_workspace_ids": [],
    "docs_dir": "",
    "approval_ttl_min": 60,
    "incidents_dir": "",
    # Puste: krok flow-patch wylaczony we wszystkich srodowiskach.
    "flow_patch_dataverse_urls": [],
}


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            return _as_list(json.loads(text))
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in re.split(r"[,\n;]", text) if p.strip()]


def config_from_env() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    for key in DEFAULT_CONFIG:
        env = os.environ.get("CLAUDE_PLUGIN_OPTION_" + key.upper())
        if env is None:
            continue
        if isinstance(DEFAULT_CONFIG[key], list):
            cfg[key] = _as_list(env)
        elif isinstance(DEFAULT_CONFIG[key], int):
            try:
                cfg[key] = int(float(env))
            except ValueError:
                pass
        else:
            cfg[key] = env.strip()
    cfg["dataverse_urls"] = [u.rstrip("/") for u in cfg["dataverse_urls"]]
    cfg["flow_patch_dataverse_urls"] = [u.rstrip("/") for u in cfg["flow_patch_dataverse_urls"]]
    cfg["sharepoint_site"] = site_path(cfg["sharepoint_site"])
    return cfg


def site_path(value: str) -> str:
    """Odwraca konwersje Git Bash: "/sites/X" przychodzi jako "C:/.../Git/sites/X"."""
    v = (value or "").strip().replace("\\", "/")
    m = re.search(r"/(sites|teams)/", v)
    if m and not v.startswith("/"):
        v = v[m.start():]
    return v.rstrip("/")


def config_problems(cfg: dict) -> list[str]:
    """Bledy ustawien, ktore inaczej wygladalyby jak brak uprawnien."""
    problems = []
    site = cfg.get("sharepoint_site") or ""
    if site and (not site.startswith("/") or ":" in site):
        problems.append(f"sharepoint_site ma byc sciezka witryny zaczynajaca sie od '/', np. /sites/Nazwa (jest: {site})")
    for key in ("dataverse_urls", "flow_patch_dataverse_urls"):
        for u in cfg.get(key) or []:
            p = urlparse(u)
            if p.scheme != "https" or not DATAVERSE_HOST_RE.match((p.hostname or "").lower()):
                problems.append(f"{key}: niepoprawny adres Dataverse {u} (oczekiwany https://<nazwa>.crm<N>.dynamics.com)")
    inc = (cfg.get("incidents_dir") or "").strip()
    if inc and not Path(inc).expanduser().is_absolute():
        problems.append(f"incidents_dir ma byc sciezka bezwzgledna (jest: {inc})")
    host = (cfg.get("sharepoint_host") or "").lower()
    if host and not SHAREPOINT_HOST_RE.match(host):
        problems.append(f"sharepoint_host: niepoprawny host {host} (oczekiwany <nazwa>.sharepoint.com)")
    return problems


def load_config() -> dict:
    path = state_dir() / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
    cfg["sharepoint_site"] = site_path(cfg.get("sharepoint_site") or "")
    return cfg


def save_config(cfg: dict) -> None:
    (state_dir() / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- hosty i tokeny

# Tylko te hosty moga dostac token. Kazdy inny adres ppcall odrzuca.
FIXED_RESOURCES = {
    "graph.microsoft.com": "https://graph.microsoft.com",
    "management.azure.com": "https://management.azure.com/",
    "api.flow.microsoft.com": "https://service.flow.microsoft.com/",
    "api.bap.microsoft.com": "https://service.powerapps.com/",
    "api.powerapps.com": "https://service.powerapps.com/",
    "api.powerplatform.com": "https://api.powerplatform.com",
    "api.fabric.microsoft.com": "https://api.fabric.microsoft.com",
}
DATAVERSE_HOST_RE = re.compile(r"^[a-z0-9-]+\.crm\d*\.dynamics\.com$")
SHAREPOINT_HOST_RE = re.compile(r"^[a-z0-9-]+(-my)?\.sharepoint\.com$")

# Hosty, do ktorych nie wolno chodzic z pominieciem ppcall.
CLOUD_HOST_PATTERNS = [
    r"dynamics\.com", r"graph\.microsoft\.com", r"sharepoint\.com",
    r"fabric\.microsoft\.com", r"flow\.microsoft\.com", r"bap\.microsoft\.com",
    r"powerplatform\.com", r"powerapps\.com", r"management\.azure\.com",
    r"vault\.azure\.net", r"login\.microsoftonline\.com", r"logic\.azure\.com",
    r"powerautomate\.com", r"database\.windows\.net", r"datawarehouse\.fabric",
    r"powerplatformusercontent\.com",
]
CLOUD_HOST_RE = re.compile("|".join(CLOUD_HOST_PATTERNS), re.I)


# Tresc wejsc i wyjsc akcji z historii przebiegu (inputsLink/outputsLink). Adres niesie podpis SAS,
# wiec ppcall pobiera go bez tokenu i tylko metoda GET.
RUN_CONTENT_HOST_RE = re.compile(r"^[a-z0-9]+\.[a-z0-9]+\.environment\.api\.powerplatformusercontent\.com$")


def is_run_content_url(url: str) -> bool:
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return False
    return (p.scheme == "https" and port in (None, 443) and "@" not in p.netloc
            and bool(RUN_CONTENT_HOST_RE.fullmatch((p.hostname or "").lower()))
            and "sig" in parse_qs(p.query or ""))


def redact_url(url: str) -> str:
    """Adres do dziennika: podpis SAS dziala jak haslo, wiec zapytanie jest wyciete."""
    return url.split("?", 1)[0] + "?<SAS>" if is_run_content_url(url) else url


def resource_for_url(url: str) -> str | None:
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        return None
    host = p.hostname.lower()
    if host in FIXED_RESOURCES:
        return FIXED_RESOURCES[host]
    if DATAVERSE_HOST_RE.match(host) or SHAREPOINT_HOST_RE.match(host):
        return f"https://{host}"
    return None


# ---------------------------------------------------------------- plan naprawy

REQUIRED_SECTIONS = [
    "Automatyzacja", "Objaw", "Przyczyna", "Dowody", "Proponowana naprawa",
    "Ryzyko i wpływ", "Plan wycofania", "Weryfikacja po naprawie", "Kroki",
]
FLOW_PATCH_SECTIONS = ["Analiza wpływu", "Test"]
FLOW_PATCH_MAX_CHANGES = 5
# Zmieniac wolno tylko wartosci pod inputs.parameters albo inputs.body. Klucze ponizej sa zabronione
# na kazdym poziomie, bo przekierowuja wywolanie albo zmieniaja polaczenie.
FLOW_PATCH_INPUT_ROOTS = {"parameters", "body"}
# Wyjatki od INPUT_ROOTS: ksztalt sciezki pod inputs -> wymagany typ akcji (sprawdzany na zywej definicji).
#   []                          cale inputs akcji Compose (wyrazenie albo tekst)
#   ["variables", "<n>", "value"]  wartosc poczatkowa w InitializeVariable
#   ["value"]                   wartosc w SetVariable
FLOW_PATCH_TYPED_TARGETS = {"Compose", "InitializeVariable", "SetVariable"}
FLOW_PATCH_FORBIDDEN_KEYS = {"uri", "method", "headers", "host", "authentication", "path", "pathtemplate",
                             "relativepath", "function", "operationid", "apimanagement", "queries"}
FLOW_PATCH_FORBIDDEN_PREFIXES = ("connection", "serviceprovider")
GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
MODIFIEDON_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
ALLOWED_HTTP_METHODS = {"POST", "PATCH", "PUT"}
ALLOWED_CLI = {"pac", "az"}
FORBIDDEN_ARG_RE = re.compile(r"delete|remove|purge|destroy|reset|secret", re.I)


def plan_path(cwd, incident_id) -> Path:
    return incident_dir(cwd, incident_id) / "plan.md"


def plan_hash(text: str) -> str:
    normalized = text.replace("\r\n", "\n").strip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current, buf = m.group(1).strip(), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()


def parse_steps(kroki_section: str) -> list[dict]:
    m = re.search(r"```json\s*\n(.*?)\n```", kroki_section, flags=re.S)
    if not m:
        raise ValueError("Sekcja 'Kroki' musi zawierac blok ```json z lista krokow.")
    steps = json.loads(m.group(1))
    if not isinstance(steps, list):
        raise ValueError("Blok krokow musi byc lista JSON.")
    return steps


def step_problems(step: dict, expected_nr: int) -> list[str]:
    errs = []
    if step.get("nr") != expected_nr:
        errs.append(f"krok {expected_nr}: pole 'nr' musi wynosic {expected_nr}")
    if not str(step.get("opis", "")).strip():
        errs.append(f"krok {expected_nr}: brak 'opis'")
    typ = step.get("typ")
    if typ == "http":
        method = str(step.get("metoda", "")).upper()
        url = str(step.get("url", ""))
        if method == "DELETE":
            errs.append(f"krok {expected_nr}: DELETE jest zabronione, usuwanie wykonuje czlowiek")
        elif method not in ALLOWED_HTTP_METHODS:
            errs.append(f"krok {expected_nr}: metoda musi byc jedna z {sorted(ALLOWED_HTTP_METHODS)}")
        if not resource_for_url(url):
            errs.append(f"krok {expected_nr}: host spoza listy dozwolonych: {url}")
        body = json.dumps(step.get("body", {}), ensure_ascii=False).lower()
        host = (urlparse(url).hostname or "").lower()
        if host == "api.flow.microsoft.com" and method in {"PATCH", "PUT"} and "definition" in body:
            errs.append(f"krok {expected_nr}: zmiana definicji flow tylko krokiem flow-patch, przebudowa logiki sciezka deweloperska (SWO)")
        if DATAVERSE_HOST_RE.match(host) and "/workflows" in url.lower() and "clientdata" in body:
            errs.append(f"krok {expected_nr}: zapis clientdata w workflows tylko krokiem flow-patch")
        if "vault.azure.net" in url.lower():
            errs.append(f"krok {expected_nr}: operacje na Key Vault sa zabronione")
    elif typ == "flow-patch":
        errs.extend(flow_patch_problems(step, expected_nr))
    elif typ == "cli":
        argv = step.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            errs.append(f"krok {expected_nr}: 'argv' musi byc niepusta lista tekstow")
        else:
            if argv[0] not in ALLOWED_CLI:
                errs.append(f"krok {expected_nr}: dozwolone programy to {sorted(ALLOWED_CLI)}")
            if argv[:2] == ["az", "rest"]:
                errs.append(f"krok {expected_nr}: 'az rest' zastap krokiem typu http")
            if any(FORBIDDEN_ARG_RE.search(a) for a in argv):
                errs.append(f"krok {expected_nr}: argumenty zawieraja operacje zabroniona (delete/remove/secret/...)")
    else:
        errs.append(f"krok {expected_nr}: 'typ' musi byc 'http', 'cli' albo 'flow-patch'")
    return errs


def _split_flow_patch_path(path) -> tuple[int, str | None]:
    """Indeks klucza inputs akcji docelowej albo (-1, powod odrzucenia)."""
    if not isinstance(path, list) or not path or not all(isinstance(k, str) and k for k in path):
        return -1, "sciezka musi byc lista niepustych tekstow"
    if path[:3] != ["properties", "definition", "actions"]:
        return -1, "sciezka musi zaczynac sie od properties, definition, actions (wyzwalacze i parametry flow sa poza zakresem)"
    i = 2
    while True:
        if len(path) < i + 2 or path[i] != "actions":
            return -1, "sciezka musi prowadzic przez actions/<nazwa akcji>"
        i += 2
        if i >= len(path):
            return -1, "sciezka konczy sie na akcji, a musi wskazywac klucz w jej inputs"
        head = path[i]
        if head == "inputs":
            return i, None
        if head in ("else", "default"):
            i += 1
        elif head == "cases" and len(path) >= i + 3:
            i += 2
        elif head != "actions":
            return -1, f"element '{head}' jest poza zakresem (dozwolona tylko zmiana wartosci w inputs akcji)"


def is_index(key: str) -> bool:
    """Numer pozycji listy w kanonicznej postaci ASCII ("0", "12"; nie "00", nie inne cyfry Unicode)."""
    return bool(re.fullmatch(r"0|[1-9][0-9]*", key))


def flow_patch_required_type(path: list[str]) -> str | None:
    """Typ akcji wymagany dla sciezki spoza parameters/body; None, gdy typ nie ma znaczenia."""
    i, problem = _split_flow_patch_path(path)
    if problem:
        return None
    keys = path[i + 1:]
    if not keys:
        return "Compose"
    if len(keys) == 3 and keys[0] == "variables" and is_index(keys[1]) and keys[2] == "value":
        return "InitializeVariable"
    if keys == ["value"]:
        return "SetVariable"
    return None


def flow_patch_path_problem(path) -> str | None:
    """Sciezka musi prowadzic przez akcje (takze zagniezdzone) do wartosci pod inputs.parameters albo inputs.body,
    albo do wartosci akcji Compose lub zmiennej (flow_patch_required_type)."""
    i, problem = _split_flow_patch_path(path)
    if problem:
        return problem
    if flow_patch_required_type(path):
        return None
    keys = path[i + 1:]
    if len(keys) < 2 or keys[0] not in FLOW_PATCH_INPUT_ROOTS:
        return ("zmienic mozna tylko wartosc pod inputs/parameters/<klucz> albo inputs/body/<klucz>, cale inputs "
                "akcji Compose albo wartosc zmiennej (inputs/variables/<n>/value, inputs/value)")
    bad = [k for k in keys if k.lower() in FLOW_PATCH_FORBIDDEN_KEYS
           or k.lower().startswith(FLOW_PATCH_FORBIDDEN_PREFIXES)]
    if bad:
        return f"klucz {bad[0]} jest zabroniony (zmiana adresu, metody, naglowkow lub polaczenia)"
    return None


def flow_patch_label(path: list[str]) -> str:
    """Nazwa akcji i klucz w jej inputs, czytelne dla czlowieka. Tylko dla sciezki po walidacji."""
    i, _ = _split_flow_patch_path(path)
    return f"{path[i - 1]}: {'/'.join(path[i + 1:]) or 'inputs'}"


def flow_patch_problems(step: dict, nr: int) -> list[str]:
    errs = []
    env = str(step.get("srodowisko", "")).rstrip("/")
    host = (urlparse(env).hostname or "").lower()
    if not env.startswith("https://") or not DATAVERSE_HOST_RE.match(host):
        errs.append(f"krok {nr}: 'srodowisko' musi byc adresem Dataverse https://<nazwa>.crm<N>.dynamics.com")
    elif env not in [u.rstrip("/") for u in load_config().get("flow_patch_dataverse_urls") or []]:
        errs.append(f"krok {nr}: flow-patch nie jest wlaczony dla {env} (ustawienie flow_patch_dataverse_urls)")
    if not GUID_RE.fullmatch(str(step.get("workflowid", ""))):
        errs.append(f"krok {nr}: 'workflowid' musi byc identyfikatorem GUID")
    if not MODIFIEDON_RE.fullmatch(str(step.get("oczekiwany_modifiedon", ""))):
        errs.append(f"krok {nr}: 'oczekiwany_modifiedon' w formacie RRRR-MM-DDTGG:MM:SSZ, odczytany z workflows")
    changes = step.get("zmiany")
    if not isinstance(changes, list) or not changes:
        return errs + [f"krok {nr}: 'zmiany' musi byc niepusta lista"]
    if len(changes) > FLOW_PATCH_MAX_CHANGES:
        errs.append(f"krok {nr}: najwyzej {FLOW_PATCH_MAX_CHANGES} zmian w jednym kroku")
    seen = set()
    for j, ch in enumerate(changes, start=1):
        where = f"krok {nr}, zmiana {j}"
        if not isinstance(ch, dict):
            errs.append(f"{where}: musi byc obiektem z polami sciezka, bylo, ma_byc")
            continue
        problem = flow_patch_path_problem(ch.get("sciezka"))
        if problem:
            errs.append(f"{where}: {problem}")
        elif tuple(ch["sciezka"]) in seen:
            errs.append(f"{where}: ta sama sciezka wystepuje dwa razy")
        else:
            seen.add(tuple(ch["sciezka"]))
        if not isinstance(ch.get("bylo"), str) or not isinstance(ch.get("ma_byc"), str):
            errs.append(f"{where}: 'bylo' i 'ma_byc' musza byc tekstem")
        elif ch["bylo"] == ch["ma_byc"]:
            errs.append(f"{where}: 'ma_byc' jest takie samo jak 'bylo'")
    return errs


def get_at(obj, path: list[str]):
    """Klucze slownikow albo numery pozycji list (jako tekst), jak w leaf_diff."""
    for key in path:
        if isinstance(obj, dict) and key in obj:
            obj = obj[key]
        elif isinstance(obj, list) and is_index(key) and int(key) < len(obj):
            obj = obj[int(key)]
        else:
            raise KeyError(key)
    return obj


def set_at(obj, path: list[str], value) -> None:
    parent = get_at(obj, path[:-1])
    if isinstance(parent, list):
        parent[int(path[-1])] = value
    else:
        parent[path[-1]] = value


def leaf_diff(a, b, prefix: tuple = ()) -> list[tuple]:
    """Sciezki, pod ktorymi definicje sie roznia (klucz dodany, usuniety albo inna wartosc)."""
    if isinstance(a, dict) and isinstance(b, dict):
        diffs = []
        for k in sorted(set(a) | set(b)):
            if k in a and k in b:
                diffs.extend(leaf_diff(a[k], b[k], prefix + (k,)))
            else:
                diffs.append(prefix + (k,))
        return diffs
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        diffs = []
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(leaf_diff(x, y, prefix + (str(i),)))
        return diffs
    return [] if a == b and type(a) is type(b) else [prefix]


def flow_patch_live_problems(step: dict, workflow: dict) -> tuple[list[str], dict | None]:
    """Stan flow z Dataverse wobec kroku. ppcall sprawdza go w plan-check i ponownie przed zapisem."""
    errs = []
    if workflow.get("ismanaged"):
        errs.append("flow jest w rozwiazaniu zarzadzanym, zmiana tylko przez wdrozenie rozwiazania")
    if workflow.get("category") != 5:
        errs.append("to nie jest flow chmurowy (category 5)")
    if workflow.get("modifiedon") != step.get("oczekiwany_modifiedon"):
        errs.append(f"flow zmienil sie od przygotowania planu: modifiedon {workflow.get('modifiedon')}, "
                    f"w planie {step.get('oczekiwany_modifiedon')}")
    try:
        definition = json.loads(workflow.get("clientdata") or "")
    except (TypeError, ValueError):
        return errs + ["nie mozna odczytac definicji flow (clientdata)"], None
    for ch in step.get("zmiany") or []:
        label = flow_patch_label(ch["sciezka"])
        try:
            current = get_at(definition, ch["sciezka"])
        except KeyError:
            errs.append(f"{label}: sciezka nie istnieje w aktualnej definicji")
            continue
        if not isinstance(current, str):
            errs.append(f"{label}: aktualna wartosc nie jest tekstem")
        elif current != ch["bylo"]:
            errs.append(f"{label}: aktualna wartosc rozni sie od 'bylo'")
        required = flow_patch_required_type(ch["sciezka"])
        if required:
            i, _ = _split_flow_patch_path(ch["sciezka"])
            actual = get_at(definition, ch["sciezka"][:i]).get("type")
            if actual != required:
                errs.append(f"{label}: ta sciezka jest dozwolona tylko w akcji {required}, a akcja ma typ {actual}")
    return errs, definition


def parse_checks(text: str) -> tuple[list[dict], list[str]]:
    """Odczyty weryfikacyjne z bloku ```json w sekcji 'Weryfikacja po naprawie' (opcjonalny)."""
    section = _strip_comments(split_sections(text).get("Weryfikacja po naprawie", ""))
    m = re.search(r"```json\s*\n(.*?)\n```", section, flags=re.S)
    if not m:
        return [], (["Weryfikacja: blok odczytow musi zaczynac sie od ```json i byc zamkniety ```"]
                    if "```" in section else [])
    try:
        checks = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        return [], [f"Weryfikacja: niepoprawny JSON ({e})"]
    if not isinstance(checks, list):
        return [], ["Weryfikacja: blok musi byc lista odczytow"]
    errs = []
    for j, c in enumerate(checks, start=1):
        if not isinstance(c, dict) or not str(c.get("opis", "")).strip() or not isinstance(c.get("url"), str):
            errs.append(f"Weryfikacja, odczyt {j}: wymagane pola opis i url")
            continue
        if not resource_for_url(c["url"]):
            errs.append(f"Weryfikacja, odczyt {j}: host spoza listy dozwolonych")
        if "sciezka" in c and (not isinstance(c["sciezka"], list) or not all(isinstance(k, str) for k in c["sciezka"])):
            errs.append(f"Weryfikacja, odczyt {j}: 'sciezka' musi byc lista tekstow")
        if "status" in c and (not isinstance(c["status"], int) or isinstance(c["status"], bool)):
            errs.append(f"Weryfikacja, odczyt {j}: 'status' musi byc liczba (kod HTTP)")
    return checks, errs


def check_plan(text: str) -> tuple[list[str], list[dict]]:
    """Zwraca (lista_bledow, kroki)."""
    errs: list[str] = []
    sections = split_sections(text)
    for name in REQUIRED_SECTIONS:
        if name not in sections:
            errs.append(f"brak sekcji '## {name}'")
        elif name != "Kroki" and len(_strip_comments(sections[name])) < 20:
            errs.append(f"sekcja '{name}' jest pusta albo zbyt ogolna")
    steps: list[dict] = []
    if "Kroki" in sections:
        try:
            steps = parse_steps(sections["Kroki"])
        except (ValueError, json.JSONDecodeError) as e:
            errs.append(f"Kroki: {e}")
        if not steps and not errs:
            errs.append("plan nie zawiera zadnego kroku do wykonania")
        for i, s in enumerate(steps, start=1):
            errs.extend(step_problems(s if isinstance(s, dict) else {}, i))
        errs.extend(parse_checks(text)[1])
        if any(isinstance(s, dict) and s.get("typ") == "flow-patch" for s in steps):
            for name in FLOW_PATCH_SECTIONS:
                if len(_strip_comments(sections.get(name, ""))) < 20:
                    errs.append(f"krok flow-patch wymaga sekcji '## {name}' z trescia")
    return errs, steps


def step_summary(step: dict) -> str:
    if step.get("typ") == "flow-patch":
        changes = step.get("zmiany") or []
        labels = "; ".join(flow_patch_label(c["sciezka"]) for c in changes
                           if isinstance(c, dict) and not flow_patch_path_problem(c.get("sciezka")))
        return (f"flow-patch {str(step.get('srodowisko', '')).rstrip('/')} workflows({step.get('workflowid')}): "
                f"{len(changes)} zmian(y): {labels}")
    if step.get("typ") == "http":
        return f"{str(step.get('metoda', '')).upper()} {step.get('url', '')}"
    return " ".join(step.get("argv", []))


# ---------------------------------------------------------------- akceptacje

def approval_path(incident_id: str) -> Path:
    return state_dir() / "akceptacje" / f"{incident_id}.json"


def read_approval(incident_id: str) -> dict | None:
    p = approval_path(incident_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_approval(data: dict) -> None:
    approval_path(data["id"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def revoke_approval(incident_id: str) -> bool:
    p = approval_path(incident_id)
    if p.exists():
        p.unlink()
        return True
    return False


def can_execute(cwd, incident_id: str, nr: int) -> tuple[bool, str, dict | None]:
    """Jedno zrodlo prawdy dla hooka i dla ppcall exec."""
    if not valid_id(incident_id):
        return False, "Niepoprawny identyfikator zgloszenia.", None
    pp = plan_path(cwd, incident_id)
    if not pp.exists():
        return False, f"Brak planu {pp}.", None
    text = pp.read_text(encoding="utf-8")
    errs, steps = check_plan(text)
    if errs:
        return False, "Plan nie przechodzi walidacji: " + "; ".join(errs), None
    appr = read_approval(incident_id)
    if not appr:
        return False, ("Brak akceptacji pracownika. Akceptacje wpisuje wylacznie czlowiek w oknie "
                       "rozmowy (AKCEPTUJĘ <ID> <skrot>), Claude nie moze jej utworzyc."), None
    if appr.get("hash") != plan_hash(text):
        return False, ("Plan zmienil sie po akceptacji (inny skrot). Akceptacja jest niewazna, "
                       "pokaz pracownikowi nowa wersje i popros o ponowna akceptacje."), None
    try:
        expires = dt.datetime.fromisoformat(appr["wygasa"])
    except Exception:
        return False, "Uszkodzony zapis akceptacji.", None
    if now() > expires:
        return False, "Akceptacja wygasla. Potrzebna ponowna akceptacja.", None
    if nr < 1 or nr > len(steps):
        return False, f"Plan ma {len(steps)} krokow, nie ma kroku {nr}.", None
    done = appr.get("wykonane", [])
    if nr in done:
        return False, f"Krok {nr} zostal juz wykonany. Ponowienie wymaga nowego planu i akceptacji.", None
    if appr.get("zatrzymano"):
        return False, ("Wykonanie zatrzymane po bledzie kroku "
                       f"{appr['zatrzymano']}. Potrzebna nowa diagnoza i nowy plan."), None
    expected = (max(done) if done else 0) + 1
    if nr != expected:
        return False, f"Kroki wykonuje sie po kolei. Nastepny dozwolony krok: {expected}.", None
    return True, "ok", steps[nr - 1]


def audit(event: dict) -> None:
    event = {"czas": now().isoformat(), "uzytkownik": who(), **event}
    with open(state_dir() / "audyt.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

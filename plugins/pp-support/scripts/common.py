"""Wspolna logika pluginu pp-support.

Uzywana przez hooki (straznik, akceptacja, audyt) i przez ppcall.
Kazda decyzja bezpieczenstwa jest liczona w obu miejscach (hook i ppcall),
zeby obejscie jednego nie wystarczylo do wykonania zmiany.
"""
from __future__ import annotations

import copy
import datetime as dt
import getpass
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

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
    (base / "sesje").mkdir(exist_ok=True)
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
BACKUP_DIR_NAME = "backup"  # odnosnik w planie (backup/<plik>) i katalog kopii sprzed 1.2.2 w zgloszeniu
BACKUP_STATE_DIR_NAME = "kopie"  # od 1.2.2 kopie leza w stanie: <stan>/kopie/<ID>/<plik>


def backup_dir(incident_id: str) -> Path:
    """Katalog kopii definicji zgloszenia w chronionym stanie (odczyt i zapis tylko ppcall)."""
    return state_dir() / BACKUP_STATE_DIR_NAME / incident_id


def legacy_backup_dir(folder: str | Path) -> Path:
    """Kopie zapisane przed 1.2.2 w zgloszenia/<ID>/backup/. ppcall przenosi je do stanu."""
    return Path(folder) / BACKUP_DIR_NAME


def step_file_path(folder: str | Path, plik: str) -> Path:
    """Plik kroku flow-definition: flow-...-nowa.json w zgloszeniu albo backup/<plik> rozwiazany do stanu.
    Kolejnosc dla kopii: kopie tego zgloszenia w stanie; przejsciowo kopia sprzed 1.2.2 w zgloszenia/<ID>/backup/;
    kopia innego zgloszenia w stanie, gdy nazwa jest jednoznaczna (wycofanie w nowym zgloszeniu).
    Nazwa kopii jest wczesniej sprawdzona (flow_definition_file_problem), a tresc wiaze sha256 z planu."""
    rel = str(plik).replace("\\", "/")
    if not rel.lower().startswith(BACKUP_DIR_NAME + "/"):
        return Path(folder) / rel
    name = rel.split("/", 1)[1]
    own = backup_dir(Path(folder).name) / name
    legacy = legacy_backup_dir(folder) / name
    if own.is_file():
        return own
    if legacy.is_file():
        return legacy
    if re.search(r"[/\*?\[\]]", name):
        return own
    others = [p for p in (state_dir() / BACKUP_STATE_DIR_NAME).glob(f"*/{name}") if p.is_file()]
    return others[0] if len(others) == 1 else own


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
    # Puste: krok flow-definition (pelna nowa definicja z pliku) wylaczony we wszystkich srodowiskach.
    "flow_definition_dataverse_urls": [],
    # Odczyt z API Salesbooka: adres API i zrodlo danych logowania "<adres Dataverse>|<workflowid>|<akcja HTTP>".
    # Puste: odczyt z Salesbooka wylaczony.
    "salesbook_api_url": "",
    "salesbook_auth_flow": "",
    # Foldery, w ktorych kazda rozmowa jest od startu rozmowa dyzurna.
    "support_dirs": [],
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
    cfg["flow_definition_dataverse_urls"] = [u.rstrip("/") for u in cfg["flow_definition_dataverse_urls"]]
    cfg["salesbook_api_url"] = cfg["salesbook_api_url"].rstrip("/")
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
    for key in ("dataverse_urls", "flow_patch_dataverse_urls", "flow_definition_dataverse_urls"):
        for u in cfg.get(key) or []:
            p = urlparse(u)
            if p.scheme != "https" or not DATAVERSE_HOST_RE.match((p.hostname or "").lower()):
                problems.append(f"{key}: niepoprawny adres Dataverse {u} (oczekiwany https://<nazwa>.crm<N>.dynamics.com)")
    sb = cfg.get("salesbook_api_url") or ""
    if sb and not is_salesbook_url(sb + "/"):
        problems.append(f"salesbook_api_url: oczekiwany adres https://<nazwa>.salesbook-ent.com/... (jest: {sb})")
    src = cfg.get("salesbook_auth_flow") or ""
    if src and salesbook_auth_source(src) is None:
        problems.append("salesbook_auth_flow: oczekiwany format <adres Dataverse>|<workflowid>|<nazwa akcji HTTP>")
    inc = (cfg.get("incidents_dir") or "").strip()
    if inc and not Path(inc).expanduser().is_absolute():
        problems.append(f"incidents_dir ma byc sciezka bezwzgledna (jest: {inc})")
    host = (cfg.get("sharepoint_host") or "").lower()
    if host and not SHAREPOINT_HOST_RE.match(host):
        problems.append(f"sharepoint_host: niepoprawny host {host} (oczekiwany <nazwa>.sharepoint.com)")
    return problems


def load_config() -> dict:
    path = state_base() / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            cfg["_nieczytelna"] = True
    cfg["sharepoint_site"] = site_path(cfg.get("sharepoint_site") or "")
    return cfg


def save_config(cfg: dict) -> None:
    # Zapis atomowy: hook w innej rozmowie nie moze przeczytac polowy pliku.
    target = state_dir() / "config.json"
    tmp = target.with_name(f"config.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


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
    r"powerplatformusercontent\.com", r"salesbook-ent\.com",
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


SALESBOOK_HOST_RE = re.compile(r"[a-z0-9-]+\.salesbook-ent\.com")


def is_salesbook_url(url: str) -> bool:
    try:
        p = urlparse(url)
        port = p.port
    except ValueError:
        return False
    return (p.scheme == "https" and port in (None, 443) and "@" not in p.netloc
            and bool(SALESBOOK_HOST_RE.fullmatch((p.hostname or "").lower())))


def salesbook_auth_source(value: str) -> tuple[str, str, str] | None:
    """Rozbiera "<adres Dataverse>|<workflowid>|<akcja>"; None, gdy format jest bledny."""
    parts = [x.strip() for x in (value or "").split("|")]
    if len(parts) != 3 or not all(parts):
        return None
    dv, wf, action = parts
    host = (urlparse(dv).hostname or "").lower()
    if not dv.startswith("https://") or not DATAVERSE_HOST_RE.match(host) or not GUID_RE.fullmatch(wf):
        return None
    return dv.rstrip("/"), wf, action


SECRET_KEY_RE = re.compile(
    r"(.*pass(word)?.*|pwd|.*secret.*|.*token|authorization|cookie|set-cookie|x-api-key|api[-_]?key|"
    r"ocp-apim-subscription-key|connection[-_]?string|pfx|sastoken|credentials?)", re.I)
SECRET_VALUE_RES = [
    # Token ma cyfre albo znak base64/JWT, wiec "Basic information" w zwyklym tekscie zostaje.
    (re.compile(r"\b(Basic|Bearer)\s+(?=[A-Za-z0-9+/=._~-]*[0-9+/=.])[A-Za-z0-9+/=._~-]{12,}"), r"\1 ***"),
    (re.compile(r"([?&](?:sig|code|token|access_token|key|api-key|apikey|password|pwd)=)[^&\s\"'#]+", re.I), r"\1***"),
    (re.compile(r'((?:\\?")(?:password|pwd|secret|clientSecret|client_secret|token|authorization)(?:\\?")\s*:\s*\\?")'
                r'(?:[^"\\]|\\.)*?(\\?")', re.I), r"\1***\2"),
]


def redact_text(text: str) -> str:
    """Hasla w dowolnym tekscie: naglowki Basic/Bearer, parametry adresu, pola "password" w JSON-ie.
    Adres tresci przebiegu (SAS) zostaje, bo ppcall potrzebuje go do odczytu."""
    if is_run_content_url(text):
        return text
    for regex, repl in SECRET_VALUE_RES:
        text = regex.sub(repl, text)
    return text


def redact_secrets(obj):
    """Zastepuje hasla '***' po nazwie klucza (kazdy typ wartosci) i po wzorcu w tekstach, takze w JSON-ie
    zapisanym jako tekst (clientdata). Niezmieniona czesc zostaje dokladnie taka jak byla."""
    if isinstance(obj, dict):
        return {k: ("***" if SECRET_KEY_RE.fullmatch(str(k)) and obj[k] not in (None, "", [], {})
                    else redact_secrets(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_secrets(v) for v in obj]
    if isinstance(obj, str):
        if obj.strip()[:1] in ("{", "["):
            try:
                inner = json.loads(obj)
            except ValueError:
                inner = None
            if isinstance(inner, (dict, list)):
                red = redact_secrets(inner)
                return obj if red == inner else json.dumps(red, ensure_ascii=False)
        return redact_text(obj)
    return obj


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
FLOW_DEFINITION_MAX_ACTIONS = 10
# Tekst pod inputs.parameters albo inputs.body dowolnej akcji. Klucze ponizej sa zabronione
# na kazdym poziomie, bo przekierowuja wywolanie albo zmieniaja polaczenie.
FLOW_PATCH_INPUT_ROOTS = {"parameters", "body"}
# Wyjatki od INPUT_ROOTS: ksztalt sciezki za nazwa akcji -> dozwolone typy akcji (sprawdzane na zywej
# definicji). Wartosc moze byc dowolnym JSON, porownanie kanoniczne. "*" = dowolny typ (runAfter).
FLOW_PATCH_TYPED_PATHS = [
    (["inputs"], ("Compose",)),
    (["inputs", "variables", "<n>", "value"], ("InitializeVariable",)),
    (["inputs", "value"], ("SetVariable",)),
    (["inputs", "select"], ("Select",)),
    (["inputs", "where"], ("Query",)),
    (["inputs", "from"], ("Query",)),
    (["inputs", "columns"], ("Table",)),
    (["expression"], ("If", "Until")),
    (["foreach"], ("Foreach",)),
    (["inputs", "runStatus"], ("Terminate",)),
    (["inputs", "runError"], ("Terminate",)),
    (["runAfter"], ("*",)),
]
FLOW_ACTION_HEADS = {"inputs", "expression", "foreach", "runAfter"}
RUN_AFTER_STATUSES = {"Succeeded", "Failed", "Skipped", "TimedOut"}

FLOW_PATCH_FORBIDDEN_KEYS = {"uri", "method", "headers", "host", "authentication", "path", "pathtemplate",
                             "relativepath", "function", "operationid", "apimanagement", "queries"}
FLOW_PATCH_FORBIDDEN_PREFIXES = ("connection", "serviceprovider")
GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
MODIFIEDON_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
ALLOWED_HTTP_METHODS = {"POST", "PATCH", "PUT"}
ALLOWED_CLI = {"pac", "az"}
# Krok cli z az: tylko te polecenia (slowa przed pierwsza opcja). Wszystko inne, takze update/set/create,
# jest odrzucane. Zmiana definicji Logic App albo flow nie jest krokiem cli (niezmiennik 6).
CLI_ARG_RE = re.compile(r"[\w.:/=,+@-]+")
AZ_PLAN_COMMANDS = {("logic", "workflow", "enable"), ("logic", "workflow", "disable"),
                    ("webapp", "restart"), ("functionapp", "restart")}
FORBIDDEN_ARG_RE = re.compile(r"delete|remove|purge|destroy|reset|secret|import|upgrade|publish|deploy", re.I)
# Power Automate API: krok http moze tylko wlaczyc, wylaczyc, uruchomic, ponowic albo anulowac przebieg.
FLOW_API_POST_ACTIONS = {"start", "stop", "run", "resubmit", "cancel"}
DATAVERSE_FORBIDDEN_PATH_RE = re.compile(r"\$batch|importsolution|stageandupgrade|deletesolution|clonesolution|"
                                         r"publishxml|publishallxml|/solutions\b|/customapis\b", re.I)


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


def step_problems(step: dict, expected_nr: int, folder: Path | None = None) -> list[str]:
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
        path = unquote(urlparse(url).path).lower().rstrip("/")
        if host == "api.flow.microsoft.com" and (method != "POST"
                                                 or path.rsplit("/", 1)[-1] not in FLOW_API_POST_ACTIONS):
            errs.append(f"krok {expected_nr}: w Power Automate API krok http moze tylko wlaczyc, wylaczyc, uruchomic, "
                        "ponowic albo anulowac przebieg (POST .../start, stop, run, resubmit, cancel); zmiana "
                        "definicji krokiem flow-patch albo flow-definition")
        if DATAVERSE_HOST_RE.match(host):
            if "/workflows" in path:
                fields = step.get("body")
                if (method != "PATCH" or not re.search(r"/workflows\([0-9a-f-]{36}\)$", path)
                        or not isinstance(fields, dict) or not fields or not set(fields) <= {"statecode", "statuscode"}):
                    errs.append(f"krok {expected_nr}: w workflows krok http moze tylko wlaczyc albo wylaczyc flow "
                                "(PATCH workflows(<id>) z statecode i statuscode); zmiana definicji krokiem "
                                "flow-patch albo flow-definition")
            if DATAVERSE_FORBIDDEN_PATH_RE.search(path):
                errs.append(f"krok {expected_nr}: operacje wsadowe ($batch) i na rozwiazaniach sa zabronione")
        if "vault.azure.net" in url.lower():
            errs.append(f"krok {expected_nr}: operacje na Key Vault sa zabronione")
    elif typ == "flow-patch":
        errs.extend(flow_patch_problems(step, expected_nr))
    elif typ == "flow-definition":
        errs.extend(flow_definition_problems(step, expected_nr, folder))
    elif typ == "cli":
        argv = step.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            errs.append(f"krok {expected_nr}: 'argv' musi byc niepusta lista tekstow")
        else:
            if argv[0] not in ALLOWED_CLI:
                errs.append(f"krok {expected_nr}: dozwolone programy to {sorted(ALLOWED_CLI)}")
            if argv[:2] == ["az", "rest"]:
                errs.append(f"krok {expected_nr}: 'az rest' zastap krokiem typu http")
            elif argv[0] == "az":
                words = []
                for a in argv[1:]:
                    if a.startswith("-"):
                        break
                    words.append(a.lower())
                if tuple(words) not in AZ_PLAN_COMMANDS:
                    allowed = ", ".join("az " + " ".join(c) for c in sorted(AZ_PLAN_COMMANDS))
                    errs.append(f"krok {expected_nr}: krok cli z az moze byc tylko jednym z: {allowed}")
            if any(a.startswith("@") or "=@" in a for a in argv[1:]):
                errs.append(f"krok {expected_nr}: argumenty z pliku (@plik) sa niedozwolone")
            # az na Windows to az.cmd uruchamiany przez cmd.exe: &, |, ^, % i cudzyslowy bylyby interpretowane
            if not all(CLI_ARG_RE.fullmatch(a) for a in argv[1:]):
                errs.append(f"krok {expected_nr}: argumenty cli moga zawierac tylko litery, cyfry i znaki . : / = , + @ - _")
            if any(FORBIDDEN_ARG_RE.search(a) for a in argv):
                errs.append(f"krok {expected_nr}: argumenty zawieraja operacje zabroniona (delete/remove/secret/...)")
    else:
        errs.append(f"krok {expected_nr}: 'typ' musi byc 'http', 'cli', 'flow-patch' albo 'flow-definition'")
    return errs


def _split_flow_patch_path(path) -> tuple[int, str | None]:
    """Indeks pierwszego klucza za nazwa akcji docelowej (inputs, expression, foreach, runAfter)
    albo (-1, powod odrzucenia)."""
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
            return -1, "sciezka konczy sie na akcji, a musi wskazywac klucz w akcji (np. inputs)"
        head = path[i]
        if head in FLOW_ACTION_HEADS:
            return i, None
        if head in ("else", "default"):
            i += 1
        elif head == "cases" and len(path) >= i + 3:
            i += 2
        elif head != "actions":
            return -1, f"element '{head}' jest poza zakresem (dozwolona tylko zmiana wartosci w istniejacej akcji)"


def is_index(key: str) -> bool:
    """Numer pozycji listy w kanonicznej postaci ASCII ("0", "12"; nie "00", nie inne cyfry Unicode)."""
    return bool(re.fullmatch(r"0|[1-9][0-9]*", key))


def flow_patch_required_type(path: list[str]) -> tuple[str, ...] | None:
    """Dozwolone typy akcji dla sciezki spoza parameters/body (("*",) = dowolny typ); None, gdy to nie jest
    taka sciezka."""
    i, problem = _split_flow_patch_path(path)
    if problem:
        return None
    keys = path[i:]
    for shape, types in FLOW_PATCH_TYPED_PATHS:
        if len(keys) == len(shape) and all(s == k or (s == "<n>" and is_index(k)) for s, k in zip(shape, keys)):
            return types
    return None


def flow_patch_path_problem(path) -> str | None:
    """Sciezka musi prowadzic przez akcje (takze zagniezdzone) do tekstu pod inputs.parameters albo inputs.body
    albo do jednej z wartosci z FLOW_PATCH_TYPED_PATHS."""
    i, problem = _split_flow_patch_path(path)
    if problem:
        return problem
    if flow_patch_required_type(path):
        return None
    keys = path[i:]
    if len(keys) < 3 or keys[0] != "inputs" or keys[1] not in FLOW_PATCH_INPUT_ROOTS:
        return ("zmienic mozna tekst pod inputs/parameters/<klucz> albo inputs/body/<klucz>, cale inputs Compose, "
                "wartosc zmiennej, inputs/select (Select), inputs/where i inputs/from (Query), inputs/columns (Table), "
                "expression (If, Until), foreach (Foreach), inputs/runStatus i inputs/runError (Terminate) "
                "albo runAfter")
    bad = [k for k in keys if k.lower() in FLOW_PATCH_FORBIDDEN_KEYS
           or k.lower().startswith(FLOW_PATCH_FORBIDDEN_PREFIXES)]
    if bad:
        return f"klucz {bad[0]} jest zabroniony (zmiana adresu, metody, naglowkow lub polaczenia)"
    return None


def flow_patch_label(path: list[str]) -> str:
    """Nazwa akcji i klucz w niej, czytelne dla czlowieka. Tylko dla sciezki po walidacji."""
    i, _ = _split_flow_patch_path(path)
    keys = path[i + 1:] if path[i] == "inputs" and len(path) > i + 1 else path[i:]
    return f"{path[i - 1]}: {'/'.join(keys)}"


def canon(value) -> str:
    """Kanoniczny zapis wartosci JSON do porownan (kolejnosc kluczy bez znaczenia)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def value_text(value, limit: int = 160) -> str:
    text = value if isinstance(value, str) else canon(value)
    return text[:limit]


def run_after_problem(value) -> str | None:
    if not isinstance(value, dict):
        return "runAfter musi byc obiektem {akcja: [statusy]}"
    for name, statuses in value.items():
        if (not isinstance(statuses, list) or not statuses
                or not all(isinstance(st, str) and st in RUN_AFTER_STATUSES for st in statuses)):
            return f"runAfter.{name}: statusy tylko z {sorted(RUN_AFTER_STATUSES)}, niepusta lista"
    return None


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
    seen: list[tuple] = []
    for j, ch in enumerate(changes, start=1):
        where = f"krok {nr}, zmiana {j}"
        if not isinstance(ch, dict) or "bylo" not in ch or "ma_byc" not in ch:
            errs.append(f"{where}: musi byc obiektem z polami sciezka, bylo, ma_byc")
            continue
        problem = flow_patch_path_problem(ch.get("sciezka"))
        if problem:
            errs.append(f"{where}: {problem}")
            continue
        path = tuple(ch["sciezka"])
        if any(path[:len(p)] == p or p[:len(path)] == path for p in seen):
            errs.append(f"{where}: sciezka powtarza sie albo zawiera inna zmiane z tego kroku")
        seen.append(path)
        typed = flow_patch_required_type(ch["sciezka"])
        if not typed and (not isinstance(ch["bylo"], str) or not isinstance(ch["ma_byc"], str)):
            errs.append(f"{where}: pod inputs/parameters i inputs/body 'bylo' i 'ma_byc' musza byc tekstem")
        elif canon(ch["bylo"]) == canon(ch["ma_byc"]):
            errs.append(f"{where}: 'ma_byc' jest takie samo jak 'bylo'")
        if typed == ("*",):
            problem = run_after_problem(ch["ma_byc"])
            if problem:
                errs.append(f"{where}: {problem}")
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


def run_after_graph_problems(actions: dict, where: str = "") -> list[str]:
    """runAfter w jednym zakresie: tylko istniejace akcje z tego zakresu, poprawne statusy, bez cykli."""
    errs = []
    graph: dict[str, list[str]] = {}
    for name, action in (actions or {}).items():
        ra = (action.get("runAfter") or {}) if isinstance(action, dict) else {}
        problem = run_after_problem(ra)
        if problem:
            errs.append(f"{where}{name}: {problem}")
            continue
        missing = [n for n in ra if n not in actions or n == name]
        if missing:
            errs.append(f"{where}{name}: runAfter wskazuje akcje spoza tego zakresu albo sama siebie: {missing[0]}")
        graph[name] = [n for n in ra if n in actions and n != name]
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        state[n] = 1
        for m in graph.get(n, []):
            if state.get(m) == 1 or (m not in state and visit(m)):
                return True
        state[n] = 2
        return False

    for n in graph:
        if n not in state and visit(n):
            errs.append(f"{where}runAfter tworzy cykl (akcja {n})")
            break
    return errs


def _workflow_problems(step: dict, workflow: dict) -> list[str]:
    errs = []
    if workflow.get("ismanaged"):
        errs.append("flow jest w rozwiazaniu zarzadzanym, zmiana tylko przez wdrozenie rozwiazania")
    if workflow.get("category") != 5:
        errs.append("to nie jest flow chmurowy (category 5)")
    if workflow.get("modifiedon") != step.get("oczekiwany_modifiedon"):
        errs.append(f"flow zmienil sie od przygotowania planu: modifiedon {workflow.get('modifiedon')}, "
                    f"w planie {step.get('oczekiwany_modifiedon')}")
    return errs


def flow_patch_live_problems(step: dict, workflow: dict) -> tuple[list[str], dict | None]:
    """Stan flow z Dataverse wobec kroku. ppcall sprawdza go w plan-check i ponownie przed zapisem."""
    errs = _workflow_problems(step, workflow)
    try:
        definition = json.loads(workflow.get("clientdata") or "")
    except (TypeError, ValueError):
        return errs + ["nie mozna odczytac definicji flow (clientdata)"], None
    changed = copy.deepcopy(definition)
    for ch in step.get("zmiany") or []:
        label = flow_patch_label(ch["sciezka"])
        try:
            current = get_at(definition, ch["sciezka"])
        except KeyError:
            errs.append(f"{label}: sciezka nie istnieje w aktualnej definicji")
            continue
        required = flow_patch_required_type(ch["sciezka"])
        if not required and not isinstance(current, str):
            errs.append(f"{label}: aktualna wartosc nie jest tekstem")
        elif canon(current) != canon(ch["bylo"]):
            errs.append(f"{label}: aktualna wartosc rozni sie od 'bylo'")
        i, _ = _split_flow_patch_path(ch["sciezka"])
        if required and "*" not in required:
            actual = action_type(get_at(definition, ch["sciezka"][:i]))
            if actual not in required:
                errs.append(f"{label}: ta sciezka jest dozwolona tylko w akcji {'/'.join(required)}, "
                            f"a akcja ma typ {actual}")
        set_at(changed, ch["sciezka"], ch["ma_byc"])
    sensitive = sensitive_refs(definition)
    for ch in step.get("zmiany") or []:
        i, _ = _split_flow_patch_path(ch["sciezka"])
        name = ch["sciezka"][i - 1]
        try:
            target = get_at(definition, ch["sciezka"][:i])
        except KeyError:
            continue
        hit = sensitive_hit(defines(name, target), sensitive)
        if hit and flow_patch_required_type(ch["sciezka"]) != ("*",):
            errs.append(f"{flow_patch_label(ch['sciezka'])}: akcja ustawia {', '.join(hit)}, z czego akcja wysylajaca "
                        "bierze adres, naglowki albo uwierzytelnienie; takiej zmiany nie wolno robic krokiem flow-patch")
        if len(SECRET_REF_RE.findall(canon(ch["ma_byc"]))) > len(SECRET_REF_RE.findall(canon(ch["bylo"]))):
            errs.append(f"{flow_patch_label(ch['sciezka'])}: wyrazenie actions(...) albo pole authentication (odczyt "
                        "wejsc innej akcji z danymi logowania) jest zabronione")
        if flow_patch_required_type(ch["sciezka"]) == ("*",):
            try:
                scope = get_at(changed, ch["sciezka"][:i - 1])
            except KeyError:
                continue
            errs.extend(f"{flow_patch_label(ch['sciezka'])}: {e}" for e in run_after_graph_problems(scope))
    return errs, definition


# ---------------------------------------------------------------- flow-definition

def flow_definition_file_problem(plik, workflowid: str) -> str | None:
    """Plik nowej definicji: flow-<workflowid>-nowa.json w katalogu zgloszenia albo kopia backup/<plik>
    (kopia lezy w stanie, zob. step_file_path)."""
    if not isinstance(plik, str) or not plik:
        return "'plik' musi byc nazwa pliku w katalogu zgloszenia"
    p = plik.replace("\\", "/").lower()
    wf = str(workflowid).lower()
    if p == f"flow-{wf}-nowa.json":
        return None
    if re.fullmatch(rf"{BACKUP_DIR_NAME}/{re.escape(wf)}-\d{{8}}T\d{{6}}Z(-\d{{1,3}})?\.json", p, re.I):
        return None
    return (f"'plik' musi byc flow-{workflowid}-nowa.json (nowa definicja) albo "
            f"{BACKUP_DIR_NAME}/{workflowid}-<czas>.json (kopia do wycofania)")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def flow_definition_problems(step: dict, nr: int, folder: Path | None) -> list[str]:
    """Walidacja bez sieci (hook i ppcall): srodowisko, identyfikatory, plik i jego skrot."""
    errs = []
    env = str(step.get("srodowisko", "")).rstrip("/")
    host = (urlparse(env).hostname or "").lower()
    if not env.startswith("https://") or not DATAVERSE_HOST_RE.match(host):
        errs.append(f"krok {nr}: 'srodowisko' musi byc adresem Dataverse https://<nazwa>.crm<N>.dynamics.com")
    elif env not in [u.rstrip("/") for u in load_config().get("flow_definition_dataverse_urls") or []]:
        errs.append(f"krok {nr}: flow-definition nie jest wlaczony dla {env} "
                    "(ustawienie flow_definition_dataverse_urls)")
    wf = str(step.get("workflowid", ""))
    if not GUID_RE.fullmatch(wf):
        return errs + [f"krok {nr}: 'workflowid' musi byc identyfikatorem GUID"]
    if not MODIFIEDON_RE.fullmatch(str(step.get("oczekiwany_modifiedon", ""))):
        errs.append(f"krok {nr}: 'oczekiwany_modifiedon' w formacie RRRR-MM-DDTGG:MM:SSZ, odczytany z workflows")
    problem = flow_definition_file_problem(step.get("plik"), wf)
    if problem:
        return errs + [f"krok {nr}: {problem}"]
    if not re.fullmatch(r"[0-9a-f]{64}", str(step.get("sha256", ""))):
        errs.append(f"krok {nr}: 'sha256' musi byc skrotem SHA-256 pliku (64 znaki, male litery)")
    if folder is None:
        return errs + [f"krok {nr}: nie mozna sprawdzic pliku definicji bez katalogu zgloszenia"]
    path = step_file_path(folder, step["plik"])
    if not path.is_file():
        where = "w kopiach zgloszenia" if str(step["plik"]).lower().startswith(BACKUP_DIR_NAME) else "w katalogu zgloszenia"
        return errs + [f"krok {nr}: brak pliku {step['plik']} {where}"]
    actual = file_sha256(path)
    if str(step.get("sha256", "")) != actual:
        errs.append(f"krok {nr}: skrot pliku {step['plik']} to {actual}, a w planie {step.get('sha256')} "
                    "(plik zmienil sie albo skrot jest zly)")
    try:
        new = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(get_at(new, ["properties", "definition", "actions"]), dict):
            raise KeyError("actions")
    except (ValueError, KeyError, UnicodeDecodeError):
        errs.append(f"krok {nr}: plik {step['plik']} nie zawiera definicji flow (properties/definition/actions)")
    return errs


ACTION_CONTAINERS = ("actions", "else", "cases", "default")
# Typy akcji, ktore flow-definition moze dodac albo zmienic: wbudowane, bez wywolan na zewnatrz i bez polaczen.
FLOW_SAFE_TYPES = {"compose", "if", "switch", "scope", "foreach", "until", "initializevariable", "setvariable",
                   "incrementvariable", "decrementvariable", "appendtoarrayvariable", "appendtostringvariable",
                   "parsejson", "query", "select", "table", "join", "terminate", "wait", "expression"}
# Odwolania w wyrazeniach: zmienne i wyniki akcji, z ktorych akcja wysylajaca buduje adres lub naglowki.
REF_FUNCS = r"variables|outputs|body|actions|result|actionOutputs|actionBody|items"
REF_RE = re.compile(rf"\b({REF_FUNCS})\s*\(\s*'((?:[^']|'')*)'", re.I)
# Odwolanie z nazwa wyliczana (np. variables(concat(...))): nie wiadomo do czego, wiec do wszystkiego.
DYNAMIC_REF_RE = re.compile(rf"\b({REF_FUNCS})\s*\((?!\s*')", re.I)
ANY_REF = ("*", "*")
# Odczyt wejsc innej akcji albo zakresu (tam sa dane logowania) i samo pole uwierzytelnienia.
SECRET_REF_RE = re.compile(r"\bactions\s*\(|\bresult\s*\(|authentication", re.I)
MASKED = "***"


def action_type(action: dict) -> str | None:
    """Typ akcji bez wzgledu na wielkosc liter klucza; None, gdy kluczy typu jest kilka albo typ nie jest tekstem."""
    keys = [k for k in action if str(k).lower() == "type"]
    if len(keys) != 1 or not isinstance(action[keys[0]], str):
        return None
    return action[keys[0]]


def _container_problem(name: str, action: dict) -> str | None:
    lowered = [str(k).lower() for k in action]
    if len(set(lowered)) != len(lowered):
        return f"{name}: klucze akcji rozniace sie tylko wielkoscia liter"
    if action_type(action) is None:
        return f"{name}: brak jednoznacznego typu akcji"
    for key in ACTION_CONTAINERS:
        value = action.get(key)
        if value is None:
            continue
        if not isinstance(value, dict):
            return f"{name}: '{key}' musi byc obiektem"
        if key in ("else", "default") and not isinstance(value.get("actions", {}), dict):
            return f"{name}: '{key}/actions' musi byc obiektem"
        if key == "cases" and not all(isinstance(b, dict) and isinstance(b.get("actions", {}), dict)
                                      for b in value.values()):
            return f"{name}: kazdy przypadek w 'cases' musi byc obiektem z obiektem 'actions'"
    return None


def _own_body(action: dict) -> dict:
    """Akcja bez zagniezdzonych akcji (te sa porownywane osobno). Kontenery innego typu zostaja w tresci."""
    body = {k: v for k, v in action.items() if not (k in ACTION_CONTAINERS and isinstance(v, dict))}
    for key in ("else", "default"):
        if isinstance(action.get(key), dict):
            body[key] = {k: v for k, v in action[key].items() if not (k == "actions" and isinstance(v, dict))}
    if isinstance(action.get("cases"), dict):
        body["cases"] = {c: ({k: v for k, v in b.items() if not (k == "actions" and isinstance(v, dict))}
                             if isinstance(b, dict) else b) for c, b in action["cases"].items()}
    return body


def scope_label(where: tuple) -> str:
    return "/".join("/".join(part) for part in where) or "glowny zakres"


def action_scopes(actions: dict, where: tuple = (), out: list | None = None) -> list:
    """Wszystkie zakresy akcji (slowniki actions). Polozenie to krotka (akcja, kontener[, przypadek]),
    wiec zakres o nazwie 'else' nie myli sie z galezia else warunku."""
    out = [] if out is None else out
    out.append((where, actions or {}))
    for name, action in (actions or {}).items():
        if not isinstance(action, dict):
            continue
        if isinstance(action.get("actions"), dict):
            action_scopes(action["actions"], where + ((name, "actions"),), out)
        for key in ("else", "default"):
            if isinstance(action.get(key), dict) and isinstance(action[key].get("actions"), dict):
                action_scopes(action[key]["actions"], where + ((name, key),), out)
        for case, body in (action.get("cases") or {}).items() if isinstance(action.get("cases"), dict) else ():
            if isinstance(body, dict) and isinstance(body.get("actions"), dict):
                action_scopes(body["actions"], where + ((name, "cases", str(case)),), out)
    return out


def flatten_actions(actions: dict) -> tuple[dict, list[str]]:
    """Nazwa akcji -> (zakres, akcja) oraz lista nazw powtorzonych (w flow nazwy akcji sa unikalne)."""
    out, dup = {}, []
    for where, scope in action_scopes(actions):
        for name, action in scope.items():
            if not isinstance(action, dict):
                dup.append(name)
                continue
            if name in out:
                dup.append(name)
            out[name] = (where, action)
    return out, dup


def _all_keys(obj) -> set:
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k).lower())
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


def _forbidden(keys) -> list[str]:
    return sorted(k for k in keys if k in FLOW_PATCH_FORBIDDEN_KEYS or k.startswith(FLOW_PATCH_FORBIDDEN_PREFIXES))


def _without_actions(definition: dict) -> dict:
    d = copy.deepcopy(definition)
    if isinstance(d, dict) and isinstance(d.get("properties"), dict) \
            and isinstance(d["properties"].get("definition"), dict):
        d["properties"]["definition"].pop("actions", None)
    return d


def _refs(text: str) -> set:
    """Odwolania do zmiennych i akcji; nazwy malymi literami (Power Automate ich nie rozroznia)."""
    found = set()
    for kind, name in REF_RE.findall(text):
        found.add(("var" if kind.lower() == "variables" else "act", name.replace("''", "'").lower()))
    if DYNAMIC_REF_RE.search(text):
        found.add(ANY_REF)
    return found


def _routing_values(obj, out: list) -> None:
    """Wartosci pod kluczami adresu, metody, naglowkow, uwierzytelnienia i polaczenia (na kazdym poziomie)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _forbidden([str(k).lower()]):
                out.append(v)
            else:
                _routing_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _routing_values(v, out)


def sensitive_refs(definition: dict) -> set:
    """Zmienne i akcje, z ktorych akcje wysylajace (Http, konektory i inne spoza FLOW_SAFE_TYPES) biora adres,
    naglowki albo uwierzytelnienie. Zmiana ich wartosci przekierowalaby istniejace wywolanie."""
    try:
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return set()
    by_name = {n.lower(): a for n, (_, a) in actions.items()}
    found: set = set()
    for _, (where, action) in actions.items():
        if str(action_type(action) or "").lower() in FLOW_SAFE_TYPES:
            continue
        own = _own_body(action)
        values: list = []
        _routing_values(own, values)
        inputs = next((v for k, v in own.items() if str(k).lower() == "inputs"), None)
        if inputs is not None and not isinstance(inputs, dict):
            values.append(inputs)  # cale inputs jako wyrazenie: adres moze byc w nim
        for v in values:
            found |= _refs(canon(v))
        # item() w petli: zrodlem jest klucz foreach petli, w ktorej lezy akcja.
        for part in where:
            parent = by_name.get(part[0].lower())
            if parent and str(action_type(parent) or "").lower() in ("foreach", "until"):
                found.add(("act", part[0].lower()))
    # Domkniecie: skad biora wartosci akcje i zmienne, od ktorych zalezy adres (Compose -> Select -> Http).
    queue = list(found)
    while queue:
        ref = queue.pop()
        if ref == ANY_REF:
            continue
        kind, name = ref
        if kind == "act":
            sources = [by_name[name]] if name in by_name else []
        else:
            sources = [a for n, a in by_name.items() if ref in defines(n, a)]
        for src in sources:
            for new in _refs(canon(_own_body(src))) - found:
                found.add(new)
                queue.append(new)
    return found


def sensitive_hit(defs: set, sensitive: set) -> list[str]:
    """Nazwy z defs, od ktorych zalezy adres, naglowki albo uwierzytelnienie akcji wysylajacej."""
    if ANY_REF in sensitive and defs:
        return sorted(n for _, n in defs)
    return sorted(n for _, n in defs & sensitive)


def defines(name: str, action: dict) -> set:
    """Co ustawia akcja: wynik akcji o tej nazwie i zmienne, ktore inicjuje albo zmienia."""
    out = {("act", name.lower())}
    inputs = _ci(action, "inputs")
    if isinstance(inputs, dict):
        if isinstance(_ci(inputs, "name"), str):
            out.add(("var", _ci(inputs, "name").lower()))
        variables = _ci(inputs, "variables")
        for var in variables if isinstance(variables, list) else []:
            if isinstance(var, dict) and isinstance(_ci(var, "name"), str):
                out.add(("var", _ci(var, "name").lower()))
    return out


def _ci(obj, key: str):
    """Wartosc klucza bez wzgledu na wielkosc liter (pierwsze trafienie); None, gdy brak albo obj nie jest dict."""
    if not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if str(k).lower() == key:
            return v
    return None


def flow_definition_diff(old: dict, new: dict) -> tuple[list[dict], list[str]]:
    """Roznice akcji (dodane, usuniete, zmienione, przeniesione) z kluczami i podgladem tresci."""
    a, _ = flatten_actions(get_at(old, ["properties", "definition", "actions"]))
    b, dup_b = flatten_actions(get_at(new, ["properties", "definition", "actions"]))
    errs = [f"nazwa akcji {n} wystepuje w nowej definicji wiecej niz raz albo akcja nie jest obiektem"
            for n in sorted(set(dup_b))]
    changes = []
    for name in sorted(set(a) | set(b)):
        if name not in b:
            changes.append({"zmiana": "usunieta", "akcja": name, "typ": action_type(a[name][1]),
                            "zakres": scope_label(a[name][0]), "definiuje": defines(name, a[name][1])})
            continue
        scope, action = b[name]
        problem = _container_problem(name, action)
        if problem:
            errs.append(problem)
        own = _own_body(action)
        text = canon(own)
        preview = value_text(redact_secrets(own), 400)
        if name not in a:
            changes.append({"zmiana": "dodana", "akcja": name, "typ": action_type(action), "zakres": scope_label(scope),
                            "klucze": sorted(own)[:8], "klucze_zakazane": _forbidden(_all_keys(own)),
                            "definiuje": defines(name, action), "podglad": preview,
                            "odczyt_hasel": bool(SECRET_REF_RE.search(text)), "maskowane": MASKED in text})
            continue
        old_scope, old_action = a[name]
        old_own = _own_body(old_action)
        paths = leaf_diff(old_own, own)
        if not paths and old_scope == scope:
            continue
        keys = set()
        for p in paths:
            keys |= {str(k).lower() for k in p}
            for side in (old_own, own):
                try:
                    keys |= _all_keys(get_at(side, [str(k) for k in p]))
                except KeyError:
                    pass
        old_text = canon(old_own)
        item = {"zmiana": "zmieniona" if paths else "przeniesiona", "akcja": name, "typ": action_type(action),
                "zakres": scope_label(scope), "klucze": sorted({"/".join(p[:2]) for p in paths})[:8],
                "klucze_zakazane": _forbidden(keys),
                "definiuje": defines(name, action) | defines(name, old_action), "podglad": preview,
                "odczyt_hasel": len(SECRET_REF_RE.findall(text)) > len(SECRET_REF_RE.findall(old_text)),
                "maskowane": text.count(MASKED) > old_text.count(MASKED)}
        if old_scope != scope:
            item["zmiana"] = "zmieniona i przeniesiona" if paths else "przeniesiona"
            item["z_zakresu"] = scope_label(old_scope)
        if str(action_type(old_action) or "").lower() != str(action_type(action) or "").lower():
            item["zmiana_typu"] = [action_type(old_action), action_type(action)]
        changes.append(item)
    return changes, errs


def flow_definition_live_problems(step: dict, workflow: dict, new: dict,
                                  restoring: bool = False) -> tuple[list[str], dict | None, list[dict]]:
    """Ograniczenia niezmiennika 6 dla nowej definicji wobec zywej. Sprawdza tylko ppcall (wymaga odczytu).
    restoring=True: plik to kopia zapisana przez ppcall exec (rejestr kopii), powrot do poprzedniego stanu."""
    errs = _workflow_problems(step, workflow)
    try:
        old = json.loads(workflow.get("clientdata") or "")
        if not isinstance(get_at(old, ["properties", "definition", "actions"]), dict):
            raise KeyError("actions")
        if not isinstance(get_at(new, ["properties", "definition", "actions"]), dict):
            raise KeyError("actions")
    except (TypeError, ValueError, KeyError):
        return errs + ["nie mozna odczytac definicji flow (clientdata albo plik)"], None, []
    outside = leaf_diff(_without_actions(old), _without_actions(new))
    if outside:
        errs.append("zmiana poza akcjami (wyzwalacze, connectionReferences, parametry, $connections) jest "
                    "zabroniona: " + ", ".join("/".join(p) for p in outside[:5]))
    changes, diff_errs = flow_definition_diff(old, new)
    errs.extend(diff_errs)
    if not changes:
        errs.append("nowa definicja nie zmienia zadnej akcji")
    if len(changes) > FLOW_DEFINITION_MAX_ACTIONS:
        errs.append(f"najwyzej {FLOW_DEFINITION_MAX_ACTIONS} akcji dodanych, usunietych lub zmienionych w jednym "
                    f"kroku (jest {len(changes)})")
    if not restoring:
        sensitive = sensitive_refs(old) | sensitive_refs(new)
        for c in changes:
            hit = sensitive_hit(c["definiuje"], sensitive)
            if hit:
                errs.append(f"{c['akcja']}: akcja ustawia {', '.join(hit)}, z czego akcja wysylajaca bierze adres, "
                            "naglowki albo uwierzytelnienie; takiej zmiany nie wolno robic krokiem flow-definition")
            if c["zmiana"] == "usunieta":
                continue
            if str(c.get("typ") or "").lower() not in FLOW_SAFE_TYPES:
                errs.append(f"{c['akcja']}: akcji typu {c.get('typ')} nie wolno dodawac ani zmieniac krokiem "
                            "flow-definition (dozwolone tylko akcje wbudowane bez wywolan na zewnatrz)")
            if c.get("klucze_zakazane"):
                errs.append(f"{c['akcja']}: zmiana kluczy {', '.join(c['klucze_zakazane'][:4])} jest zabroniona "
                            "(adres, metoda, naglowki, uwierzytelnienie, polaczenie)")
            if c.get("zmiana_typu"):
                errs.append(f"{c['akcja']}: zmiana typu akcji {c['zmiana_typu'][0]} -> {c['zmiana_typu'][1]} "
                            "jest zabroniona")
            if c.get("odczyt_hasel"):
                errs.append(f"{c['akcja']}: wyrazenie actions(...) albo pole authentication (odczyt wejsc innej akcji "
                            "z danymi logowania) jest zabronione")
            if c.get("maskowane"):
                errs.append(f"{c['akcja']}: tresc zawiera {MASKED} (haslo zamaskowane przy odczycie); plik musi "
                            "powstac z prawdziwych wartosci, nie z zamaskowanej odpowiedzi")
    for where, scope in action_scopes(get_at(new, ["properties", "definition", "actions"])):
        errs.extend(run_after_graph_problems(scope, f"{scope_label(where)}: "))
    return errs, old, changes


def backup_registry() -> Path:
    return state_dir() / "kopie.jsonl"


def register_backup(workflowid: str, path: Path, sha256: str) -> None:
    """Skrot kopii zapisanej przez ppcall exec (z bajtow w pamieci, nie z ponownego odczytu pliku).
    Tylko taka kopia moze wrocic do flow bez ograniczen kluczy."""
    with open(backup_registry(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"workflowid": workflowid.lower(), "plik": Path(path).name,
                            "sha256": sha256, "czas": now().isoformat()}) + "\n")


def is_registered_backup(workflowid: str, sha256: str) -> bool:
    p = backup_registry()
    if not p.exists():
        return False
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("workflowid") == workflowid.lower() and rec.get("sha256") == sha256:
            return True
    return False


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


def check_plan(text: str, folder: Path | None = None) -> tuple[list[str], list[dict]]:
    """Zwraca (lista_bledow, kroki). folder: katalog zgloszenia (pliki krokow flow-definition)."""
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
            errs.extend(step_problems(s if isinstance(s, dict) else {}, i, folder))
        errs.extend(parse_checks(text)[1])
        flow_types = {s.get("typ") for s in steps if isinstance(s, dict)} & {"flow-patch", "flow-definition"}
        if flow_types:
            for name in FLOW_PATCH_SECTIONS:
                if len(_strip_comments(sections.get(name, ""))) < 20:
                    errs.append(f"krok {'/'.join(sorted(flow_types))} wymaga sekcji '## {name}' z trescia")
    return errs, steps


def step_summary(step: dict) -> str:
    if step.get("typ") == "flow-patch":
        changes = step.get("zmiany") or []
        labels = "; ".join(flow_patch_label(c["sciezka"]) for c in changes
                           if isinstance(c, dict) and not flow_patch_path_problem(c.get("sciezka")))
        return (f"flow-patch {str(step.get('srodowisko', '')).rstrip('/')} workflows({step.get('workflowid')}): "
                f"{len(changes)} zmian(y): {labels}")
    if step.get("typ") == "flow-definition":
        return (f"flow-definition {str(step.get('srodowisko', '')).rstrip('/')} workflows({step.get('workflowid')}): "
                f"nowa definicja z pliku {step.get('plik')} (sha256 {str(step.get('sha256', ''))[:12]})")
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
    errs, steps = check_plan(text, pp.parent)
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


# ---------------------------------------------------------------- aktywacja w rozmowie

SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,100}")
SESSION_MAX_AGE_DAYS = 30
# Akceptacja i odwolanie w swoim formacie (polecenie na poczatku linii), "dyzur" jako pierwsze slowo wiadomosci.
ACTIVATION_PROMPT_RE = re.compile(
    rf"^\s*(AKCEPTUJ[EĘ]|ODWO[LŁ]UJ[EĘ])\s+{ID_RE}\b|\A\s*dy[zż]ur\b|/pp-support:", re.I | re.M)


def in_support_dir(cwd) -> bool:
    try:
        here = Path(cwd).expanduser().resolve()
    except (OSError, TypeError):
        return False
    for d in _as_list(load_config().get("support_dirs")):
        try:
            if here == Path(d).expanduser().resolve() or here.is_relative_to(Path(d).expanduser().resolve()):
                return True
        except OSError:
            continue
    return False


def session_active(session_id, cwd) -> bool:
    """Straznik pilnuje rozmowy dyzurnej. Bez poprawnego identyfikatora rozmowy, przy nieczytelnej
    konfiguracji albo bledzie odczytu pilnuje zawsze (fail closed)."""
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return True
    try:
        if load_config().get("_nieczytelna"):
            return True
        marker = state_base() / "sesje" / f"{session_id}.json"
        if marker.exists():
            os.utime(marker)  # data ostatniego uzycia: przycinanie liczy od niej, nie od aktywacji
            return True
        if in_support_dir(cwd):
            # Znacznik zostaje po wyjsciu z folderu dyzurnego (cd w rozmowie).
            activate_session(session_id, "folder dyzurny")
            return True
        return False
    except Exception:  # noqa: BLE001
        return True


def activate_session(session_id, reason: str) -> None:
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return
    path = state_dir() / "sesje" / f"{session_id}.json"
    if path.exists():
        return
    path.write_text(json.dumps({"czas": now().isoformat(), "powod": reason}, ensure_ascii=False), encoding="utf-8")
    audit({"typ": "aktywacja-rozmowy", "sesja": session_id, "powod": reason})


def prune_sessions() -> None:
    limit = now().timestamp() - SESSION_MAX_AGE_DAYS * 86400
    for f in (state_dir() / "sesje").glob("*.json"):
        try:
            if f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass


def audit(event: dict) -> None:
    event = {"czas": now().isoformat(), "uzytkownik": who(), **event}
    with open(state_dir() / "audyt.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

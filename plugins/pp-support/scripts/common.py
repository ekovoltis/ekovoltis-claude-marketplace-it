"""Wspolna logika pluginu pp-support.

Uzywana przez hooki (straznik, akceptacja, audyt) i przez ppcall.
Kazda decyzja bezpieczenstwa jest liczona w obu miejscach (hook i ppcall),
zeby obejscie jednego nie wystarczylo do wykonania zmiany.
"""
from __future__ import annotations

import copy
import unicodedata
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


def step_file_path(folder: str | Path, plik: str, sha256: str | None = None) -> Path:
    """Plik kroku flow-definition: flow-...-nowa.json w zgloszeniu albo backup/<plik> rozwiazany do stanu.
    Kolejnosc dla kopii: kopie tego zgloszenia w stanie; przejsciowo kopia sprzed 1.2.2 w zgloszenia/<ID>/backup/;
    kopia innego zgloszenia w stanie, gdy nazwa jest jednoznaczna, a przy kilku plikach o tej nazwie
    jedyny o skrocie sha256 z planu (wycofanie w nowym zgloszeniu).
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
    if len(others) > 1 and sha256:
        others = [p for p in others if file_sha256(p) == sha256]
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
    # Odczyt SQL (ppcall sql): wpisy <serwer>/<baza>, tylko endpointy SQL Fabric.
    "fabric_sql_endpoints": ["ezpoaao4s3guzlluryza2wjh6y-pxnazkwsqg5etc62asdz35n22u.datawarehouse.fabric.microsoft.com/ekovoltis"],
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
    for entry in _as_list(cfg.get("fabric_sql_endpoints")):
        server, _, db = entry.partition("/")
        if not FABRIC_SQL_HOST_RE.match(server.strip().lower()) or not SQL_DB_RE.match(db.strip()):
            problems.append(f"fabric_sql_endpoints: oczekiwany wpis <nazwa>.datawarehouse.fabric.microsoft.com/<baza> (jest: {entry})")
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


# ---------------------------------------------------------------- Fabric SQL (tylko odczyt, 1.4.0)

# Token dla tego zasobu ppcall przekazuje sterownikowi ODBC we wlasnym procesie, wylacznie
# do serwera z listy fabric_sql_endpoints, ktory musi byc hostem Fabric.
SQL_RESOURCE = "https://database.windows.net/"
FABRIC_SQL_HOST_RE = re.compile(r"^[a-z0-9-]+\.datawarehouse\.fabric\.microsoft\.com$")
SQL_DB_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
SQL_DRIVER = "ODBC Driver 18 for SQL Server"
SQL_MAX_ROWS = 5000
SQL_MAX_ROWS_LIMIT = 50000
SQL_TIMEOUT_S = 120
SQL_TIMEOUT_LIMIT_S = 600
SQL_MAX_QUERY_CHARS = 20000
SQL_PERSONAL_TAG = "_DANE_OSOBOWE"
# Slowa, ktorych nie moze byc nigdzie poza literalami i komentarzami: zmiany danych i schematu,
# wywolania procedur, dostep do zrodel zewnetrznych, polecenia sterujace i transakcje.
# T-SQL dopuszcza kilka polecen bez srednika, dlatego kazde z tych slow jest odrzucane w dowolnym miejscu.
SQL_FORBIDDEN_WORDS = frozenset("""
INSERT UPDATE DELETE MERGE DROP ALTER CREATE TRUNCATE EXEC EXECUTE INTO OUTPUT
OPENROWSET OPENQUERY OPENDATASOURCE OPENXML WAITFOR BULK COPY RENAME
GRANT REVOKE DENY BACKUP RESTORE DBCC KILL SHUTDOWN RECONFIGURE CHECKPOINT USE SETUSER REVERT
DECLARE SET IF WHILE BEGIN COMMIT ROLLBACK SAVE TRAN TRANSACTION RETURN GOTO GO PRINT RAISERROR THROW
OPEN CLOSE DEALLOCATE CURSOR RECEIVE SEND MOVE CONVERSATION ENABLE DISABLE
READTEXT WRITETEXT UPDATETEXT ADD GET
TABLOCK TABLOCKX XLOCK UPDLOCK HOLDLOCK PAGLOCK SERIALIZABLE REPEATABLEREAD
""".split())
SQL_SET_OPERATORS = frozenset({"UNION", "ALL", "EXCEPT", "INTERSECT"})
# Liczby tak jak w lekserze T-SQL: 0x.. (cyfry szesnastkowe), 1.5e3, 1e (wykladnik moze byc pusty). Slowo po liczbie jest osobnym tokenem,
# bo serwer tez je oddziela ("WHERE 1=1DELETE ..." to dwa polecenia).
_SQL_TOKEN_RE = re.compile(r"0[xX][0-9A-Fa-f]*|\d+(?:\.\d*)?(?:[eE][+-]?\d*)?|\.\d+(?:[eE][+-]?\d*)?|[^\W\d][\w@#$]*|[@#][\w@#$]*|''|\S")


def sql_endpoints(cfg: dict) -> list[tuple[str, str]]:
    """Wpisy fabric_sql_endpoints w postaci <serwer>/<baza>."""
    result = []
    for entry in _as_list(cfg.get("fabric_sql_endpoints")):
        server, _, db = entry.partition("/")
        result.append((server.strip().lower(), db.strip()))
    return result


def sql_endpoint_problem(cfg: dict, server: str, db: str) -> str | None:
    s, d = (server or "").strip().lower(), (db or "").strip()
    if not FABRIC_SQL_HOST_RE.match(s):
        return ("ppcall sql: serwer musi byc endpointem SQL Fabric (<nazwa>.datawarehouse.fabric.microsoft.com), "
                f"bez protokolu i portu (jest: {server}).")
    if not SQL_DB_RE.match(d):
        return f"ppcall sql: niepoprawna nazwa bazy ({db})."
    allowed = {(a, b.lower()) for a, b in sql_endpoints(cfg)}
    if (s, d.lower()) not in allowed:
        return (f"ppcall sql: endpoint {s} / {d} nie jest na liscie fabric_sql_endpoints w ustawieniach pluginu. "
                "Liste zmienia czlowiek w ustawieniach.")
    return None


def _strip_sql(query: str) -> tuple[str | None, str | None]:
    """Zapytanie bez komentarzy (takze zagniezdzonych) i z literalami tekstowymi zastapionymi ''.
    Nazwy w [] i "" to jedno slowo: zwykla nazwa ASCII bez zmian (slowa kluczowe nadal widoczne), inna jako Q$<n>. Zwraca (tekst, problem)."""
    out, i, n = [], 0, len(query)
    while i < n:
        ch, two = query[i], query[i:i + 2]
        if two == "--":
            ends = [j for j in (query.find("\n", i), query.find("\r", i)) if j >= 0]
            i = min(ends) if ends else n
            out.append(" ")
        elif two == "/*":
            depth, i = 1, i + 2
            while i < n and depth:
                if query[i:i + 2] == "/*":
                    depth, i = depth + 1, i + 2
                elif query[i:i + 2] == "*/":
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                return None, "niezamkniety komentarz /* */"
            out.append(" ")
        elif ch == "'":
            i += 1
            while True:
                j = query.find("'", i)
                if j < 0:
                    return None, "niezamkniety literal tekstowy"
                if query[j + 1:j + 2] == "'":
                    i = j + 2
                    continue
                i = j + 1
                break
            out.append(" '' ")
        elif ch in "[\"":
            close = "]" if ch == "[" else '"'
            buf, i = [], i + 1
            while True:
                if i >= n:
                    return None, "niezamknieta nazwa w [] albo \"\""
                if query[i] == close:
                    if query[i + 1:i + 2] == close:
                        buf.append(close)
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(query[i])
                i += 1
            # Nazwa w [] albo "" to jeden identyfikator. Zwykla nazwa ASCII zostaje slowem (lista zakazanych ja
            # widzi); kazda inna ([x ekovoltis], [ekovoltis.x], znaki spoza ASCII) staje sie nieprzezroczystym
            # tokenem Q$<n>, ktory nie moze byc nazwa bazy z listy (SQL_DB_RE nie dopuszcza $).
            name = "".join(buf)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                name = f"Q${len(out)}"
            out.append(" " + name + " ")
        else:
            out.append(ch)
            i += 1
    return "".join(out), None


def sql_query_problem(query: str, db: str | None = None, check_names: bool = True) -> str | None:
    """Jedno polecenie SELECT albo WITH ... SELECT. Sprawdzaja hook i ppcall przed wyslaniem do serwera."""
    q = query or ""
    if not q.strip():
        return "ppcall sql: puste zapytanie."
    if len(q) > SQL_MAX_QUERY_CHARS:
        return f"ppcall sql: zapytanie dluzsze niz {SQL_MAX_QUERY_CHARS} znakow."
    if any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") and c not in "\t\r\n" for c in q):
        return "ppcall sql: zapytanie zawiera znaki sterujace albo niewidoczne."
    stripped, problem = _strip_sql(q)
    if problem:
        return f"ppcall sql: {problem}."
    for ch, why in ((";", "srednik (dozwolone jest jedno polecenie bez srednika)"),
                    ("{", "nawias klamrowy (sekwencje ODBC {call ...})"), ("}", "nawias klamrowy")):
        if ch in stripped:
            return f"ppcall sql: niedozwolony {why}."
    raw_tokens = _SQL_TOKEN_RE.findall(stripped)
    tokens = [t.upper() for t in raw_tokens]
    if not tokens or tokens[0] not in ("SELECT", "WITH"):
        return "ppcall sql: zapytanie musi zaczynac sie od SELECT albo WITH (tylko odczyt)."
    bad = sorted({t for t in tokens if t in SQL_FORBIDDEN_WORDS})
    if bad:
        return ("ppcall sql: tylko odczyt, niedozwolone slowa: " + ", ".join(bad) +
                ". Kolumne o takiej nazwie odczytaj przez SELECT * albo widok.")
    for a, b in zip(tokens, tokens[1:]):
        if a == "NEXT" and b == "VALUE":
            return "ppcall sql: NEXT VALUE FOR zmienia sekwencje."
    depth, main_selects, prev = 0, 0, ""
    for t in tokens:
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
            if depth < 0:
                return "ppcall sql: niesparowane nawiasy."
        elif t == "SELECT" and depth == 0 and prev not in SQL_SET_OPERATORS:
            main_selects += 1
        prev = t
    if depth:
        return "ppcall sql: niesparowane nawiasy."
    if main_selects != 1:
        return ("ppcall sql: dozwolone jest jedno polecenie SELECT (zapytania laczy UNION, EXCEPT, INTERSECT); "
                f"znaleziono {main_selects}.")
    return sql_names_problem(raw_tokens, db) if check_names else None


def sql_names_problem(tokens: list[str], db: str | None) -> str | None:
    """Nazwy wieloczesciowe tylko w bazie z listy: <baza>.<schemat>.<obiekt>[.<kolumna>]. Nazwa trzyczesciowa
    z inna pierwsza czescia (inna baza tej samej hurtowni) i dluzsze niz cztery czesci sa odrzucane."""
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i] == "." or (i + 1 < n and tokens[i + 1] == "."):
            start, parts, expect_word = i, [], tokens[i] != "."
            if tokens[i] == ".":
                parts.append("")
            while i < n:
                t = tokens[i]
                if expect_word and t != ".":
                    parts.append(t)
                    expect_word = False
                elif t == ".":
                    if not expect_word:
                        expect_word = True
                    else:
                        parts.append("")  # baza..obiekt (pominiety schemat)
                else:
                    break
                i += 1
            if len(parts) > 4:
                return "ppcall sql: nazwa z wiecej niz czterema czesciami (serwer polaczony) jest niedozwolona."
            # porownanie tylko ASCII: upper() zamienia np. dotless i na I
            if len(parts) in (3, 4) and (db is None or not parts[0].isascii()
                                         or parts[0].lower() != db.strip().lower()):
                return (f"ppcall sql: nazwa {'.'.join(parts).lower()} wskazuje inna baze niz {db or '(nieznana)'}. "
                        "Uzyj schemat.tabela albo aliasu tabeli (alias.kolumna).")
            if i == start:
                i += 1
            continue
        i += 1
    return None


def sql_out_path(path: str) -> str:
    """Nazwa pliku wyniku ppcall sql zawsze ma znacznik _DANE_OSOBOWE."""
    p = Path(path)
    if SQL_PERSONAL_TAG.lower() in p.name.lower():
        return path
    return str(p.with_name(p.stem + SQL_PERSONAL_TAG + p.suffix))


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
                             "relativepath", "function", "operationid", "apimanagement", "queries", "dataset",
                             "url", "baseurl", "siteurl", "resourceuri"}
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
    if keys == ["inputs", "parameters", "path"]:
        return None  # tylko w wyjatku dla akcji odczytu SharePoint; warunki sprawdza ppcall (sp_filename_exception)
    if len(keys) < 3 or keys[0] != "inputs" or keys[1] not in FLOW_PATCH_INPUT_ROOTS:
        return ("zmienic mozna tekst pod inputs/parameters/<klucz> albo inputs/body/<klucz>, cale inputs Compose, "
                "wartosc zmiennej, inputs/select (Select), inputs/where i inputs/from (Query), inputs/columns (Table), "
                "expression (If, Until), foreach (Foreach), inputs/runStatus i inputs/runError (Terminate) "
                "albo runAfter")
    bad = [k for k in keys if _forbidden([k.lower()])]
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
    errs.extend(execution_order_problems(definition, changed))
    # zrodla wstawek ze starej i z nowej definicji: krok moze podlaczyc nowe zrodlo do zapytania
    sql_sources = sql_insert_sources(definition)
    for ref, names in sql_insert_sources(changed).items():
        sql_sources.setdefault(ref, set()).update(names)
    for ch in step.get("zmiany") or []:
        i, _ = _split_flow_patch_path(ch["sciezka"])
        name = ch["sciezka"][i - 1]
        try:
            target = get_at(definition, ch["sciezka"][:i])
        except KeyError:
            continue
        hit = sensitive_hit(defines(name, target), sensitive)
        exception, why = sp_filename_exception(definition, ch)
        keys_l = [k.lower() for k in ch["sciezka"][i:]]
        sql = flow_patch_sql_check(target, ch, i)
        if sql and sql["problem"]:
            errs.append(f"{flow_patch_label(ch['sciezka'])}: {sql['problem']}")
        for expr in (sql or {}).get("nowe_wyrazenia") or []:
            bad = sql_new_expr_sources_problem(changed, name, expr)
            if bad:
                errs.append(f"{flow_patch_label(ch['sciezka'])}: nowe wyrazenie {expr[:60]} wstawia do zapytania "
                            f"SQL wartosc ze zrodla, ktore {bad}")
        sql_hits = sql_source_hits(defines(name, target), sql_sources)
        # wynik konektora to dane z czasu dzialania (znane ograniczenie): stale sprawdzane w akcjach wbudowanych
        builtin = str(action_type(target) or "").lower() in FLOW_SAFE_TYPES
        bad = sql_insert_problem(ch.get("ma_byc")) if sql_hits and builtin else None
        if sql_hits and builtin and not bad:
            try:
                if structured_output(get_at(changed, ch["sciezka"][:i])):
                    bad = "nowa wartosc to tablica albo obiekt (do tekstu trafia JSON z [ ] i \")"
            except KeyError:
                pass
        if bad:
            errs.append(f"{flow_patch_label(ch['sciezka'])}: zmienia dane wstawiane do zapytania SQL "
                        f"({', '.join(sql_hits)}): {bad}")
        if sql_query_action(target) and keys_l[:2] == ["inputs", "parameters"] and \
                not _sql_query_key("/".join(keys_l[2:])):
            errs.append(f"{flow_patch_label(ch['sciezka'])}: w akcji SQL mozna zmienic tylko tekst zapytania; "
                        "serwer i baza wskazuja, dokad trafia zapytanie")
            continue
        if sp_write_action(target) and keys_l[:2] == ["inputs", "parameters"] and keys_l[2:3] != ["body"]:
            errs.append(f"{flow_patch_label(ch['sciezka'])}: w akcji zapisu SharePoint mozna zmienic tylko tresc (body); "
                        "parametry wskazuja, gdzie i co zapisac")
            continue
        if keys_l == ["inputs", "parameters", "path"] and not exception:
            errs.append(f"{flow_patch_label(ch['sciezka'])}: {why or 'klucz path jest zabroniony'}")
            continue
        refs_hit = defines(name, target) & sensitive
        allowed = {("var", str(exception.get("zmienna", "")).lower())} if exception and exception.get("zmienna") else set()
        if exception and ANY_REF not in sensitive and refs_hit <= allowed:
            pass  # wyjatek: tylko zmienna z nazwa pliku, nic innego zaleznego od tej akcji
        elif hit:
            # Od 1.4.2 (decyzja Michala Kaszty z 2026-11-09) takze runAfter: zmiana kolejnosci akcji ustawiajacych
            # wartosc adresu wybiera, ktora wartosc dostanie akcja wysylajaca.
            if why:
                errs.append(f"{flow_patch_label(ch['sciezka'])}: wyjatek dla nazwy pliku SharePoint nie ma zastosowania: {why}")
            errs.append(f"{flow_patch_label(ch['sciezka'])}: akcja ustawia {', '.join(hit)}, z czego akcja wysylajaca "
                        "bierze adres, naglowki albo uwierzytelnienie; takiej zmiany nie wolno robic krokiem flow-patch")
        if len(SECRET_REF_RE.findall(_expr_view(canon(ch["ma_byc"])))) > \
                len(SECRET_REF_RE.findall(_expr_view(canon(ch["bylo"])))):
            errs.append(f"{flow_patch_label(ch['sciezka'])}: wyrazenie actions(...) albo pole authentication (odczyt "
                        "wejsc innej akcji z danymi logowania) jest zabronione")
        if flow_patch_required_type(ch["sciezka"]) == ("*",):
            try:
                scope = get_at(changed, ch["sciezka"][:i - 1])
            except KeyError:
                continue
            errs.extend(f"{flow_patch_label(ch['sciezka'])}: {e}" for e in run_after_graph_problems(scope))
    return errs, definition


# ---------------------------------------------------------------- flow-patch: zapytanie SQL (1.4.1, ITS-418)
# Zapytanie, ktore bylo tylko odczytem wedlug parsera ppcall sql, po zmianie tez musi nim byc. Zapytania, ktore juz
# zmieniaja dane, flow-patch zmienia tak jak dotad (bez nowych ograniczen).
SQL_QUERY_OPERATION_RE = re.compile(r"^execute(passthrough)?nativequery", re.I)


def sql_query_action(action) -> bool:
    """Akcja konektora wykonujaca tekst zapytania SQL (np. ExecutePassThroughNativeQuery_V2)."""
    if not isinstance(action, dict) or str(action_type(action) or "").lower() not in ("openapiconnection", "apiconnection"):
        return False
    inputs = _ci(action, "inputs")
    host = _ci(inputs, "host")
    if isinstance(host, dict) and SQL_QUERY_OPERATION_RE.match(str(_ci(host, "operationid") or "")):
        return True
    return "/query/sql" in str(_ci(inputs, "path") or "").lower()


# Nowe wyrazenie w zapytaniu SQL (decyzja Michala Kaszty z 2026-11-09, ITS-418): tylko funkcje operujace na
# danych, odwolania do zmiennych i wynikow akcji po nazwie (bez wejscia wyzwalacza) i literaly, ktore nie zmieniaja
# kontekstu SQL: cyfry, spacje, przecinki, kropki, nawiasy i parzysta liczba apostrofow (literal z szescioma apostrofami daje '').
SQL_EXPR_DATA_FUNCS = {"if", "empty", "equals", "not", "and", "or", "greater", "less", "greaterorequals",
                       "lessorequals", "substring", "length", "sub", "add", "mul", "div", "mod", "min", "max", "trim",
                       "first", "last", "split", "join", "concat", "coalesce", "int", "float", "skip", "take",
                       "indexof", "lastindexof"}
SQL_EXPR_REF_FUNCS = {"variables", "outputs", "body", "items", "item", "actionoutputs", "actionbody"}
_WDL_TOKEN_RE = re.compile(r"'(?:[^']|'')*'|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\s+|[(),?\[\]]|.", re.S)
_SAFE_LITERAL_RE = re.compile(r"[0-9 ,.()']*")
WHOLE_VALUE_FUNCS = {"if", "coalesce", "concat"}


def _wdl_literals(expr: str, refs: set | None = None) -> tuple[list, bool]:
    """Literaly tekstowe wyrazenia WDL (po rozwinieciu '') poza nazwami odwolan i pol, oraz czy wyrazenie
    sklada sie tylko z dozwolonych elementow (funkcje na danych, odwolania po nazwie)."""
    refs = SQL_EXPR_REF_FUNCS if refs is None else refs
    tokens = [x for x in _WDL_TOKEN_RE.findall(expr) if not x.isspace()]
    literals, safe, stack = [], True, []  # stack: [funkcja, numer argumentu]
    for n, tok in enumerate(tokens):
        prev = tokens[n - 1] if n else ""
        prev2 = tokens[n - 2].lower() if n > 1 else ""
        nxt = tokens[n + 1] if n + 1 < len(tokens) else ""
        if tok.startswith("'") and tok.endswith("'") and len(tok) > 1:
            if (prev == "(" and prev2 in refs and nxt == ")") or (prev == "[" and nxt == "]"):
                continue
            literals.append((tok[1:-1].replace("''", "'"), tuple(f for f, _ in stack)))
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok):
            if nxt != "(" or tok.lower() not in SQL_EXPR_DATA_FUNCS | refs:
                safe = False
            elif tok.lower() in NUMERIC_FUNCS and not _numeric_slot(stack):
                safe = False  # liczba (np. -1 z sub) wstawiona jako tekst moze dac -- albo /*
            elif tok.lower() == "split" and (not stack or stack[-1][0] not in ARRAY_CONSUMERS or stack[-1][1] != 0):
                safe = False  # tablica wstawiona jako tekst to JSON z [ ] i "
        elif tok == "(":
            stack.append([prev.lower() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prev) else "(", 0])
        elif tok == ")":
            if stack:
                stack.pop()
        elif tok == ",":
            if stack:
                stack[-1][1] += 1
        elif not re.fullmatch(r"\d+(?:\.\d+)?|[?\[\]]", tok):
            safe = False
    return literals, safe


# Funkcje dajace liczbe: dozwolone tylko tam, gdzie wynik jest uzyty jako liczba, a nie wstawiony jako tekst.
NUMERIC_FUNCS = {"sub", "add", "mul", "div", "mod", "min", "max", "int", "float", "length", "indexof", "lastindexof"}
_NUMBER_PARAMS = {"substring": 1, "take": 1, "skip": 1}  # od ktorego argumentu funkcja przyjmuje liczby
ARRAY_CONSUMERS = {"first", "last", "join", "length", "empty"}  # zamieniaja tablice z split na tekst albo liczbe
_NUMBER_CONSUMERS = NUMERIC_FUNCS | {"equals", "greater", "less", "greaterorequals", "lessorequals"}


def _numeric_slot(stack: list) -> bool:
    if not stack:
        return False
    func, arg = stack[-1]
    if func in _NUMBER_CONSUMERS:
        return True
    if func in _NUMBER_PARAMS:
        return arg >= _NUMBER_PARAMS[func]
    return func == "if" and arg == 0


def _safe_sql_expr(expr: str) -> bool:
    """Nowe wyrazenie @{...} w zapytaniu: wstawia tylko dane, a jego literaly nie zmieniaja kontekstu SQL."""
    if not (expr.startswith("@{") and expr.endswith("}")):
        return False
    literals, safe = _wdl_literals(expr[2:-1])
    # Apostrofy tylko w literale zwracanym w calosci (if, coalesce, concat): substring, first, take itp. moga
    # wyciac z niego pojedynczy apostrof i zmienic kontekst SQL.
    return safe and all(_SAFE_LITERAL_RE.fullmatch(v) and v.count("'") % 2 == 0
                        and ("'" not in v or set(stack) <= WHOLE_VALUE_FUNCS) for v, stack in literals)


# Slowa zapisu z decyzji Michala Kaszty (2026-11-09) oraz inne polecenia zmieniajace dane, uprawnienia i stan serwera.
SQL_WRITE_WORDS_RE = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|INTO|GRANT|"
                                r"REVOKE|DENY|DECLARE|WAITFOR|BULK|COPY|OPENROWSET|OPENQUERY|OPENDATASOURCE|KILL|"
                                r"SHUTDOWN|DBCC|BACKUP|RESTORE|RECONFIGURE|SETUSER|RENAME)\b", re.I)


def _constant_texts(value) -> list[str]:
    """Stale fragmenty wartosci flow: tekst z wyrazeniami usunietymi, tekst z wyrazeniami zastapionymi sklejonymi
    literalami (DEL@{'ETE'} daje DELETE) i same literaly (bez nazw odwolan i pol)."""
    out: list = []
    strings: list = []
    _leaves(value, strings) if not isinstance(value, str) else strings.append(value)
    for s in strings:
        if s.startswith("@") and not s.startswith(("@{", "@@")):
            lits = [v for v, _ in _wdl_literals(s[1:])[0]]
            out.extend(lits + ["".join(lits)])
            continue
        i, n, buf, glued = 0, len(s), [], []
        while i < n:
            if s.startswith("@@", i):
                buf.append("@")
                glued.append("@")
                i += 2
            elif s.startswith("@{", i):
                depth, j, quote = 0, i + 1, False
                while j < n:
                    c = s[j]
                    if quote:
                        if c == "'":
                            quote = False
                    elif c == "'":
                        quote = True
                    elif c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                lits = [v for v, _ in _wdl_literals(s[i + 2:j])[0]]
                out.extend(lits)
                glued.append("".join(lits))
                i = j + 1
            else:
                buf.append(s[i])
                glued.append(s[i])
                i += 1
        out.extend(["".join(buf), "".join(glued)])
    return out


def _value_expressions(value) -> list[str]:
    """Tresc wszystkich wyrazen w wartosci flow (cale @... i wstawki @{...})."""
    found: list = []
    strings: list = []
    _leaves(value, strings) if not isinstance(value, str) else strings.append(value)
    for s in strings:
        if s.startswith("@") and not s.startswith(("@{", "@@")):
            found.append(s[1:])
            continue
        parts = _flow_sql_parts(s, placeholder=False)[1] if "@{" in s else []
        found.extend(e[2:-1] for e, _, _ in parts)
    return found


def structured_output(action: dict) -> bool:
    """Akcja wbudowana, ktorej stala wartosc to tablica albo obiekt (wstawiane do tekstu jako JSON z [ ] i ")."""
    t = str(action_type(action) or "").lower()
    inputs = _ci(action, "inputs")
    if t == "compose":
        return isinstance(inputs, (list, dict))
    values = [_ci(inputs, "value")]
    variables = _ci(inputs, "variables")
    values += [_ci(v, "value") for v in variables if isinstance(v, dict)] if isinstance(variables, list) else []
    return any(isinstance(v, (list, dict)) for v in values)


def sql_insert_problem(value) -> str | None:
    """Nowa stala wartosc w zrodle wstawki do zapytania SQL: bez srednika, apostrofu i slow zapisu; wyrazenia tylko
    z funkcji na danych (bez dekodowania tekstu: base64ToString, uriComponentToString, json, toUpper i podobnych)."""
    for expr in _value_expressions(value):
        # wejscie wyzwalacza to dane z czasu dzialania (znane ograniczenie), ale funkcje musza byc z listy
        lits, safe = _wdl_literals(expr, SQL_EXPR_REF_FUNCS | {"triggerbody", "triggeroutputs"})
        if not safe:
            return "nowe wyrazenie uzywa funkcji spoza listy funkcji na danych (moglaby zbudowac tekst polecenia)"
        # literaly w wyrazeniach tylko z cyfr, spacji, przecinkow i kropek: z liter if/concat zlozylyby polecenie
        if any(not re.fullmatch(r"[0-9 ,.]*", v) for v, _ in lits):
            return "nowe wyrazenie zawiera literal tekstowy, z ktorego daloby sie zlozyc polecenie SQL"
    for text in _constant_texts(value):
        if ";" in text or "'" in text:
            return "nowa stala wartosc zawiera srednik albo apostrof"
        # znaczniki komentarza, cudzyslow i nawiasy nazw przesuwaja granice literalow w tekscie zapytania
        if any(m in text for m in ("--", "/*", "*/", '"', "[", "]", "\n", "\r")) or \
                text.strip()[:1] in ("-", "/", "*") or text.strip()[-1:] in ("-", "/", "*"):
            return "nowa stala wartosc zawiera znacznik komentarza SQL, cudzyslow, nawias nazwy albo nowa linie"
        m = SQL_WRITE_WORDS_RE.search(text)
        if m:
            return f"nowa stala wartosc zawiera slowo zapisu {m.group(1).upper()}"
    return None


def sql_insert_sources(definition: dict) -> dict:
    """Zrodla wartosci wstawianych do zapytan SQL (domkniecie jak w sensitive_refs): odwolanie -> akcje SQL."""
    try:
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return {}
    out: dict = {}
    for name, (where, action) in actions.items():
        if not sql_query_action(action):
            continue
        params = _ci(_ci(action, "inputs"), "parameters")
        texts = [v for k, v in params.items() if _sql_query_key(k)] if isinstance(params, dict) else []
        body = _ci(_ci(action, "inputs"), "body")
        if body is not None:
            texts.append(body)
        if not texts:
            continue
        for ref in _dependency_closure(actions, [(canon(v), where) for v in texts]):
            out.setdefault(ref, set()).add(name)
    return out


def _value_parts(action: dict) -> dict:
    """Wartosci akcji, ktore moga trafic dalej (bez runAfter, typu i metadanych)."""
    return {k: v for k, v in _own_body(action).items() if str(k).lower() in ("inputs", "foreach", "expression")}


def sql_new_expr_sources_problem(definition: dict, sql_action: str, expr: str) -> str | None:
    """Nowe wyrazenie w zapytaniu: stale w akcjach wbudowanych, z ktorych bierze wartosc, musza przejsc
    sql_insert_problem (inaczej krok podlaczylby do zapytania stala z poleceniem SQL)."""
    try:
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return None
    where = actions[sql_action][0] if sql_action in actions else ()
    refs = _dependency_closure(actions, [(canon(expr), where)])
    if ANY_REF in refs:
        return "ma nazwe wyliczana"
    for n, (_, a) in actions.items():
        if str(action_type(a) or "").lower() in FLOW_SAFE_TYPES and defines(n, a) & refs:
            bad = sql_insert_problem(_value_parts(a)) or \
                ("wartosc to tablica albo obiekt (JSON z [ ] i \")" if structured_output(a) else None)
            if bad:
                return f"ustawia akcja {n}: {bad}"
    return None


def sql_source_hits(defs: set, sources: dict) -> list[str]:
    """Akcje SQL, do ktorych zapytania trafia wartosc ustawiana przez zmieniana akcje."""
    if ANY_REF in sources and defs:
        return sorted(sources[ANY_REF])
    return sorted({a for d in defs for a in sources.get(d, ())})


def _sql_query_key(key) -> bool:
    return any("query" in part for part in str(key).lower().split("/"))


def _flow_sql_text(value) -> str | None:
    return _flow_sql_parts(value)[0]


def _flow_sql_exprs(value) -> list | None:
    """Wyrazenia @{...} w zapytaniu: (wyrazenie, czy poza literalem i komentarzem SQL, czy sklejone z tekstem
    polecenia: litera, cyfra albo -, /, * tuz przed nim, litera albo cyfra tuz po nim)."""
    text, exprs = _flow_sql_parts(value)
    if text is None:
        return None
    _, problem = _strip_sql(text)
    if problem:
        return None
    kinds = _placeholder_contexts(text)
    result = []
    for n, (e, before, after) in enumerate(exprs):
        kind = kinds.get(n, "?")
        # sklejone: litera, cyfra, -, /, * albo drugie wyrazenie (}@{) tuz przed nim albo tuz po nim
        glued = kind == "sql" and (bool(re.match(r"[\w*/}-]", before)) or bool(re.match(r"[\w*/@-]", after)))
        result.append((e, kind, glued))
    return sorted(result)


def _placeholder_contexts(text: str) -> dict:
    """Dla kazdego zzexpr<n>zz rodzaj miejsca w zapytaniu: sql (czesc polecenia), str ('...'), name ([...] albo
    "...") albo comment (-- albo /* */). Przeniesienie wyrazenia miedzy rodzajami zmienia jego znaczenie."""
    kinds: dict = {}

    def mark(segment: str, kind: str) -> None:
        for m in re.finditer(r"zzexpr(\d+)zz", segment):
            kinds[int(m.group(1))] = kind

    i, n = 0, len(text)
    while i < n:
        two, ch = text[i:i + 2], text[i]
        if two == "--":
            ends = [j for j in (text.find("\n", i), text.find("\r", i)) if j >= 0]
            j = min(ends) if ends else n
            mark(text[i:j], "comment")
        elif two == "/*":
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j:j + 2] == "/*":
                    depth, j = depth + 1, j + 2
                elif text[j:j + 2] == "*/":
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            mark(text[i:j], "comment")
        elif ch in "'[\"":
            close = {"'": "'", "[": "]", '"': '"'}[ch]
            j = i + 1
            while j < n:
                if text[j] == close:
                    if text[j + 1:j + 2] == close:
                        j += 2
                        continue
                    break
                j += 1
            j = min(j + 1, n)
            mark(text[i:j], "str" if ch == "'" else "name")
        else:
            m = re.match(r"zzexpr(\d+)zz", text[i:])
            if m:
                kinds[int(m.group(1))] = "sql"
                j = i + m.end()
            else:
                j = i + 1
        i = j
    return kinds


def _flow_sql_parts(value, placeholder: bool = True) -> tuple[str | None, list]:
    """Tekst SQL z wartosci flow: wyrazenia @{...} zastapione nazwami zzexpr<n>zz, jeden koncowy srednik
    pominiety, oraz lista wyrazen (tekst, znak przed, znak po). placeholder=False: wyrazenia usuniete (tak
    sprawdzane jest sklejanie DEL@{''}ETE). None, gdy wartosc jest calym wyrazeniem (@...) albo nie jest tekstem."""
    exprs: list = []
    if not isinstance(value, str):
        return None, exprs
    if value.startswith("@") and not value.startswith(("@{", "@@")):
        return None, exprs
    out, i, n = [], 0, len(value)
    while i < n:
        if value.startswith("@@", i):
            out.append("@")
            i += 2
        elif value.startswith("@{", i):
            depth, j, quote = 0, i + 1, False
            while j < n:
                c = value[j]
                if quote:
                    if c == "'":
                        quote = False
                elif c == "'":
                    quote = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:
                return None, exprs
            out.append(f"zzexpr{len(exprs)}zz" if placeholder else "")
            exprs.append((value[i:j + 1], value[i - 1:i], value[j + 1:j + 2]))
            i = j + 1
        else:
            out.append(value[i])
            i += 1
    text = "".join(out).strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    return text, exprs


def flow_patch_sql_check(target, ch: dict, i: int) -> dict | None:
    """Zmiana tekstu zapytania w akcji SQL: czy stare i nowe zapytanie jest tylko odczytem. None dla innych zmian."""
    if not sql_query_action(target):
        return None
    keys = [str(k).lower() for k in ch["sciezka"][i:]]
    if keys[:1] != ["inputs"] or keys[1:2] not in (["parameters"], ["body"]):
        return None
    if not any(_sql_query_key(k) for k in keys[2:]) and keys[1:2] != ["body"]:
        return None
    old, new = _flow_sql_text(ch.get("bylo")), _flow_sql_text(ch.get("ma_byc"))
    result_new: list = []
    old_ro = old is not None and sql_query_problem(old, check_names=False) is None
    new_problem = "nie da sie sprawdzic (cale wyrazenie albo nie tekst)" if new is None else \
        sql_query_problem(new, check_names=False)
    if new_problem is None:
        empty = _flow_sql_parts(ch.get("ma_byc"), placeholder=False)[0]
        new_problem = sql_query_problem(empty or "", check_names=False)
    if new_problem is None:
        # nowe zapytanie moze miec tylko wyrazenia ze starego, w tym samym miejscu (w literale albo poza nim)
        old_e = {(e, o) for e, o, _ in _flow_sql_exprs(ch.get("bylo")) or []}
        new_e = _flow_sql_exprs(ch.get("ma_byc"))
        if new_e is None:
            new_problem = "nie da sie ustalic polozenia wyrazen @{...}"
        else:
            # Wyrazenie moze wstawic dowolny tekst, wiec dozwolone sa tylko wyrazenia ze starego zapytania, w tym
            # samym miejscu (w literale albo poza nim), i niesklejone z tekstem polecenia.
            fresh = [(e, o) for e, o, _ in new_e if (e, o) not in old_e]
            # nowe wyrazenie tylko w poleceniu albo w literale: w nazwie [..]/".." i w komentarzu wartosc z ] albo
            # nowa linia zamyka nazwe albo komentarz
            extra = [(e, o) for e, o in fresh if o not in ("sql", "str") or not _safe_sql_expr(e)]
            result_new = sorted({e for e, _ in fresh})
            glued = [e for e, _, g in new_e if g]
            if glued:
                new_problem = "wyrazenie " + glued[0][:80] + " jest sklejone z tekstem polecenia SQL"
            elif extra:
                new_problem = ("nowe albo przeniesione wyrazenie " + extra[0][0][:80] + " moze wstawic dowolny tekst "
                               "SQL; dozwolone sa wyrazenia ze starego zapytania w tym samym miejscu i nowe wyrazenia "
                               "na danych (zmienne, wyniki akcji, funkcje jak if, substring, length; literaly tylko z "
                               "cyfr, przecinkow, nawiasow i parzystej liczby apostrofow)")
    result = {"stare_odczyt": old_ro, "nowe_odczyt": new_problem is None, "problem": None,
              "nowe_wyrazenia": result_new}
    if old_ro and new_problem:
        result["problem"] = ("zapytanie SQL bylo tylko odczytem, a nowe nie jest (" + new_problem.replace("ppcall sql: ", "") +
                             "); zmiane zapytania na zapis robi developer")
    return result

# ---------------------------------------------------------------- wyjatek: nazwa pliku w sciezce odczytu SharePoint
# Decyzja Michala Kaszty z 2026-11-04 (ITS-418): flow-patch moze zmienic nazwe pliku czytanego przez akcje
# SharePoint tylko do odczytu, gdy wartosc jest wylacznie ostatnim segmentem `path` przy stalym folderze i witrynie.
SP_READ_OPERATIONS = {"getfilecontentbypath", "getfilemetadatabypath"}
SP_API_RE = re.compile(r"^/providers/Microsoft\.PowerApps/apis/shared_sharepointonline$", re.I)
RESERVED_FILE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
SP_DATASET_RE = re.compile(r"^https://[a-z0-9-]+\.sharepoint\.com(/(sites|teams)/[^/?#@{}%\\]+)?$", re.I)
FILENAME_RE = re.compile(r"^[^/\\:*?\"<>|#%@{}\x00-\x1f]{1,120}\.[A-Za-z0-9]{1,8}$")
SP_FILENAME_LABEL = "wyjatek: nazwa pliku w sciezce odczytu SharePoint"


def filename_problem(old, new) -> str | None:
    """Stara i nowa wartosc to sama nazwa pliku w tym samym folderze, z tym samym rozszerzeniem."""
    for label, v in (("bylo", old), ("ma_byc", new)):
        if (not isinstance(v, str) or not FILENAME_RE.fullmatch(v) or ".." in v or v != v.strip()
                or v.startswith(".")):
            return f"'{label}' nie jest sama nazwa pliku (bez /, \\, .., %, @, {{}}, ?, #, :; z rozszerzeniem)"
        if (unicodedata.normalize("NFKC", v) != v or any(unicodedata.category(c) in ("Cc", "Cf") for c in v)
                or v.rsplit(".", 1)[0].lower() in RESERVED_FILE_NAMES or v.rsplit(".", 1)[0].endswith((" ", "."))):
            return f"'{label}' zawiera znaki niewidoczne, zastepcze albo nazwe zarezerwowana"
    if old.rsplit(".", 1)[1].lower() != new.rsplit(".", 1)[1].lower():
        return "nowa nazwa pliku ma inne rozszerzenie niz stara"
    return None


def sp_write_action(action) -> bool:
    """Akcja konektora SharePoint, ktora nie jest odczytem (Get/List/Search)."""
    if not isinstance(action, dict) or str(action_type(action) or "").lower() not in ("openapiconnection", "apiconnection",
                                                                                      "openapiconnectionwebhook"):
        return False
    host = _ci(_ci(action, "inputs"), "host")
    if not isinstance(host, dict) or not SP_API_RE.search(str(_ci(host, "apiid") or "")):
        return False
    return not re.match(r"(get|list|search)", str(_ci(host, "operationid") or "").lower())


def sp_read_action(action) -> tuple[str, str] | None:
    """(dataset, path) akcji SharePoint tylko do odczytu po sciezce; None dla kazdej innej akcji."""
    if not isinstance(action, dict) or str(action_type(action) or "").lower() != "openapiconnection":
        return None
    inputs = _ci(action, "inputs")
    host = _ci(inputs, "host")
    params = _ci(inputs, "parameters")
    if not isinstance(host, dict) or not isinstance(params, dict):
        return None
    if str(_ci(host, "operationid") or "").lower() not in SP_READ_OPERATIONS:
        return None
    if not SP_API_RE.search(str(_ci(host, "apiid") or "")):
        return None
    dataset, path = _ci(params, "dataset"), _ci(params, "path")
    if not isinstance(dataset, str) or not isinstance(path, str) or not SP_DATASET_RE.fullmatch(dataset.rstrip("/")):
        return None
    return dataset.rstrip("/"), path


def _folder_ok(folder: str) -> bool:
    """Staly folder: zaczyna sie od /, bez wyrazen, bez .. i znakow kodowania."""
    return (folder.startswith("/") and not re.search(r"[@{}%?#\\]", folder)
            and ".." not in folder.split("/") and "//" not in folder)


def derived_consumers_problem(definition: dict, start: list[str]) -> str | None:
    """Dokad trafia wynik akcji odczytu (i wszystko z niego wyliczone): wolno tylko do akcji wbudowanych
    (FLOW_SAFE_TYPES) i do tresci (body) akcji zapisu SharePoint. Kazde inne uzycie wylacza wyjatek."""
    try:
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return "nie mozna odczytac akcji flow"
    by_name = {n.lower(): a for n, (_, a) in actions.items()}

    def ancestors(where) -> list[str]:
        return [part[0].lower() for part in where]

    def mark(name: str, where) -> None:
        # wynik akcji jest tez w result('<zakres>') kazdego zakresu nad nia
        derived.add(("act", name.lower()))
        derived.update(("act", anc) for anc in ancestors(where))

    derived: set = set()
    for n in start:
        mark(n, actions[n][0] if n in actions else ())
    changed = True
    while changed:
        changed = False
        for n, (where, a) in actions.items():
            key = ("act", n.lower())
            if key in derived:
                continue
            refs = _refs(canon(_own_body(a)))
            if ANY_REF in refs:
                return f"akcja {n} ma odwolanie z nazwa wyliczana"
            # item() / items() w petli po wartosci wyliczonej z pliku nie wskazuje akcji z nazwy
            in_loop = any(("act", anc) in derived and str(action_type(by_name.get(anc, {})) or "").lower()
                          in ("foreach", "until") for anc in ancestors(where))
            if not refs & derived and not in_loop:
                continue
            t = str(action_type(a) or "").lower()
            if t in FLOW_SAFE_TYPES:
                mark(n, where)
                derived |= {r for r in defines(n, a) if r[0] == "var"}
            elif sp_write_action(a) and not in_loop:
                rest = copy.deepcopy(_own_body(a))
                params = _ci(_ci(rest, "inputs"), "parameters")
                if isinstance(params, dict):
                    for k in [k for k in params if str(k).lower() == "body"]:
                        params[k] = ""
                if _refs(canon(rest)) & derived:
                    return f"wynik odczytu trafia do parametru akcji {n} innego niz tresc pliku"
                continue  # wynik zapisu (id, sciezka nowego pliku) zalezy od folderu i nazwy, nie od tresci
            else:
                return f"wynik odczytu trafia do akcji {n} ({action_type(a)}), ktora nie jest akcja wbudowana ani zapisem tresci SharePoint"
            changed = True
    return None


def sp_filename_exception(definition: dict, ch: dict) -> tuple[dict | None, str | None]:
    """Czy zmiana miesci sie w wyjatku. (opis, None) gdy tak; (None, powod) gdy zmiana dotyczy akcji odczytu
    albo jej zmiennej, ale warunki nie sa spelnione; (None, None) gdy to w ogole nie ten przypadek."""
    path = ch["sciezka"]
    i, problem = _split_flow_patch_path(path)
    if problem:
        return None, None
    name = path[i - 1]
    try:
        target = get_at(definition, path[:i])
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return None, None
    keys = path[i:]
    # 1. Literal w inputs/parameters/path akcji odczytu
    if [k.lower() for k in keys] == ["inputs", "parameters", "path"]:
        found = sp_read_action(target)
        if not found:
            return None, "klucz path wolno zmienic tylko w akcji SharePoint tylko do odczytu (GetFileContentByPath, GetFileMetadataByPath)"
        old, new = ch.get("bylo"), ch.get("ma_byc")
        if not isinstance(old, str) or not isinstance(new, str) or "/" not in old or "/" not in new:
            return None, "path musi byc stalym tekstem z folderem i nazwa pliku"
        old_dir, old_file = old.rsplit("/", 1)
        new_dir, new_file = new.rsplit("/", 1)
        if old_dir != new_dir or not _folder_ok(old_dir + "/"):
            return None, "folder w path musi zostac ten sam i nie moze zawierac wyrazen"
        bad = filename_problem(old_file, new_file)
        if bad:
            return None, bad
        if ("act", name.lower()) in sensitive_refs(definition) or ANY_REF in sensitive_refs(definition):
            return None, f"wynik akcji {name} trafia do adresu, identyfikatora albo uwierzytelnienia innej akcji"
        problem = derived_consumers_problem(definition, [name])
        if problem:
            return None, problem
        return {"akcje": [name], "dataset": found[0], "folder": old_dir, "plik": new_file, "stary_plik": old_file}, None
    # 2. Wartosc zmiennej uzytej jako ostatni segment path akcji odczytu
    t = action_type(target)
    if str(t or "").lower() not in ("setvariable", "initializevariable"):
        return None, None
    var = None
    if str(t).lower() == "setvariable" and [k.lower() for k in keys] == ["inputs", "value"]:
        var = _ci(_ci(target, "inputs"), "name")
    elif str(t).lower() == "initializevariable" and len(keys) == 4 and keys[:2] == ["inputs", "variables"]:
        try:
            var = _ci(get_at(definition, path[:-1]), "name")
        except KeyError:
            var = None
    if not isinstance(var, str):
        return None, None
    v = var.lower()
    users, folders, datasets, setters = [], set(), set(), []
    pattern = re.compile(r"^(?P<folder>/[^@{}]*)/@\{variables\('" + re.escape(var) + r"'\)\}$", re.I)
    for aname, (_, action) in actions.items():
        if defines(aname, action) == {("act", aname.lower()), ("var", v)} and str(action_type(action) or "").lower() in (
                "setvariable", "initializevariable"):
            setters.append(aname)  # ustawia tylko te zmienna; czy ktos czyta jej wynik, sprawdzane nizej
            continue
        text = canon(_own_body(action))
        if ("var", v) not in _refs(text) and ANY_REF not in _refs(text):
            continue
        found = sp_read_action(action)
        if not found:
            return None, (f"zmienna {var} trafia tez do akcji {aname} ({action_type(action)}), ktora nie jest "
                          "odczytem SharePoint po sciezce")
        m = pattern.fullmatch(found[1])
        if not m or not _folder_ok(m.group("folder") + "/"):
            return None, (f"w akcji {aname} zmienna {var} nie jest calym ostatnim segmentem path przy stalym folderze")
        rest = copy.deepcopy(_own_body(action))
        params = _ci(_ci(rest, "inputs"), "parameters")
        for k in [k for k in params if str(k).lower() == "path"]:
            params[k] = ""
        if ("var", v) in _refs(canon(rest)) or ANY_REF in _refs(canon(rest)):
            return None, f"w akcji {aname} zmienna {var} wystepuje poza path"
        users.append(aname)
        folders.add(m.group("folder"))
        datasets.add(found[0])
    if not users:
        return None, None
    sensitive = sensitive_refs(definition)
    if ANY_REF in sensitive:
        return None, "flow ma odwolania z nazwa wyliczana, zaleznosci nie da sie ustalic"
    for aname in users:
        if ("act", aname.lower()) in sensitive:
            return None, (f"wynik akcji {aname} trafia do adresu, identyfikatora albo uwierzytelnienia innej akcji "
                          "(np. Http, DeleteFile), wiec zmiana pliku zmienilaby jej cel")
    problem = derived_consumers_problem(definition, users)
    if problem:
        return None, problem
    every_text = [canon(_own_body(a)) for n, (_, a) in actions.items()]
    for aname in setters:
        if any(("act", aname.lower()) in _refs(t) for t in every_text):
            return None, f"wynik akcji {aname} jest uzywany przez inna akcje"
    bad = filename_problem(ch.get("bylo"), ch.get("ma_byc"))
    if bad:
        return None, bad
    if len(folders) != 1 or len(datasets) != 1:
        return None, f"zmienna {var} jest czytana z roznych folderow albo witryn"
    return {"akcje": sorted(users), "dataset": datasets.pop(), "folder": folders.pop(), "plik": ch["ma_byc"],
            "stary_plik": ch["bylo"], "zmienna": var}, None


def flow_patch_exceptions(step: dict, definition: dict) -> list[dict]:
    """Zmiany kroku objete wyjatkiem nazwy pliku SharePoint (z etykieta sciezki)."""
    out = []
    for ch in step.get("zmiany") or []:
        exception, _ = sp_filename_exception(definition, ch)
        if exception:
            out.append({**exception, "zmiana": flow_patch_label(ch["sciezka"])})
    return out


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
    path = step_file_path(folder, step["plik"], str(step.get("sha256", "")))
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
    """Klucze zabronione; klucz splaszczony ("parameters/method") sprawdzany segment po segmencie."""
    return sorted(k for k in keys if any(part in FLOW_PATCH_FORBIDDEN_KEYS or part.startswith(FLOW_PATCH_FORBIDDEN_PREFIXES)
                                         for part in str(k).lower().split("/")))


def _without_actions(definition: dict) -> dict:
    d = copy.deepcopy(definition)
    if isinstance(d, dict) and isinstance(d.get("properties"), dict) \
            and isinstance(d["properties"].get("definition"), dict):
        d["properties"]["definition"].pop("actions", None)
    return d


def _leaves(obj, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(str(k))
            _leaves(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _leaves(v, out)
    elif isinstance(obj, str):
        out.append(obj)
    elif obj is not None:
        out.append(json.dumps(obj))


def _expr_view(text: str) -> str:
    """Tekst do wyszukiwania odwolan: z zapisu JSON (canon) surowe teksty i klucze, po jednym w linii. Nowa
    linia i tabulator przed nazwa funkcji sa wtedy zwyklymi odstepami, a nazwy z " albo \\ zostaja jak w defines()."""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(value, str):
        return value
    parts: list = []
    _leaves(value, parts)
    return "\n".join(parts)


def _refs(text: str) -> set:
    """Odwolania do zmiennych i akcji; nazwy malymi literami (Power Automate ich nie rozroznia)."""
    text = _expr_view(text)
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


# item() w petli (1.4.1, ITS-418, decyzja Michala Kaszty z 2026-11-09): petla jest zrodlem adresu tylko wtedy, gdy
# wartosc, od ktorej zalezy adres, zawiera item(), items('<petla>') albo iterationIndexes('<petla>').
_ITEMS_RE = re.compile(r"\bitems\s*\(\s*'((?:[^']|'')*)'\s*\)", re.I)
_ITERATION_RE = re.compile(r"\biterationIndexes\s*\(\s*'((?:[^']|'')*)'\s*\)", re.I)
_ITERATION_DYNAMIC_RE = re.compile(r"\biterationIndexes\s*\((?!\s*')", re.I)
_ITEM_RE = re.compile(r"\bitem\s*\(\s*\)", re.I)
# Akcje, w ktorych item() poza polem 'from' oznacza biezacy element ich wlasnej tablicy, a nie petli.
IMPLICIT_ITEM_TYPES = {"select", "query", "table"}
# Zakresy nad zrodlem wartosci: warunek decyduje, czy i ktora wartosc zrodlo ustawi, a runAfter zakresu (takze
# Scope, od 1.4.2) decyduje, kiedy ja ustawi wzgledem akcji wysylajacej.
CONTROL_TYPES = {"if", "switch", "until", "foreach", "scope"}


def _expr_refs(text: str) -> set:
    """Odwolania po nazwie bez items('<petla>') (te obsluguje _loop_refs, bez rozwijania calej petli)."""
    text = _expr_view(text)
    refs = _refs(_ITEMS_RE.sub(" ", text))
    if _ITERATION_DYNAMIC_RE.search(text):
        refs.add(ANY_REF)
    return refs


def _loop_refs(text: str, where: tuple, action: dict | None, by_name: dict) -> set:
    """Petle, z ktorych element bierze wartosc: items('X') i iterationIndexes('X') wprost, item() to
    najblizsza petla foreach obejmujaca akcje, w ktorej stoi wyrazenie."""
    text = _expr_view(text)
    loops = {m.replace("''", "'").lower() for m in _ITEMS_RE.findall(text) + _ITERATION_RE.findall(text)}
    item_text = text
    if action is not None and str(action_type(action) or "").lower() in IMPLICIT_ITEM_TYPES:
        item_text = _expr_view(canon(_ci(_ci(action, "inputs"), "from")))
    if _ITEM_RE.search(item_text):
        for part in reversed(where):
            parent = by_name.get(part[0].lower())
            if parent is not None and str(action_type(parent) or "").lower() == "foreach":
                loops.add(part[0].lower())
                break
    return loops


def sensitive_refs(definition: dict) -> set:
    """Zmienne i akcje, z ktorych akcje wysylajace (Http, konektory i inne spoza FLOW_SAFE_TYPES) biora adres,
    naglowki albo uwierzytelnienie. Zmiana ich wartosci przekierowalaby istniejace wywolanie."""
    try:
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return set()
    return _dependency_closure(actions, [s for seeds in _sender_seeds(actions).values() for s in seeds])


def sender_refs(definition: dict) -> dict:
    """Dla kazdej akcji wysylajacej (nazwa malymi literami): od czego zalezy jej adres (domkniecie)."""
    try:
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return {}
    return {n.lower(): _dependency_closure(actions, seeds) for n, seeds in _sender_seeds(actions).items()}


def _sender_seeds(actions: dict) -> dict:
    """Akcja wysylajaca -> teksty (z polozeniem), z ktorych bierze adres, naglowki, uwierzytelnienie, polaczenie."""
    out: dict = {}
    for name, (where, action) in actions.items():
        if str(action_type(action) or "").lower() in FLOW_SAFE_TYPES:
            continue
        seeds = out.setdefault(name, [])
        own = _own_body(action)
        values: list = []
        _routing_values(own, values)
        inputs = next((v for k, v in own.items() if str(k).lower() == "inputs"), None)
        if inputs is not None and not isinstance(inputs, dict):
            values.append(inputs)  # cale inputs jako wyrazenie: adres moze byc w nim
        if sp_write_action(action):
            # zapis, przeniesienie, usuniecie w SharePoint: cel wyznaczaja wszystkie parametry poza trescia pliku
            params = _ci(_ci(own, "inputs"), "parameters")
            if isinstance(params, dict):
                values.extend(v for k, v in params.items() if str(k).lower() != "body")
        if sql_query_action(action):
            # akcja SQL: serwer i baza (wszystko poza tekstem zapytania) wskazuja cel
            params = _ci(_ci(own, "inputs"), "parameters")
            if isinstance(params, dict):
                values.extend(v for k, v in params.items() if not _sql_query_key(k))
        seeds.extend((canon(v), where) for v in values)  # akcja wysylajaca: item() to element petli, w ktorej lezy
    return out


def _dependency_closure(actions: dict, seeds: list) -> set:
    """Odwolania z tekstow seeds (tekst, polozenie) i wszystko, z czego biora wartosc (domkniecie)."""
    by_name = {n.lower(): a for n, (_, a) in actions.items()}
    where_of = {n.lower(): w for n, (w, _) in actions.items()}
    # result('<zakres>') zwraca wyniki akcji wewnatrz zakresu: zakres zalezy od wszystkich swoich akcji
    nested: dict[str, list[str]] = {}
    for n, (where, _) in actions.items():
        for part in where:
            nested.setdefault(part[0].lower(), []).append(n.lower())
    found: set = set()
    queued: set = set()
    queue: list = []

    def push(ref) -> None:
        found.add(("act", ref[1]) if ref[0] == "loop" else ref)
        if ref not in queued:
            queued.add(ref)
            queue.append(ref)

    def scan(text: str, where: tuple, action: dict | None) -> None:
        for ref in _expr_refs(text):
            push(ref)
        for loop in _loop_refs(text, where, action, by_name):
            push(("loop", loop))

    def scan_source(name: str) -> None:
        """Tresc zrodla i wyrazenia wszystkich obejmujacych je warunkow i petli (If, Switch, Until, Foreach):
        warunek decyduje, czy i ktora wartosc zrodlo ustawi."""
        scan(canon(_own_body(by_name[name])), where_of[name], by_name[name])
        for part in where_of[name]:
            parent = by_name.get(part[0].lower())
            if parent is not None and str(action_type(parent) or "").lower() in CONTROL_TYPES:
                push(("loop", part[0].lower()))

    for text, where in seeds:
        scan(text, where, None)
    # Domkniecie: skad biora wartosci akcje i zmienne, od ktorych zalezy adres (Compose -> Select -> Http).
    while queue:
        ref = queue.pop()
        if ref == ANY_REF:
            continue
        kind, name = ref
        if kind == "loop":
            # petla dodana przez item(): zrodlem jest tylko jej wlasna tresc (foreach), nie akcje w srodku
            if name in by_name:
                scan_source(name)
            continue
        if kind == "act":
            for inner in nested.get(name, []):
                push(("act", inner))
            sources = [name] if name in by_name else []
        else:
            sources = [n for n, a in by_name.items() if ref in defines(n, a)]
        for src in sources:
            scan_source(src)
    return found


# ---------------------------------------------------------------- kolejnosc wykonania (1.4.2)
# Decyzja Michala Kaszty z 2026-11-09 (opcja b): krok nie moze zmienic, czy akcja ustawiajaca wartosc wrazliwa
# wykonuje sie przed akcja wysylajaca albo po niej, ani kolejnosci dwoch akcji ustawiajacych te sama wartosc.
# Inaczej wysylka dostalaby inna, juz istniejaca wartosc adresu (np. SetGood/SetEvil, przepiete Http).

def _exec_order(definition: dict):
    """(akcje, lancuch przodkow kazdej akcji, bezposrednie runAfter w zakresie)."""
    try:
        actions, _ = flatten_actions(get_at(definition, ["properties", "definition", "actions"]))
    except KeyError:
        return {}, {}, {}
    chains, preds = {}, {}
    for n, (where, a) in actions.items():
        chain, w = [], ()
        for part in where:
            chain.append((w, part[0].lower()))
            w = w + (part,)
        chain.append((w, n.lower()))
        chains[n.lower()] = chain
        ra = a.get("runAfter") if isinstance(a, dict) else None
        preds[(w, n.lower())] = {str(k).lower() for k in ra} if isinstance(ra, dict) else set()
    return actions, chains, preds


def _reaches(preds: dict, scope: tuple, start: str, goal: str) -> bool:
    """Czy goal jest (posrednio) w runAfter akcji start w tym samym zakresie."""
    seen, todo = set(), [start]
    while todo:
        cur = todo.pop()
        for p in preds.get((scope, cur), ()):
            if p == goal:
                return True
            if p not in seen:
                seen.add(p)
                todo.append(p)
    return False


def _relation(chains: dict, preds: dict, x: str, y: str) -> str:
    """przed / po / zagniezdzone / niezalezne (galezie rownolegle albo rozne galezie warunku)."""
    cx, cy = chains[x], chains[y]
    i = 0
    while i < min(len(cx), len(cy)) and cx[i] == cy[i]:
        i += 1
    if i == len(cx) or i == len(cy):
        return "zagniezdzone"
    (wx, nx), (wy, ny) = cx[i], cy[i]
    if wx != wy:
        return "wylaczne"  # rozne galezie warunku albo przypadki Switch
    if _reaches(preds, wx, ny, nx):
        return "przed"
    if _reaches(preds, wx, nx, ny):
        return "po"
    return "rownolegle"  # wyscig: wynik zalezy od czasu wykonania galezi


def _timing_set(chains: dict, preds: dict, x: str, y: str) -> set:
    """Akcje, od ktorych zalezy czas wykonania x i y: przodkowie obu oraz domkniecie runAfter kazdego elementu
    lancucha w jego zakresie."""
    out: set = set()
    for name in (x, y):
        for scope, n in chains[name]:
            out.add(n)
            todo = [n]
            while todo:
                cur = todo.pop()
                for p in preds.get((scope, cur), ()):
                    if p not in out:
                        out.add(p)
                        todo.append(p)
    return out


def execution_order_problems(old: dict, new: dict) -> list[str]:
    """Zmiany kolejnosci miedzy akcjami ustawiajacymi wartosci wrazliwe a akcjami wysylajacymi."""
    sensitive = sensitive_refs(old) | sensitive_refs(new)
    if not sensitive:
        return []
    o_actions, o_chains, o_preds = _exec_order(old)
    n_actions, n_chains, n_preds = _exec_order(new)
    common = set(o_chains) & set(n_chains)
    by_old = {n.lower(): a for n, (_, a) in o_actions.items()}
    by_new = {n.lower(): a for n, (_, a) in n_actions.items()}
    setters: dict = {}
    for n in common:
        refs = (defines(n, by_old[n]) | defines(n, by_new[n])) & sensitive
        if ANY_REF in sensitive:
            refs = refs | {ANY_REF}
        if refs:
            setters[n] = refs
    senders = {n for n in common if str(action_type(by_old[n]) or "").lower() not in FLOW_SAFE_TYPES
               or str(action_type(by_new[n]) or "").lower() not in FLOW_SAFE_TYPES}
    errs: list = []
    # para tylko wtedy, gdy adres tej akcji wysylajacej zalezy od wartosci ustawianej przez te akcje
    per_sender: dict = {}
    for refs_map in (sender_refs(old), sender_refs(new)):
        for h, refs in refs_map.items():
            per_sender.setdefault(h, set()).update(refs)
    # Posrednicy (Compose czytajacy zmienna z adresem itp.) tez "odbieraja" wartosc w chwili wykonania, wiec ich
    # kolejnosc wzgledem akcji ustawiajacych jest sprawdzana jak kolejnosc akcji wysylajacej.
    readers = {n for n in common if ("act", n) in sensitive and n not in senders}
    for defn, acts in ((old, o_actions), (new, n_actions)):
        for name, (where, a) in acts.items():
            if name.lower() in readers:
                per_sender.setdefault(name.lower(), set()).update(
                    _dependency_closure(acts, [(canon(_own_body(a)), where)]))
    pairs = []
    for h in senders | readers:
        deps = per_sender.get(h, set())
        related = [s for s in setters if s != h and (ANY_REF in deps or setters[s] & deps)]
        pairs += [(s, h) for s in related]
        related.sort()
        pairs += [(a, b) for i, a in enumerate(related) for b in related[i + 1:]
                  if (setters[a] & setters[b] & deps) - {("act", a), ("act", b)}]
    # akcje, ktorych zmiana w kroku moze zmienic czas wykonania: dodane; Wait, petle, zakresy, warunki i konektory
    # przy kazdej zmianie; akcje wbudowane na danych (Compose, zmienne, Select...) tylko przy zmianie runAfter, typu
    # albo runtimeConfiguration (zmiana samej wartosci kontroluje sensitive_hit)
    def _timing_view(a: dict) -> str:
        own = _own_body(a)
        if str(action_type(a) or "").lower() in FLOW_SAFE_TYPES - {"wait", "until", "foreach", "scope", "if", "switch"}:
            own = {k: v for k, v in own.items() if str(k).lower() in ("runafter", "type", "runtimeconfiguration")}
        return canon(own)
    touched = {n for n in n_chains if n not in by_old or _timing_view(by_old[n]) != _timing_view(by_new[n])}
    for x, y in pairs:
        before, after = _relation(o_chains, o_preds, x, y), _relation(n_chains, n_preds, x, y)
        if before != after:
            errs.append(f"zmiana kolejnosci wykonania: {x} wzgledem {y} ({before} -> {after}); od kolejnosci zalezy "
                        "adres, naglowki albo uwierzytelnienie akcji wysylajacej")
        elif after == "rownolegle":
            # wyscig: krok nie moze zmienic niczego, od czego zalezy czas wykonania obu galezi (Wait, Until, petla)
            ts = _timing_set(o_chains, o_preds, x, y) | _timing_set(n_chains, n_preds, x, y)
            # takze akcja w srodku przodka (np. Wait w Scope poprzedzajacym akcje wysylajaca)
            hit = sorted(t for t in touched if any(p in ts for _, p in n_chains.get(t, [])))
            if hit:
                errs.append(f"{x} i {y} wykonuja sie rownolegle, a krok zmienia {', '.join(hit[:3])}, od czego zalezy, "
                            "ktora wartosc adresu wygra")
    return sorted(set(errs))


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
                "odczyt_hasel": len(SECRET_REF_RE.findall(_expr_view(text))) >
                len(SECRET_REF_RE.findall(_expr_view(old_text))),
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
        errs.extend(execution_order_problems(old, new))
        sensitive = sensitive_refs(old) | sensitive_refs(new)
        sql_sources = sql_insert_sources(old)
        for ref, names in sql_insert_sources(new).items():
            sql_sources.setdefault(ref, set()).update(names)
        new_actions, _ = flatten_actions(get_at(new, ["properties", "definition", "actions"]))
        for c in changes:
            c["sql"] = sql_source_hits(c["definiuje"], sql_sources)
            if c["sql"] and c["zmiana"] != "usunieta" and c["akcja"] in new_actions:
                new_action = new_actions[c["akcja"]][1]
                bad = sql_insert_problem(_value_parts(new_action)) or \
                    ("wartosc to tablica albo obiekt (JSON z [ ] i \")" if structured_output(new_action) else None)
                if bad:
                    errs.append(f"{c['akcja']}: zmienia dane wstawiane do zapytania SQL ({', '.join(c['sql'])}): {bad}")
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

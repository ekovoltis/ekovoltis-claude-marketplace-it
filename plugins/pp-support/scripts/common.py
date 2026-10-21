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
from urllib.parse import urlparse

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


def incident_dir(cwd: str | Path, incident_id: str) -> Path:
    return Path(cwd) / INCIDENTS_DIR_NAME / incident_id


def valid_id(incident_id: str) -> bool:
    return bool(re.fullmatch(ID_RE, incident_id or ""))


RESERVED_INCIDENT_FILES = {"plan.md", "wykonanie.jsonl"}


def out_path_problem(cwd: str | Path, path: str) -> str | None:
    """Cel `ppcall get --out`: tylko plik w <cwd>/zgloszenia/<ID>/. Sprawdzaja hook i ppcall."""
    if not path:
        return "Brak sciezki --out."
    if ":" in Path(path).name:  # strumienie NTFS (plik:strumien)
        return "--out: niedozwolona nazwa pliku."
    base = Path(cwd).expanduser().resolve()
    target = Path(path).expanduser()
    target = (target if target.is_absolute() else base / target).resolve()
    where = f"--out: zapis tylko do pliku w {INCIDENTS_DIR_NAME}/<ID>/ w katalogu pracy."
    try:
        parts = target.relative_to((base / INCIDENTS_DIR_NAME).resolve()).parts
    except ValueError:
        return where
    if len(parts) < 2 or not valid_id(parts[0]):
        return where
    # Windows pomija koncowe kropki i spacje: "plan.md." to ten sam plik co plan.md
    if len(parts) == 2 and parts[1].rstrip(". ").lower() in RESERVED_INCIDENT_FILES:
        return "--out: plan.md i wykonanie.jsonl nie moga byc nadpisane odpowiedzia HTTP."
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
    return cfg


def load_config() -> dict:
    path = state_dir() / "config.json"
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            cfg.update(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            pass
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
]
CLOUD_HOST_RE = re.compile("|".join(CLOUD_HOST_PATTERNS), re.I)


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
            errs.append(f"krok {expected_nr}: zmiana definicji flow na produkcji jest zabroniona (SWO: dev, potem wdrozenie)")
        if DATAVERSE_HOST_RE.match(host) and "/workflows" in url.lower() and "clientdata" in body:
            errs.append(f"krok {expected_nr}: zmiana logiki flow przez tabele workflows jest zabroniona")
        if "vault.azure.net" in url.lower():
            errs.append(f"krok {expected_nr}: operacje na Key Vault sa zabronione")
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
        errs.append(f"krok {expected_nr}: 'typ' musi byc 'http' albo 'cli'")
    return errs


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
    return errs, steps


def step_summary(step: dict) -> str:
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

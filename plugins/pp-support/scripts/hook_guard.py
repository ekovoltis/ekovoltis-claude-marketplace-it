"""PreToolUse: straznik pluginu pp-support.

Zasady:
  * ruch do chmury Microsoft wylacznie przez ppcall (zadnego curl, az rest, Invoke-RestMethod)
  * zadnego pobierania tokenow i sekretow poza ppcall
  * pac i az tylko w trybie odczytu; kazda zmiana to krok zatwierdzonego planu
  * ppcall exec dopuszczany tylko przy waznej akceptacji, a i tak z pytaniem do czlowieka
  * zapis do stanu akceptacji i do kodu pluginu jest zablokowany
Kazdy blad wewnetrzny konczy sie odmowa (fail closed).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

TOKEN_RE = re.compile(r"get-access-token|Get-AzAccessToken|Get-MsalToken|acquire_token|"
                      r"az\s+login\b|Connect-(AzAccount|MgGraph|PnPOnline)|\bmsal\b", re.I)
PS_MUTATION_RE = re.compile(
    r"\b(Set|New|Remove|Update|Start|Stop|Restart|Enable|Disable|Add|Grant|Revoke|Clear|Reset|Publish|"
    r"Import|Invoke|Register|Unregister|Move|Rename|Restore|Approve|Deny|Suspend|Resume)-"
    r"(Az|Admin|PowerApp|Flow|PnP|Mg|Crm|Dataverse|Fabric|Sp)\w*", re.I)
STATE_RE = re.compile(re.escape(C.STATE_DIR_NAME), re.I)
# Proby dojscia do stanu z pominieciem pelnej nazwy: wzorce nazw i nazwy podkatalogow. Sprawdzane tylko,
# gdy polecenie wskazuje katalog domowy albo .claude (HOME_ANCHOR_RE) lub cwd jest w domu albo w .claude,
# zeby praca nad kodem pluginu i zwykle polecenia ze slowem "akceptacje" nie byly blokowane.
# Sklejanie nazwy z czesci wylapuje _dequote + STATE_RE.
STATE_EVASION_RE = re.compile(
    r"support-st|pp-?supp[^\s/\\@]*[*?\[\]]|\.claude[\\/]+[^\s\\/]*[*?\[]|"
    r"[\\/](akceptacje|sesje)([\\/*?\[]|\b)|\b(akceptacje|sesje)[\\/*?\[]", re.I)
HOME_ANCHOR_RE = re.compile(
    r"(?<![\w~])~(?=[/\\\s\"';&|)]|$)|\$\{?HOME\b|\$env:HOME|USERPROFILE|HOMEPATH|GetFolderPath|"
    r"\.claude|(?<![\w.])\.[^\s/\\.]*[*?\[]|(?<![\w.])\.\.(?=[/\\\s\"';&|)]|$)", re.I)  # .c*, .[c]laude, ..
GLOB_WORD_RE = re.compile(r"[^\s/\\;&|()<>=]*[*?\[][^\s/\\;&|()<>=]*")
# Zawsze, bez kotwicy: wzorzec po pp-supp (jak w 1.1.0) i fragment nazwy w kodzie podanym interpreterowi
# (python -c "'%s-state' % 'pp-support'"), bo zmienna albo formatowanie napisu omija kotwice.
STATE_GLOB_ALWAYS_RE = re.compile(r"pp-?supp[^\s/\\@]*[*?\[\]]", re.I)
INLINE_CODE_RE = re.compile(r"(^|[\s/\\])(python[\d.]*|py|node|perl|ruby|pwsh|powershell)(\.exe)?\s"
                            r"(.*\s)?-(c|e|Command|EncodedCommand)\b", re.I)
STATE_PART_RE = re.compile(r"pp-?supp|support-st", re.I)


def _dequote(s: str) -> str:
    """Polecenie bez cudzyslowow, backtickow i laczenia napisow: 'pp-support-sta''te', 'pp-' + 'x', 'pp-' 'x'."""
    s = re.sub(r"['\"`]\s*[+.]?\s*['\"`]", "", s)  # sasiednie literaly (Python, Perl, PHP)
    return re.sub(r"\s*\+\s*", "", re.sub(r"['\"`]", "", s))


def _home() -> str:
    return _norm_path(str(Path.home())).rstrip("/")


def _home_cwd(cwd: str) -> bool:
    c, home = _norm_path(cwd).rstrip("/"), _home()
    return c == home or c == home + "/.claude" or c.startswith(home + "/.claude/")


def _glob_under_home(text: str) -> bool:
    """Wzorzec tuz za katalogiem domowym podanym wprost: C:\\Users\\x\\*\\pp-s*, /c/Users/x/.c?aude/..."""
    t = re.sub(r"/+", "/", text.replace("\\", "/").lower())
    t = re.sub(r"(?<![\w.:])/([a-z])/", r"\1:/", t)
    return bool(re.search(re.escape(_home()) + r"/[^/\s]*[*?\[]", t))


def _glob_hits_state(text: str, min_literal: int = 2) -> bool:
    """Wzorzec z co najmniej min_literal znakami doslownymi, ktory pasuje do nazwy katalogu stanu (pp-s*, *-state)."""
    for word in GLOB_WORD_RE.findall(text):
        if len(re.sub(r"[*?]|\[[^\]]*\]?", "", word)) >= min_literal and fnmatch.fnmatch(C.STATE_DIR_NAME, word.lower()):
            return True
    return False


def touches_backup(s: str, cwd: str) -> bool:
    """Polecenie powloki siegajace do zgloszenia/<ID>/backup (odczyt, kopiowanie, zapis)."""
    text = _dequote(s).replace("\\", "/").lower()
    if re.search(rf"{C.INCIDENTS_DIR_NAME}/[^/\s]+/{C.BACKUP_DIR_NAME}(/|[\s*?\[]|$)", text):
        return True
    try:
        root = _norm_path(str(C.incidents_root(cwd))).rstrip("/")
        here = _norm_path(str(Path(cwd).resolve())).rstrip("/")
    except (OSError, ValueError):
        return True
    if root and root in _norm_path(text) and f"/{C.BACKUP_DIR_NAME}" in text:
        return True
    rel = here[len(root) + 1:].split("/") if here.startswith(root + "/") else []
    in_backup = (len(rel) >= 2 and rel[1] == C.BACKUP_DIR_NAME) or bool(
        re.search(rf"/{C.INCIDENTS_DIR_NAME}/[^/]+/{C.BACKUP_DIR_NAME}(/|$)", here))
    in_incident = len(rel) == 1 or bool(re.search(rf"/{C.INCIDENTS_DIR_NAME}/[^/]+$", here))
    if in_backup:
        return True  # biezacy katalog to kopia
    if in_incident and re.search(rf"(^|[\s/\"'=]){C.BACKUP_DIR_NAME}([/\s*?\[]|$)", text):
        return True  # biezacy katalog to zgloszenie, odwolanie wzgledne do backup/
    return False


def touches_state(s: str, cwd: str, full: str | None = None) -> bool:
    """s: segment polecenia; full: cale polecenie (kotwica z innego segmentu, np. cd ~/.claude && rm ...)."""
    deq = _dequote(s)
    state = _norm_path(str(C.state_base()))
    if STATE_RE.search(s) or STATE_RE.search(deq) or state in _norm_path(s) or state in _norm_path(deq):
        return True
    try:
        if Path(cwd).expanduser().resolve().is_relative_to(C.state_base().expanduser().resolve()):
            return True
    except (OSError, ValueError):
        pass
    if (STATE_GLOB_ALWAYS_RE.search(s) or STATE_GLOB_ALWAYS_RE.search(deq) or _glob_hits_state(deq, 5)
            or (INLINE_CODE_RE.search(s) and STATE_PART_RE.search(deq))):
        return True
    whole = s if full is None else full
    dwhole = _dequote(whole)
    if not (HOME_ANCHOR_RE.search(whole) or HOME_ANCHOR_RE.search(dwhole) or _home_cwd(cwd)
            or _glob_under_home(whole) or _glob_under_home(dwhole)):
        return False
    return bool(STATE_EVASION_RE.search(s) or STATE_EVASION_RE.search(deq) or _glob_hits_state(deq))

PAC_READ = {
    ("help",), ("auth", "list"), ("auth", "who"), ("auth", "select"), ("org", "who"), ("org", "list"),
    ("env", "list"), ("env", "who"), ("admin", "list"), ("solution", "list"), ("solution", "export"),
    ("solution", "unpack"), ("connection", "list"), ("connector", "list"), ("application", "list"),
    ("pipeline", "list"), ("data", "export"),
}
AZ_READ_LAST = re.compile(r"^(list|show|list-\S+|show-\S+|query|get-instance-view|version)$")


def emit(decision: str | None, reason: str = "") -> None:
    if decision is None:
        sys.exit(0)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason}}, ensure_ascii=False))
    sys.exit(0)


def _norm_path(p: str) -> str:
    p = p.replace("\\", "/").strip().lower()
    p = re.sub(r"^/([a-z])/", r"\1:/", p)  # /c/Users -> c:/users (Git Bash)
    return re.sub(r"/+", "/", p)


def _ppcall_path() -> str:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(C.PLUGIN_ROOT)
    return _norm_path(root.rstrip("/\\") + "/scripts/ppcall")


def _mentions_ppcall(segment: str) -> bool:
    text = re.sub(r"/+", "/", segment.replace("\\", "/").lower())
    text = re.sub(r"(?<![\w.:])/([a-z])/", r"\1:/", text)
    return _ppcall_path() in text


def _is_shell(token: str) -> bool:
    return re.sub(r"\.exe$", "", Path(token.replace("\\", "/")).name.lower()) in ("bash", "sh")


def is_ppcall(tokens: list[str]) -> bool:
    """Tylko oryginalny ppcall z katalogu pluginu, nie plik o tej samej nazwie gdzie indziej."""
    if not tokens:
        return False
    first = tokens[1] if _is_shell(tokens[0]) and len(tokens) > 1 else tokens[0]
    return _norm_path(first) == _ppcall_path()


def split_segments(cmd: str, dialect: str = "bash") -> list[str]:
    """Dzieli polecenie na ;, &&, ||, |, & i nowe linie, ale nie wewnatrz cudzyslowow.

    W PowerShell znakiem ucieczki jest backtick, a backslash jest zwyklym znakiem.
    """
    escape = "`" if dialect == "powershell" else "\\"
    segs, buf, quote, i = [], [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            elif ch == escape and quote == '"' and i + 1 < len(cmd):
                buf.append(cmd[i + 1]); i += 1
        elif ch == "`" and dialect == "powershell" and i + 1 < len(cmd):
            buf.append(ch); buf.append(cmd[i + 1]); i += 1
        elif ch in ("'", '"'):
            quote = ch; buf.append(ch)
        elif ch in ";|&\n":
            segs.append("".join(buf)); buf = []
            if i + 1 < len(cmd) and cmd[i + 1] == ch and ch in "|&":
                i += 1
        else:
            buf.append(ch)
        i += 1
    segs.append("".join(buf))
    return [s for s in segs if s.strip()]


def ppcall_args(tokens: list[str]) -> list[str]:
    return tokens[2:] if _is_shell(tokens[0]) else tokens[1:]


def _tokenize_powershell(segment: str) -> list[str]:
    tokens, buf, quote, started, i = [], [], None, False, 0
    while i < len(segment):
        ch, nxt = segment[i], segment[i + 1:i + 2]
        if quote == "'":
            if ch == "'" and nxt == "'":
                buf.append("'"); i += 1
            elif ch == "'":
                quote = None
            else:
                buf.append(ch)
        elif quote == '"':
            if ch == "`" and nxt:
                buf.append(nxt); i += 1
            elif ch == '"' and nxt == '"':
                buf.append('"'); i += 1
            elif ch == '"':
                quote = None
            else:
                buf.append(ch)
        elif ch == "`" and nxt:
            buf.append(nxt); started = True; i += 1
        elif ch in ("'", '"'):
            quote, started = ch, True
        elif ch.isspace():
            if started:
                tokens.append("".join(buf)); buf, started = [], False
        else:
            buf.append(ch); started = True
        i += 1
    if started:
        tokens.append("".join(buf))
    return tokens


def tokenize(segment: str, dialect: str = "bash") -> list[str]:
    if dialect == "powershell":
        return _tokenize_powershell(segment)
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def out_targets(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args[:-1]) if a == "--out"]


def classify_segment(seg: str, cwd: str, dialect: str = "bash", active: bool = True,
                     full: str | None = None) -> tuple[str | None, str]:
    """Zwraca (decyzja, powod). None = brak decyzji (zwykle uprawnienia Claude Code)."""
    s = seg.strip()
    if not s:
        return None, ""
    tokens = tokenize(s, dialect)
    if not is_ppcall(tokens) and _mentions_ppcall(s):
        return "deny", ('ppcall uruchamiaj bezposrednio: "${CLAUDE_PLUGIN_ROOT}/scripts/ppcall" <komenda>, '
                        "bez zmiennych, eval, bash -c ani python ppcall.py.")
    if is_ppcall(tokens):
        args = ppcall_args(tokens)
        sub = args[0] if args else ""
        if sub == "exec":
            if len(args) != 3 or not args[2].isdigit():
                return "deny", "Uzycie: ppcall exec <ID> <nr-kroku>"
            ok, reason, step = C.can_execute(cwd, args[1], int(args[2]))
            if not ok:
                return "deny", reason
            extra = ""
            if step.get("typ") == "flow-patch":
                values = " | ".join(f"{C.flow_patch_label(c['sciezka'])}: bylo {C.value_text(c['bylo'])!r}, "
                                    f"ma byc {C.value_text(c['ma_byc'])!r}" for c in step["zmiany"])
                extra = (f" Wartosci: {values}. ppcall zapisze kopie, a po zapisie porowna definicje i przy "
                         "kazdej innej roznicy przywroci kopie.")
            if step.get("typ") == "flow-definition":
                extra = (" Nowa definicja flow z pliku zgloszenia (moze dodawac, usuwac i przestawiac akcje). "
                         "ppcall porowna ja z aktualna, zapisze kopie, a po zapisie przy kazdej roznicy albo "
                         "zmianie stanu flow przywroci kopie. Zmiany akcji pokazal ppcall plan-check.")
            return "ask", (f"ZMIANA W SRODOWISKU. Zgloszenie {args[1]}, krok {args[2]}: {step['opis']} | "
                           f"{C.step_summary(step)}.{extra} Zatwierdz tylko jesli to dokladnie zaakceptowany krok.")
        if sub in ("get", "runs"):
            for target in out_targets(args):
                problem = C.out_path_problem(cwd, target)
                if problem:
                    return "deny", problem
                decision, reason = guard_write(str(Path(cwd) / target), cwd, active)
                if decision:
                    return decision, reason
        if sub in {"get", "runs", "verify", "dostepy", "rejestr", "plan-new", "plan-check", "status", "revoke",
                   "config", "konfiguracja", ""}:
            return "allow", "pp-support: operacja tylko do odczytu lub na lokalnych plikach zgloszenia"
        return "deny", f"Nieznana komenda ppcall: {sub}"
    if touches_state(s, cwd, full):
        return "deny", "Stan akceptacji pp-support jest dostepny tylko dla ppcall i hookow."
    if touches_backup(s, cwd):
        return "deny", ("Kopie definicji flow w backup/ czyta i zapisuje wylacznie ppcall (moga zawierac hasla). "
                        "Do wycofania sluzy krok flow-definition z plikiem kopii.")
    if not active:
        return None, ""
    if TOKEN_RE.search(s):
        return "deny", ("Pobieranie tokenow i logowanie wykonuje czlowiek albo ppcall. "
                        "Do odczytu uzyj: ppcall get <url>.")
    if C.CLOUD_HOST_RE.search(s):
        return "deny", ("Ruch do uslug Microsoft tylko przez ppcall (odczyt: ppcall get <url>; "
                        "zmiana: krok planu i ppcall exec).")
    if PS_MUTATION_RE.search(s):
        return "deny", "Polecenia zmieniajace zasoby Azure/Power Platform tylko jako krok zatwierdzonego planu."
    if not tokens:
        return None, ""
    prog = Path(tokens[0].replace("\\", "/")).name.lower()
    prog = re.sub(r"\.(exe|cmd|bat)$", "", prog)
    if prog == "pac":
        words = tuple(t.lower() for t in tokens[1:] if not t.startswith("-"))
        if any(words[:len(r)] == r for r in PAC_READ) or not words or "--version" in tokens:
            return "allow", "pp-support: pac w trybie odczytu"
        return "deny", ("pac: dozwolony tylko odczyt. Zmiane opisz w planie naprawy jako krok typu cli "
                        "i wykonaj przez ppcall exec po akceptacji pracownika.")
    if prog == "az":
        words = []
        for t in tokens[1:]:
            if t.startswith("-"):
                break
            words.append(t.lower())
        if words[:1] == ["rest"]:
            return "deny", "az rest: uzyj ppcall get (odczyt) albo kroku planu (zmiana)."
        if words[:2] == ["keyvault", "secret"] and words[-1:] != ["list"]:
            return "deny", "Odczyt wartosci sekretow jest zabroniony."
        if "--version" in tokens or (words and AZ_READ_LAST.match(words[-1])):
            return "allow", "pp-support: az w trybie odczytu"
        return "deny", ("az: dozwolony tylko odczyt (list/show). Zmiane opisz w planie naprawy "
                        "i wykonaj przez ppcall exec po akceptacji pracownika.")
    return None, ""


RANK = {"deny": 3, "ask": 2, None: 1, "allow": 0}


def guard_command(cmd: str, cwd: str, dialect: str = "bash", active: bool = True) -> tuple[str | None, str]:
    segments = split_segments(cmd, dialect)
    decisions = [classify_segment(seg, cwd, dialect, active, cmd) for seg in segments] or [(None, "")]
    if len(segments) > 1 and any(d == "ask" for d, _ in decisions):
        return "deny", "ppcall exec uruchamiaj jako osobne polecenie, bez laczenia z innymi."
    worst = max(decisions, key=lambda d: RANK[d[0]])
    if worst[0] == "allow" and any(d is None for d, _ in decisions):
        return None, ""  # czesc polecenia poza naszym zakresem: zwykle uprawnienia
    return worst


def _in_backup(path: str, cwd: str | None) -> bool:
    if not path:
        return False
    p = Path(path).expanduser()
    try:
        p = p.resolve()
        parts = p.relative_to(C.incidents_root(cwd or os.getcwd()).expanduser().resolve()).parts
    except (ValueError, OSError):
        parts = ()
    return bool(re.search(r"/zgloszenia/[^/]+/backup(/|$)", str(p).replace("\\", "/"), re.I)
                or (len(parts) >= 2 and parts[1].rstrip(". ").lower() == C.BACKUP_DIR_NAME))


def guard_write(path: str, cwd: str | None = None, active: bool = True) -> tuple[str | None, str]:
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except Exception:
        pass
    text = str(p).replace("\\", "/")
    try:
        in_state = p.is_relative_to(C.state_base().expanduser().resolve())
    except OSError:
        in_state = True
    if STATE_RE.search(text) or in_state:
        return "deny", "Stan akceptacji pp-support jest chroniony."
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(C.PLUGIN_ROOT)
    try:
        if p.is_relative_to(Path(root).resolve()):
            return "deny", "Kod pluginu pp-support jest chroniony przed modyfikacja w trakcie pracy."
    except Exception:
        pass
    if active and re.search(r"/\.claude/settings(\.local)?\.json$", text):
        return "deny", "Zmiana ustawien Claude Code w trakcie obslugi zgloszenia jest zablokowana."
    parts: tuple = ()
    try:
        parts = p.relative_to(C.incidents_root(cwd or os.getcwd()).expanduser().resolve()).parts
    except (ValueError, OSError):
        pass
    if (re.search(r"/zgloszenia/[^/]+/wykonanie\.jsonl$", text)
            or (len(parts) == 2 and parts[1].rstrip(". ").lower() == "wykonanie.jsonl")):
        return "deny", "Dziennik wykonania prowadzi wylacznie ppcall."
    if (re.search(r"/zgloszenia/[^/]+/weryfikacja\.jsonl$", text)
            or (len(parts) == 2 and parts[1].rstrip(". ").lower() == C.VERIFY_LOG)):
        return "deny", "Wyniki weryfikacji zapisuje wylacznie ppcall verify."
    if (re.search(r"/zgloszenia/[^/]+/backup(/|$)", text, re.I)
            or (len(parts) >= 2 and parts[1].rstrip(". ").lower() == C.BACKUP_DIR_NAME)):
        return "deny", "Kopie definicji flow w backup/ zapisuje wylacznie ppcall exec."
    return None, ""


MCP_SCOPE_RE = re.compile(r"dataverse|power_?platform|powerapps|power_?automate|fabric|sharepoint|azure|"
                          r"m365|microsoft|graph|dynamics", re.I)
MCP_WRITE_RE = re.compile(r"create|update|delete|upsert|insert|set|patch|post|put|send|run|trigger|start|"
                          r"stop|enable|disable|deploy|import|publish|remove|write|execute|invoke", re.I)


# Przegladarka (Claude in Chrome, przegladarka w aplikacji): lista narzedzi i akcji tylko do odczytu
# przechodzi bez pytan, wgrywanie plikow jest zablokowane, wszystko inne (takze nieznane) za zgoda.
BROWSER_TOOL_RE = re.compile(r"^mcp__(claude-in-chrome|Claude_Browser)__")
BROWSER_READ_TOOLS = {"read_page", "get_page_text", "find", "tabs_context", "tabs_context_mcp", "tabs_create",
                      "tabs_create_mcp", "tabs_close", "tabs_close_mcp", "tabs_select", "resize_window",
                      "shortcuts_list", "list_connected_browsers", "select_browser", "switch_browser",
                      "preview_list", "preview_logs", "read_console_messages", "read_network_requests"}
BROWSER_READ_ACTIONS = {"screenshot", "scroll", "scroll_to", "hover", "mouse_move", "wait", "zoom",
                        "cursor_position"}
BROWSER_DENY_TOOLS = {"file_upload", "upload_image"}


def _browser_ask(what: str) -> tuple[str, str]:
    return "ask", f"Przegladarka: {what}. Moze zmienic dane w aplikacji. Zatwierdz tylko, jesli rozumiesz skutek."


def browser_decision(name: str, tin) -> tuple[str | None, str]:
    name = name.split("__")[-1]
    if not isinstance(tin, dict):
        return "ask", "Przegladarka: nierozpoznane wejscie narzedzia."
    if name in BROWSER_DENY_TOOLS:
        return "deny", "Wgrywanie plikow przez przegladarke jest zablokowane."
    if name == "navigate":
        url = str(tin.get("url", "")).strip().lower()
        if url in ("back", "forward") or re.match(r"(https?://|[a-z0-9.-]+\.[a-z]{2,}(/|$|:))", url):
            return None, ""
        return "deny", "Przegladarka: dozwolone sa tylko adresy http(s), bez javascript:, data: i file:."
    if name in BROWSER_READ_TOOLS:
        return None, ""
    if name == "computer":
        if tin.get("action") in BROWSER_READ_ACTIONS:
            return None, ""
        return _browser_ask(str(tin.get("action_summary") or tin.get("action") or "nieznana akcja"))
    if name == "browser_batch":
        actions = tin.get("actions")
        if not isinstance(actions, list) or not actions:
            return "ask", "Przegladarka: nierozpoznana lista akcji."
        results = []
        for a in actions:
            if not isinstance(a, dict) or not isinstance(a.get("name"), str):
                return "ask", "Przegladarka: nierozpoznana akcja w serii."
            results.append(browser_decision(a["name"], a.get("input") if "input" in a else {}))
        return max(results, key=lambda d: RANK[d[0]])
    return _browser_ask(str(tin.get("action_summary") or name))


def activation_trigger(tool: str, tin: dict) -> str | None:
    """Poczatek pracy dyzurnej w rozmowie: skill pluginu albo pierwsze uzycie ppcall."""
    if tool == "Skill" and str(tin.get("skill", "")).lower().startswith("pp-support:"):
        return f"skill {tin.get('skill')}"
    if tool in ("Bash", "PowerShell"):
        cmd = str(tin.get("command", ""))
        if _mentions_ppcall(cmd) or re.search(r"(^|[\s/\\\"'])ppcall(\.py)?([\s\"';&|)]|$)", cmd):
            return "ppcall"
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tool = data.get("tool_name", "")
        tin = data.get("tool_input", {}) or {}
        cwd = data.get("cwd") or os.getcwd()
        if tool == "Read" and "backup" not in str(tin.get("file_path") or "").lower():
            emit(None)  # odczyt plikow poza kopiami: bez dostepu do stanu, zeby nie spowalniac innych projektow
        session = data.get("session_id")
        active = C.session_active(session, cwd)
        if not active:
            trigger = activation_trigger(tool, tin)
            if trigger:
                C.activate_session(session, trigger)
                active = True
        if tool in ("Bash", "PowerShell"):
            dialect = "powershell" if tool == "PowerShell" else "bash"
            decision, reason = guard_command(str(tin.get("command", "")), cwd, dialect, active)
        elif tool == "Read" and _in_backup(str(tin.get("file_path") or ""), cwd):
            decision, reason = "deny", ("Kopie definicji flow moga zawierac hasla. Do wycofania sluzy krok odwrotny "
                                        "z plan-check, nie odczyt kopii.")
        elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            decision, reason = guard_write(str(tin.get("file_path") or tin.get("notebook_path") or ""), cwd, active)
        elif not active:
            decision, reason = None, ""
        elif BROWSER_TOOL_RE.match(tool):
            decision, reason = browser_decision(tool.split("__")[-1], tin)
        elif tool.startswith("mcp__") and MCP_SCOPE_RE.search(tool):
            action = tool.split("__")[-1]
            if MCP_WRITE_RE.search(action):
                decision, reason = "deny", ("Zapis przez MCP do uslug Microsoft jest wylaczony. "
                                            "Zmiana tylko jako krok planu wykonany przez ppcall exec.")
            else:
                decision, reason = None, ""
        else:
            decision, reason = None, ""
        if decision in ("deny", "ask"):
            C.audit({"typ": "straznik", "narzedzie": tool, "decyzja": decision, "powod": reason,
                     "wejscie": str(tin.get("command") or tin.get("file_path") or "")[:500]})
        emit(decision, reason)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        emit("deny", f"pp-support: blad straznika ({e}), operacja zablokowana zapobiegawczo.")


if __name__ == "__main__":
    main()

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
    r"[\\/](akceptacje|sesje|kopie)([\\/*?\[]|\b)|\b(akceptacje|sesje|kopie)[\\/*?\[]", re.I)
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


# Polecenia przechodzace drzewo katalogow (przeszukiwanie, archiwizacja, kopiowanie rekurencyjne).
RECURSIVE_PROGS = {"rg", "ag", "ack", "find", "fd", "tar", "zip", "7z", "7za", "robocopy", "xcopy", "rsync",
                   "du", "tree", "compress-archive"}
# Program -> wzorzec opcji wlaczajacej rekurencje (dla tych programow tylko z ta opcja).
RECURSIVE_FLAGS = {
    **dict.fromkeys(("grep", "egrep", "fgrep", "zgrep"),
                    re.compile(r"^(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|--dereference-recursive)$")),
    **dict.fromkeys(("cp", "scp"), re.compile(r"^(-[a-zA-Z]*[rRa][a-zA-Z]*|--recursive|--archive)$")),
    **dict.fromkeys(("chmod", "chown"), re.compile(r"^(-[a-zA-Z]*R[a-zA-Z]*|--recursive)$")),
    # ls i dir w PowerShell to Get-ChildItem (-r = -Recurse); w Git Bash ls -R, w cmd dir /s
    **dict.fromkeys(("ls", "dir", "gci", "get-childitem", "copy-item", "cpi", "copy", "select-string", "sls"),
                    re.compile(r"^(-[a-zA-Z]*R[a-zA-Z]*|-r(e(c(u(r(s(e)?)?)?)?)?)?(:\$?true)?|/s)$", re.I)),
    "findstr": re.compile(r"^/s$", re.I),
}
HOME_VAR_RE = re.compile(r"^(~|\$\{?HOME\}?|\$\{?env:(HOME|USERPROFILE)\}?|%USERPROFILE%|\$USERPROFILE|"
                         r"\$\{?env:HOMEDRIVE\}?\$\{?env:HOMEPATH\}?|%HOMEDRIVE%%HOMEPATH%)(?=/|$)", re.I)
LS_BASH_FLAG_RE = re.compile(r"^(-[a-zA-Z]*R[a-zA-Z]*|--recursive)$")  # w bashu ls -r to odwrocona kolejnosc
# Nakladki uruchamiajace inne polecenie: command grep -r ..., timeout 10 find ~, cmd /c dir /s ...
WRAPPERS = {"command", "builtin", "env", "timeout", "nice", "nohup", "sudo", "xargs", "stdbuf", "time", "exec",
            "ionice", "chrt", "doas", "busybox", "winpty", "wsl"}
CD_PROGS = {"cd", "pushd", "chdir", "set-location", "sl", "push-location"}
SHELL_PROGS = {"bash", "sh", "zsh", "dash", "ksh", "pwsh", "powershell"}
SHELL_CODE_FLAGS = {"-c"}


def _prog(token: str) -> str:
    return re.sub(r"\.(exe|cmd|bat)$", "", Path(token.replace("\\", "/")).name.lower())


def _unwrap(tokens: list[str]) -> tuple[list[str], bool, bool]:
    """Polecenie spod nakladek (command, env, timeout, xargs, cmd /c ...).
    Zwraca (tokeny, czy byl xargs, czy polecenie wykona cmd.exe)."""
    xargs = cmd = False
    while tokens:
        prog = _prog(tokens[0])
        if prog in ("cmd",) and len(tokens) > 2 and tokens[1].lower() in ("/c", "/k"):
            tokens, cmd = tokens[2:], True
            if len(tokens) == 1:  # cmd /c "dir /s ..."
                tokens = tokens[0].split()
            continue
        if prog not in WRAPPERS:
            break
        xargs = xargs or prog == "xargs"
        rest = tokens[1:]
        while rest and (rest[0].startswith("-") or "=" in rest[0] or re.fullmatch(r"[\d.]+[smhd]?", rest[0])):
            rest = rest[1:]
        tokens = rest
    return tokens, xargs, cmd


def _is_recursive(tokens: list[str], dialect: str = "bash") -> bool:
    if not tokens:
        return False
    prog = _prog(tokens[0])
    args = [t.lower() for t in tokens[1:]]
    if prog in RECURSIVE_PROGS or any(a.startswith(("-recurse", "-depth")) for a in args):
        return True
    if prog in ("grep", "egrep", "fgrep", "zgrep") and (
            "--directories=recurse" in args or "-drecurse" in args
            or any(a in ("-d", "--directories") and nxt == "recurse" for a, nxt in zip(args, args[1:]))):
        return True
    flag = LS_BASH_FLAG_RE if dialect == "bash" and prog in ("ls", "dir") else RECURSIVE_FLAGS.get(prog)
    return bool(flag) and any(flag.match(t) for t in tokens[1:])


def _path_candidates(tokens: list[str]) -> list[str]:
    """Argumenty mogace byc sciezka: bez opcji, a z opcji -Nazwa:wartosc i --nazwa=wartosc sama wartosc."""
    out = []
    for t in tokens:
        if not t:
            continue
        if t.startswith("-"):
            m = re.match(r"^-[^:=]+[:=](.+)$", t)
            if m:
                out.append(m.group(1))
            continue
        out.append(t)
    return out


def cd_targets(cmd: str, dialect: str = "bash") -> list[str]:
    """Katalogi, do ktorych polecenie przechodzi (cd, pushd, Set-Location); samo cd to dom."""
    targets = []
    for seg in split_segments(cmd, dialect):
        toks = tokenize(seg.strip(), dialect)
        if toks and _prog(toks[0]) in CD_PROGS:
            cand = _path_candidates(toks[1:])
            targets.append(cand[0] if cand else "~")
    return targets


def _covers_state(path: str, cwd: str) -> bool:
    """Sciezka (z ~, $HOME, wzorcem) jest katalogiem stanu, jego przodkiem albo lezy w stanie."""
    p = HOME_VAR_RE.sub(lambda _: str(Path.home()).replace("\\", "/"), path.replace("\\", "/"))
    parts = p.split("/")
    for i, part in enumerate(parts):
        if re.search(r"[*?\[]", part):  # wzorzec: liczy sie staly poczatek sciezki
            parts = parts[:i]
            break
    p = "/".join(parts)
    norm = _norm_path(p)
    m = re.fullmatch(r"/([a-z])|([a-z]):", norm)  # /c (Git Bash) albo C: to caly dysk
    if m:
        norm = f"{m.group(1) or m.group(2)}:/"
    if not re.match(r"^([a-z]:)?/", norm):
        norm = _norm_path(os.path.join(cwd, p))
    norm = _norm_path(os.path.normpath(norm)).rstrip("/") or "/"
    return any(state == norm or state.startswith(norm.rstrip("/") + "/") or norm.startswith(state + "/")
               for state in _state_forms())


def _state_forms() -> set[str]:
    """Sciezka stanu tak, jak podana, i po rozwinieciu (nazwy 8.3 jak RUNNER~1, dowiazania)."""
    base = C.state_base()
    forms = {_norm_path(str(base)).rstrip("/")}
    try:
        forms.add(_norm_path(str(Path(base).expanduser().resolve())).rstrip("/"))
    except (OSError, ValueError):
        pass
    return forms


PS_VALUE_OPTIONS = ("-executionpolicy", "-windowstyle", "-workingdirectory", "-outputformat", "-inputformat",
                    "-version", "-configurationname", "-configurationfile", "-psconsolefile", "-settingsfile",
                    "-custompipename")
PS_VALUE_ALIASES = {"-ep", "-ex", "-wd", "-of", "-if", "-w", "-v", "-o"}
PS_FLAG_OPTIONS = ("-noprofile", "-nologo", "-noninteractive", "-noexit", "-sta", "-mta", "-interactive",
                   "-login", "-noprofileloadtime", "-help")
PS_FLAG_ALIASES = {"-nop", "-noni", "-nol", "-l", "-?", "/?", "-h"}


def _ps_option_name(arg: str) -> str:
    """Pelna nazwa opcji -WorkingDirectory dla jej skrotow (-wd, -work, -WorkingDir:...)."""
    name = arg.lower().split(":", 1)[0]
    if name == "-wd" or (len(name) >= 3 and "-workingdirectory".startswith(name) and name != "-w"):
        return "-workingdirectory"
    return name


def _ps_option(arg: str) -> str | None:
    """Rodzaj opcji pwsh/powershell (skroty jak w PowerShell): 'value', 'flag', 'file' albo None (nieznana)."""
    name = arg.lower().split(":", 1)[0].replace("/", "-", 1) if arg.startswith("/") else arg.lower().split(":", 1)[0]
    if name in ("-f", "-file") or (len(name) >= 3 and "-file".startswith(name)):
        return "file"
    if name in PS_VALUE_ALIASES or (len(name) >= 3 and any(o.startswith(name) for o in PS_VALUE_OPTIONS)):
        return "value"
    if name in PS_FLAG_ALIASES or (len(name) >= 3 and any(o.startswith(name) for o in PS_FLAG_OPTIONS)):
        return "flag"
    return None


def recursive_reaches_state(tokens: list[str], cwd: str, dialect: str = "bash", full: str | None = None) -> bool:
    """Rekurencyjne polecenie, ktore obejmuje katalog stanu (dom, .claude, dysk) albo startuje z takiego cwd.
    full: cale polecenie; cd/Set-Location w innym segmencie zmienia cwd, xargs bierze sciezki z innego segmentu."""
    tokens, xargs, cmd = _unwrap(tokens)
    if tokens and _prog(tokens[0]) in SHELL_PROGS:  # bash -c "...", pwsh -Command "..."
        args = [t.lower() for t in tokens[1:]]
        ps = _prog(tokens[0]) in ("pwsh", "powershell")
        if ps and any(a == "-ec" or (len(a) >= 2 and "-encodedcommand".startswith(a)) for a in args):
            return True  # kodu zakodowanego nie da sie sprawdzic: w rozmowie dyzurnej odmowa
        inner_dialect = "powershell" if ps else "bash"
        def code_flag(a: str) -> bool:
            if ps:
                return a in ("/c", "/command", "--command", "-cwa") or (len(a) >= 2 and (
                    "-command".startswith(a) or "-commandwithargs".startswith(a)))
            return bool(re.fullmatch(r"-[a-z]*c", a))

        # Argumenty po kolei: opcje, potem flaga kodu (-c/-Command) albo pierwszy argument pozycyjny.
        start, wd, i = None, None, 0
        while i < len(args):
            a = args[i]
            if code_flag(a):
                if i + 1 >= len(args) or args[i + 1] == "-":
                    return True  # kod ze stdin albo brak kodu: nie da sie sprawdzic
                start = i + 2
                break
            if not a.startswith(("-", "/")) or a == "-":
                if a == "-":
                    return True  # skrypt ze stdin
                start = i + 1  # powershell 5.1: reszta to polecenie; bash: skrypt (sprawdzany jak polecenie)
                break
            if ps:
                kind = _ps_option(a)
                if kind == "file":
                    value = a.split(":", 1)[1] if ":" in a else (args[i + 1] if i + 1 < len(args) else "-")
                    return value == "-"  # -File skrypt.ps1 to skrypt; -File - czyta kod ze stdin
                if kind is None:
                    return True  # nieznana opcja: nie zgadujemy, gdzie zaczyna sie polecenie
                if kind == "value":
                    value = a.split(":", 1)[1] if ":" in a else (tokens[i + 2] if i + 2 < len(tokens) else "")
                    if _ps_option_name(a) in ("-workingdirectory", "-wd"):
                        wd = value
                    i += 1 if ":" in a else 2
                    continue
            elif re.fullmatch(r"-[a-z]*s[a-z]*", a):
                return True  # bash -s: kod ze stdin
            elif a in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                i += 2
                continue
            i += 1
        if start is None:
            # sama powloka: kod ze stdin, gdy polecenie podaje go potokiem albo przekierowaniem
            return bool(full and re.search(r"[|<]", full))
        if start >= len(tokens):
            return True
        if wd:
            cwd = os.path.join(cwd, HOME_VAR_RE.sub(lambda _: str(Path.home()).replace("\\", "/"),
                                                   wd.replace("\\", "/")))
        inner = " ".join(tokens[start:])
        return any(recursive_reaches_state(tokenize(seg.strip(), inner_dialect), cwd, inner_dialect, inner)
                   for seg in split_segments(inner, inner_dialect))
    if not _is_recursive(tokens, "cmd" if cmd else dialect):
        return False
    if _covers_state(".", cwd):
        return True
    whole = full or ""
    if any(_covers_state(t, cwd) for t in cd_targets(whole, dialect)):
        return True
    candidates = _path_candidates(tokens[1:])
    if xargs:
        for seg in split_segments(whole, dialect):
            candidates += _path_candidates(tokenize(seg.strip(), dialect)[1:])
    return any(_covers_state(t, cwd) for t in candidates)


def tool_path_in_state(path: str, cwd: str) -> bool:
    """Read albo Grep: plik lub katalog w stanie, katalog stanu albo jego przodek (dom, .claude, dysk)."""
    if not path:
        return False
    try:
        p = str((Path(cwd) / Path(path).expanduser()).resolve())
    except (OSError, ValueError):
        return True
    return bool(STATE_RE.search(p)) or _covers_state(p, cwd)


def grep_reaches_legacy_backup(path: str, cwd: str) -> bool:
    """Grep obejmujacy kopie sprzed 1.2.2 w zgloszenia/<ID>/backup/ (dopoki ppcall ich nie przeniesie)."""
    try:
        target = (Path(cwd) / Path(path or ".").expanduser()).resolve()
        root = C.incidents_root(cwd).expanduser().resolve()
        legacy = [d.resolve() for d in root.glob(f"*/{C.BACKUP_DIR_NAME}") if d.is_dir()] if root.is_dir() else []
    except (OSError, ValueError):
        return True
    return any(d == target or d.is_relative_to(target) or target.is_relative_to(d) for d in legacy)


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
                source = ("kopii definicji ze stanu pluginu (wycofanie)"
                          if str(step.get("plik", "")).replace("\\", "/").lower().startswith(C.BACKUP_DIR_NAME + "/")
                          else "pliku zgloszenia")
                extra = (f" Nowa definicja flow z {source} (moze dodawac, usuwac i przestawiac akcje). "
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
    if recursive_reaches_state(tokens, cwd, dialect, full):
        return "deny", ("Przeszukiwanie, kopiowanie i archiwizacja calego katalogu domowego albo .claude sa "
                        "zablokowane w rozmowie dyzurnej (obejmuja stan pp-support i kopie definicji flow). "
                        "Wskaz konkretny katalog.")
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


def deny_logged(tool: str, path: str, reason: str) -> None:
    try:
        C.audit({"typ": "straznik", "narzedzie": tool, "decyzja": "deny", "powod": reason, "wejscie": path[:500]})
    finally:
        emit("deny", reason)


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tool = data.get("tool_name", "")
        tin = data.get("tool_input", {}) or {}
        cwd = data.get("cwd") or os.getcwd()
        if tool == "Read":
            path = str(tin.get("file_path") or "")
            if tool_path_in_state(path, cwd):
                deny_logged(tool, path, "Stan pp-support (akceptacje, kopie definicji flow) czyta wylacznie ppcall. "
                            "Kopie moga zawierac hasla; do wycofania sluzy krok flow-definition z plikiem kopii.")
            if "backup" not in path.lower():
                emit(None)  # zwykly odczyt: bez dostepu do stanu, zeby nie spowalniac innych projektow
        if tool == "Grep":
            path = str(tin.get("path") or "")
            if tool_path_in_state(path or cwd, cwd) or grep_reaches_legacy_backup(path, cwd):
                deny_logged(tool, path or cwd, "Grep obejmuje stan pp-support albo kopie definicji flow (moga "
                            "zawierac hasla). Wskaz konkretny katalog projektu albo zgloszenia.")
            emit(None)
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

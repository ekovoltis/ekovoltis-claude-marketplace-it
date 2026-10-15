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


def is_ppcall(tokens: list[str]) -> bool:
    """Tylko oryginalny ppcall z katalogu pluginu, nie plik o tej samej nazwie gdzie indziej."""
    if not tokens:
        return False
    first = tokens[1] if tokens[0] in ("bash", "sh") and len(tokens) > 1 else tokens[0]
    return _norm_path(first) == _ppcall_path()


def split_segments(cmd: str) -> list[str]:
    """Dzieli polecenie na ;, &&, ||, |, & i nowe linie, ale nie wewnatrz cudzyslowow."""
    segs, buf, quote, i = [], [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < len(cmd):
                buf.append(cmd[i + 1]); i += 1
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
    return tokens[2:] if tokens[0] in ("bash", "sh") else tokens[1:]



def tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def classify_segment(seg: str, cwd: str) -> tuple[str | None, str]:
    """Zwraca (decyzja, powod). None = brak decyzji (zwykle uprawnienia Claude Code)."""
    s = seg.strip()
    if not s:
        return None, ""
    tokens = tokenize(s)
    if is_ppcall(tokens):
        args = ppcall_args(tokens)
        sub = args[0] if args else ""
        if sub == "exec":
            if len(args) != 3 or not args[2].isdigit():
                return "deny", "Uzycie: ppcall exec <ID> <nr-kroku>"
            ok, reason, step = C.can_execute(cwd, args[1], int(args[2]))
            if not ok:
                return "deny", reason
            return "ask", (f"ZMIANA W SRODOWISKU. Zgloszenie {args[1]}, krok {args[2]}: {step['opis']} | "
                           f"{C.step_summary(step)}. Zatwierdz tylko jesli to dokladnie zaakceptowany krok.")
        if sub in {"get", "dostepy", "rejestr", "plan-new", "plan-check", "status", "revoke", "config", ""}:
            return "allow", "pp-support: operacja tylko do odczytu lub na lokalnych plikach zgloszenia"
        return "deny", f"Nieznana komenda ppcall: {sub}"
    if STATE_RE.search(s):
        return "deny", "Stan akceptacji pp-support jest dostepny tylko dla ppcall i hookow."
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


def guard_command(cmd: str, cwd: str) -> tuple[str | None, str]:
    segments = split_segments(cmd)
    decisions = [classify_segment(seg, cwd) for seg in segments] or [(None, "")]
    if len(segments) > 1 and any(d == "ask" for d, _ in decisions):
        return "deny", "ppcall exec uruchamiaj jako osobne polecenie, bez laczenia z innymi."
    worst = max(decisions, key=lambda d: RANK[d[0]])
    if worst[0] == "allow" and any(d is None for d, _ in decisions):
        return None, ""  # czesc polecenia poza naszym zakresem: zwykle uprawnienia
    return worst


def guard_write(path: str) -> tuple[str | None, str]:
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except Exception:
        pass
    text = str(p).replace("\\", "/")
    if STATE_RE.search(text):
        return "deny", "Stan akceptacji pp-support jest chroniony."
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(C.PLUGIN_ROOT)
    try:
        if p.is_relative_to(Path(root).resolve()):
            return "deny", "Kod pluginu pp-support jest chroniony przed modyfikacja w trakcie pracy."
    except Exception:
        pass
    if re.search(r"/\.claude/settings(\.local)?\.json$", text):
        return "deny", "Zmiana ustawien Claude Code w trakcie obslugi zgloszenia jest zablokowana."
    if re.search(r"/zgloszenia/[^/]+/wykonanie\.jsonl$", text):
        return "deny", "Dziennik wykonania prowadzi wylacznie ppcall."
    return None, ""


MCP_SCOPE_RE = re.compile(r"dataverse|power_?platform|powerapps|power_?automate|fabric|sharepoint|azure|"
                          r"m365|microsoft|graph|dynamics", re.I)
MCP_WRITE_RE = re.compile(r"create|update|delete|upsert|insert|set|patch|post|put|send|run|trigger|start|"
                          r"stop|enable|disable|deploy|import|publish|remove|write|execute|invoke", re.I)


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tool = data.get("tool_name", "")
        tin = data.get("tool_input", {}) or {}
        cwd = data.get("cwd") or os.getcwd()
        if tool in ("Bash", "PowerShell"):
            decision, reason = guard_command(str(tin.get("command", "")), cwd)
        elif tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            decision, reason = guard_write(str(tin.get("file_path") or tin.get("notebook_path") or ""))
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

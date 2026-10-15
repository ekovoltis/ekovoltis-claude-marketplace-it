"""Testy harnessu pp-support. Bez dostepu do chmury: HTTP jest podmieniony.

Uruchomienie:  python3 tests/test_harness.py
"""
from __future__ import annotations

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
TMP = Path(tempfile.mkdtemp(prefix="pp-support-test-"))
os.environ["PP_SUPPORT_STATE_DIR"] = str(TMP / "state")
os.environ["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
WORK = TMP / "work"
WORK.mkdir()
PPCALL = str(SCRIPTS / "ppcall")
Q = shlex.quote(PPCALL)  # tak jak wpisze to model: sciezka w cudzyslowie
WIN = PPCALL.replace("/", "\\")  # zapis z backslashami, jak w PowerShell na Windows
# subprocess na Windows szuka "bash" najpierw w System32 (WSL), dlatego jawna sciezka
BASH = os.environ.get("PP_TEST_BASH") or shutil.which("bash") or "bash"
DV = "https://ekovoltis-test.crm4.dynamics.com"
FAILS: list[str] = []


def hook(name: str, payload: dict) -> dict | None:
    r = subprocess.run([BASH, str(SCRIPTS / "run-py"), name], input=json.dumps(payload),
                       capture_output=True, text=True, env=os.environ)
    assert r.returncode in (0, 2), r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def decision(cmd: str, tool: str = "Bash") -> str | None:
    key = "command" if tool in ("Bash", "PowerShell") else "file_path"
    out = hook("hook_guard", {"tool_name": tool, "tool_input": {key: cmd}, "cwd": str(WORK)})
    return out["hookSpecificOutput"]["permissionDecision"] if out else None


def expect(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'OK ' if ok else 'ZLE'} {label}: {got}" + ("" if ok else f" (oczekiwano {want})"))
    if not ok:
        FAILS.append(label)


def ppcall(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, PPCALL, *args], cwd=WORK, capture_output=True, text=True, env=os.environ)


def prompt(text: str) -> dict | None:
    return hook("hook_prompt", {"prompt": text, "cwd": str(WORK)})


print("1. Straznik: odczyt i blokady")
expect("ppcall get", decision(f'{Q} get "{DV}/api/data/v9.2/WhoAmI"'), "allow")
expect("ppcall get z & w cudzyslowie", decision(f'{Q} get "{DV}/api/data/v9.2/x?$top=5&$select=name"'), "allow")
expect("ppcall get | head", decision(f'{Q} get "{DV}/api/data/v9.2/WhoAmI" | head -5'), None)
expect("podrobiony ppcall", decision(f'./scripts/ppcall get "{DV}/api/data/v9.2/WhoAmI"'), "deny")
expect("curl do Dataverse", decision(f'curl -H "Authorization: x" {DV}/api/data/v9.2/accounts'), "deny")
expect("python z hostem", decision('python -c "import requests; requests.get(\'https://graph.microsoft.com/v1.0/me\')"'), "deny")
expect("az get-access-token", decision("az account get-access-token --resource https://x"), "deny")
expect("az rest", decision("az rest --method get --url https://example.org"), "deny")
expect("az list", decision("az resource list -g rg-automaty -o table"), "allow")
expect("az secret list", decision("az keyvault secret list --vault-name v"), "allow")
expect("az logicapp show", decision("az logic workflow show -g rg -n wf"), "allow")
expect("az webapp restart", decision("az webapp restart -n x -g y"), "deny")
expect("az keyvault secret show", decision("az keyvault secret show --vault-name v -n s"), "deny")
expect("pac solution list", decision("pac solution list"), "allow")
expect("pac solution import", decision("pac solution import --path x.zip"), "deny")
expect("pac org who", decision("pac.exe org who"), "allow")
expect("PowerShell Set-AdminFlow", decision("Set-AdminFlowOwnerRole -EnvironmentName x", "PowerShell"), "deny")
expect("Invoke-RestMethod", decision("Invoke-RestMethod https://api.flow.microsoft.com/x", "PowerShell"), "deny")
expect("dostep do stanu", decision("cat ~/.claude/pp-support-state/akceptacje/INC-1.json"), "deny")
expect("zwykle ls", decision("ls -la"), None)
expect("zapis do stanu", decision(str(Path.home() / ".claude/pp-support-state/akceptacje/INC-1.json"), "Write"), "deny")
expect("zapis do pluginu", decision(str(SCRIPTS / "common.py"), "Edit"), "deny")
expect("zapis do planu", decision(str(WORK / "zgloszenia/INC-1/plan.md"), "Write"), None)
mcp = hook("hook_guard", {"tool_name": "mcp__dataverse__update_record", "tool_input": {}, "cwd": str(WORK)})
expect("MCP Dataverse update", mcp["hookSpecificOutput"]["permissionDecision"] if mcp else None, "deny")
mcp = hook("hook_guard", {"tool_name": "mcp__dataverse__read_query", "tool_input": {}, "cwd": str(WORK)})
expect("MCP Dataverse read", mcp, None)
mcp = hook("hook_guard", {"tool_name": "mcp__Atlassian__createJiraIssue", "tool_input": {}, "cwd": str(WORK)})
expect("MCP Atlassian (poza zakresem)", mcp, None)

print("2. Plan i walidacja")
expect("plan-new", ppcall("plan-new", "INC-1").returncode, 0)
expect("pusty szablon nie przechodzi", ppcall("plan-check", "INC-1").returncode, 2)
expect("exec bez planu gotowego", decision(f"{Q} exec INC-1 1"), "deny")

PLAN = f"""# Plan naprawy: zgłoszenie INC-1

## Automatyzacja
007 AKDW, Automatyczne komunikaty do wytwórców, produkcja, developer Bus Przemysław.

## Objaw
Wytwórcy nie dostali wczoraj komunikatów SMS o cenach poniżej limitu, zgłosił Trading.

## Przyczyna
Flow został automatycznie wyłączony przez platformę po 14 dniach błędów połączenia z bramką SMS.

## Dowody
FAKT: flowruns pokazuje 14 nieudanych przebiegów z błędem 401 od 2026-10-01, stan flow Suspended.

## Proponowana naprawa
Połączenie zostało już odnowione przez człowieka, włączamy flow ponownie i sprawdzamy następny przebieg.

## Ryzyko i wpływ
Po włączeniu flow wyśle komunikaty tylko dla jutrzejszych cen, zaległych nie nadrobi automatycznie.

## Plan wycofania
Wyłączenie flow tym samym API (turnOff), stan wraca do obecnego bez utraty danych.

## Weryfikacja po naprawie
Odczyt stanu flow zwraca Started, a najbliższy przebieg o 14:00 kończy się statusem Succeeded.

## Kroki
```json
[
  {{"nr": 1, "opis": "Włączenie flow AKDW", "typ": "http", "metoda": "POST",
   "url": "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/env1/flows/abc/start?api-version=2016-11-01"}}
]
```
"""
plan_file = WORK / "zgloszenia/INC-1/plan.md"
plan_file.write_text(PLAN, encoding="utf-8")
r = ppcall("plan-check", "INC-1")
expect("poprawny plan", r.returncode, 0)
h = [l for l in r.stdout.splitlines() if "Skrot planu" in l][0].split()[-1]

bad = PLAN.replace('"metoda": "POST"', '"metoda": "DELETE"')
plan_file.write_text(bad, encoding="utf-8")
expect("DELETE odrzucony", ppcall("plan-check", "INC-1").returncode, 2)
bad = PLAN.replace("https://api.flow.microsoft.com", "https://evil.example.com")
plan_file.write_text(bad, encoding="utf-8")
expect("obcy host odrzucony", ppcall("plan-check", "INC-1").returncode, 2)
bad = PLAN.replace('"typ": "http", "metoda": "POST",\n   "url": "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/env1/flows/abc/start?api-version=2016-11-01"',
                  '"typ": "cli", "argv": ["pac", "solution", "delete", "--solution-name", "x"]')
plan_file.write_text(bad, encoding="utf-8")
expect("pac delete odrzucony", ppcall("plan-check", "INC-1").returncode, 2)
plan_file.write_text(PLAN, encoding="utf-8")

print("3. Akceptacja")
expect("exec bez akceptacji", decision(f"{Q} exec INC-1 1"), "deny")
expect("ppcall exec bez akceptacji (sam skrypt)", ppcall("exec", "INC-1", "1").returncode, 3)
own_cause = "Platforma sama zawiesiła flow, bo przez dwa tygodnie bramka SMS odrzucała logowanie."
own_fix = "Skoro połączenie już działa, tylko włączamy flow z powrotem, nic więcej nie ruszamy."
r = prompt(f"AKCEPTUJĘ INC-1 deadbeef00\nPrzyczyna: {own_cause}\nNaprawa: {own_fix}")
expect("zly skrot", r and r.get("decision"), "block")
r = prompt(f"AKCEPTUJĘ INC-1 {h}\nPrzyczyna: flow nie dziala\nNaprawa: wlaczyc")
expect("za krotkie uzasadnienie", r and r.get("decision"), "block")
r = prompt(f"AKCEPTUJĘ INC-1 {h}\nPrzyczyna: Flow został automatycznie wyłączony przez platformę po 14 dniach błędów połączenia\nNaprawa: {own_fix}")
expect("przepisane z planu", r and r.get("decision"), "block")
r = prompt("Pomóż mi z flow AKDW, nie działa")
expect("zwykla wiadomosc bez wplywu", r, None)
r = prompt(f"AKCEPTUJE INC-1 {h}\nPrzyczyna: {own_cause}\nNaprawa: {own_fix}")
expect("poprawna akceptacja (bez polskich znakow)", "additionalContext" in json.dumps(r or {}), True)
expect("exec po akceptacji -> pytanie", decision(f"{Q} exec INC-1 1"), "ask")
expect("exec z PowerShell (backslashe) -> pytanie", decision(f'bash "{WIN}" exec INC-1 1', "PowerShell"), "ask")
expect("exec przez /usr/bin/bash -> pytanie", decision(f"/usr/bin/bash {Q} exec INC-1 1"), "ask")
expect("exec laczony z innym poleceniem", decision(f"{Q} exec INC-1 1 && echo ok"), "deny")
expect("exec kroku 2 (nie istnieje)", decision(f"{Q} exec INC-1 2"), "deny")

print("4. Zmiana planu po akceptacji")
plan_file.write_text(PLAN.replace("14:00", "15:00"), encoding="utf-8")
expect("exec po zmianie planu", decision(f"{Q} exec INC-1 1"), "deny")
expect("exec z PowerShell po zmianie planu", decision(f'bash "{WIN}" exec INC-1 1', "PowerShell"), "deny")
plan_file.write_text(PLAN, encoding="utf-8")
expect("exec po przywroceniu planu", decision(f"{Q} exec INC-1 1"), "ask")

print("5. Wykonanie (HTTP podmieniony)")
sys.path.insert(0, str(SCRIPTS))
import ppcall as P  # noqa: E402
calls = []
P.http = lambda m, u, b=None, timeout=90: (calls.append((m, u)) or (200, b'{"ok":true}'))
os.chdir(WORK)
try:
    P.cmd_exec(["INC-1", "1"])
except SystemExit as e:
    expect("exec zakonczony", e.code, None)
expect("wyslane zadanie", calls[0][0] if calls else None, "POST")
expect("ponowny exec tego samego kroku", decision(f"{Q} exec INC-1 1"), "deny")
log = (WORK / "zgloszenia/INC-1/wykonanie.jsonl").read_text(encoding="utf-8")
expect("dziennik wykonania", '"sukces": true' in log, True)

print("6. Odwolanie")
r = prompt("ODWOŁUJĘ INC-1")
expect("odwolanie", "odwolal" in json.dumps(r or {}, ensure_ascii=False), True)
expect("brak akceptacji po odwolaniu", (TMP / "state/akceptacje/INC-1.json").exists(), False)

print("7. Brak Pythona -> blokada")
env = {"PATH": "/nonexistent", "HOME": str(Path.home())}
if os.environ.get("SYSTEMROOT"):
    env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
r = subprocess.run([BASH, str(SCRIPTS / "run-py"), "hook_guard"], input="{}", capture_output=True, text=True, env=env)
expect("kod wyjscia przy braku Pythona", r.returncode, 2)

print("8. ppcall get --out")
stan = TMP / "state/akceptacje/INC-9.json"
stan.write_text('{"nie": "ruszac"}', encoding="utf-8")
get = f'{Q} get "{DV}/api/data/v9.2/WhoAmI" --out '
expect("--out w katalogu zgloszenia", decision(get + "zgloszenia/INC-1/odp.json"), "allow")
expect("--out w podkatalogu zgloszenia", decision(get + "zgloszenia/INC-1/dane/odp.json"), "allow")
for label, target in [
    ("--out plan.md", "zgloszenia/INC-1/plan.md"),
    ("--out PLAN.md. (Windows)", "zgloszenia/INC-1/PLAN.md."),
    ("--out wykonanie.jsonl", "zgloszenia/INC-1/wykonanie.jsonl"),
    ("--out stan akceptacji", shlex.quote(str(stan))),
    ("--out ~/.claude/pp-support-state", "~/.claude/pp-support-state/akceptacje/INC-9.json"),
    ("--out kod pluginu", shlex.quote(str(SCRIPTS / "common.py"))),
    ("--out .claude/settings.json", ".claude/settings.json"),
    ("--out .claude w zgloszeniu", "zgloszenia/INC-1/.claude/settings.json"),
    ("--out poza zgloszeniem", "odp.json"),
    ("--out ucieczka przez ..", "zgloszenia/INC-1/../../odp.json"),
    ("--out sam katalog zgloszenia", "zgloszenia/INC-1"),
    ("--out zly identyfikator", "zgloszenia/x/odp.json"),
    ("--out strumien NTFS", "zgloszenia/INC-1/odp.json:s"),
    ("--out podwojny, drugi zly", "zgloszenia/INC-1/a.json --out zgloszenia/INC-1/plan.md"),
]:
    expect(label, decision(get + target), "deny")
link = WORK / "zgloszenia/INC-1/link"
try:
    link.symlink_to(TMP / "state", target_is_directory=True)
except OSError:
    print("  -- dowiazanie symboliczne niedostepne (brak uprawnien), pominiete")
else:
    expect("--out przez dowiazanie do stanu", decision(get + "zgloszenia/INC-1/link/akceptacje/INC-9.json"), "deny")

calls.clear()
before = plan_file.read_text(encoding="utf-8")
for label, target in [("ppcall: --out plan.md", "zgloszenia/INC-1/plan.md"),
                      ("ppcall: --out stan akceptacji", str(stan)),
                      ("ppcall: --out kod pluginu", str(SCRIPTS / "common.py")),
                      ("ppcall: --out poza zgloszeniem", "../odp.json")]:
    try:
        P.cmd_get([f"{DV}/api/data/v9.2/WhoAmI", "--out", target])
        code = 0
    except SystemExit as e:
        code = e.code
    expect(label, code, 3)
expect("ppcall: brak zapytania HTTP przy odmowie", calls, [])
expect("ppcall: plan nienaruszony", plan_file.read_text(encoding="utf-8") == before, True)
expect("ppcall: stan nienaruszony", stan.read_text(encoding="utf-8"), '{"nie": "ruszac"}')
try:
    P.cmd_get([f"{DV}/api/data/v9.2/WhoAmI", "--out", "zgloszenia/INC-1/odp.json"])
except SystemExit as e:
    expect("ppcall: poprawny --out", e.code, None)
expect("ppcall: zapis do zgloszenia", (WORK / "zgloszenia/INC-1/odp.json").read_bytes(), b'{"ok":true}')

print("9. Rozpoznawanie ppcall (Windows, PowerShell, obejscia)")
import hook_guard as G  # noqa: E402
expect("PowerShell: backslash przed cudzyslowem nie jest ucieczka",
       G.split_segments('echo "a\\"; ls', "powershell"), ['echo "a\\"', " ls"])
expect("PowerShell: tokeny", G.tokenize(r'''bash "C:\a b\ppcall" `"x\ 'it''s' ''', "powershell"),
       ["bash", r"C:\a b\ppcall", '"x\\', "it's"])
expect("PowerShell get", decision(f'bash "{WIN}" get "{DV}/api/data/v9.2/WhoAmI"', "PowerShell"), "allow")
expect("PowerShell exec bez akceptacji", decision(f'bash "{WIN}" exec INC-1 1', "PowerShell"), "deny")
if " " not in WIN:
    expect("PowerShell: sciezka bez cudzyslowu", decision(f"bash {WIN} exec INC-1 1", "PowerShell"), "deny")
expect("PowerShell bash.exe",decision(f'bash.exe "{WIN}" exec INC-1 1', "PowerShell"), "deny")
expect("PowerShell: trik z \\\"", decision(f'echo "a\\"; bash "{WIN}" exec INC-1 1', "PowerShell"), "deny")
expect("Bash: sciezka z backslashami w apostrofach", decision(f"'{WIN}' exec INC-1 1"), "deny")
if len(PPCALL) > 2 and PPCALL[1] == ":":
    gitbash = "/" + PPCALL[0].lower() + PPCALL[2:].replace("\\", "/")
    expect("Bash: sciezka /c/... (Git Bash)", decision(f"{shlex.quote(gitbash)} status INC-1"), "allow")
expect("bash -c z ppcall", decision(f'bash -c "{Q} exec INC-1 1"'), "deny")
expect("python ppcall.py", decision(f"python {shlex.quote(str(SCRIPTS / 'ppcall.py'))} exec INC-1 1"), "deny")
expect("ppcall przez zmienna", decision(f"P={Q}; $P exec INC-1 1"), "deny")

print("10. Awaria hooka -> blokada (fail closed)")
broken = TMP / "zepsuty"
shutil.copytree(SCRIPTS, broken)
(broken / "common.py").write_text('raise RuntimeError("zepsuty import")\n', encoding="utf-8")


def broken_hook(name: str, payload: dict) -> int:
    return subprocess.run([BASH, str(broken / "run-py"), name], input=json.dumps(payload),
                          capture_output=True, text=True, env=os.environ).returncode


expect("PreToolUse przy wyjatku importu", broken_hook("hook_guard", {"tool_name": "Bash", "tool_input": {"command": "ls"}}), 2)
expect("akceptacja przy awarii", broken_hook("hook_prompt", {"prompt": f"AKCEPTUJĘ INC-1 {h}"}), 2)
expect("odwolanie przy awarii", broken_hook("hook_prompt", {"prompt": "ODWOŁUJĘ INC-1"}), 2)
expect("zwykla wiadomosc przy awarii", broken_hook("hook_prompt", {"prompt": "Pomoz z flow"}), 0)

print()
print("WSZYSTKO OK" if not FAILS else f"NIEUDANE: {FAILS}")
sys.exit(1 if FAILS else 0)

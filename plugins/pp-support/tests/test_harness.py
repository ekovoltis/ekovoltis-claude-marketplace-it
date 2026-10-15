"""Testy harnessu pp-support. Bez dostepu do chmury: HTTP jest podmieniony.

Uruchomienie:  python3 tests/test_harness.py
"""
from __future__ import annotations

import io
import json
import os
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
DV = "https://ekovoltis-test.crm4.dynamics.com"
FAILS: list[str] = []


def hook(name: str, payload: dict) -> dict | None:
    r = subprocess.run(["bash", str(SCRIPTS / "run-py"), name], input=json.dumps(payload),
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
    return subprocess.run(["bash", PPCALL, *args], cwd=WORK, capture_output=True, text=True, env=os.environ)


def prompt(text: str) -> dict | None:
    return hook("hook_prompt", {"prompt": text, "cwd": str(WORK)})


print("1. Straznik: odczyt i blokady")
expect("ppcall get", decision(f'{PPCALL} get "{DV}/api/data/v9.2/WhoAmI"'), "allow")
expect("ppcall get z & w cudzyslowie", decision(f'{PPCALL} get "{DV}/api/data/v9.2/x?$top=5&$select=name"'), "allow")
expect("ppcall get | head", decision(f'{PPCALL} get "{DV}/api/data/v9.2/WhoAmI" | head -5'), None)
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
expect("exec bez planu gotowego", decision(f"{PPCALL} exec INC-1 1"), "deny")

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
expect("exec bez akceptacji", decision(f"{PPCALL} exec INC-1 1"), "deny")
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
expect("exec po akceptacji -> pytanie", decision(f"{PPCALL} exec INC-1 1"), "ask")
expect("exec laczony z innym poleceniem", decision(f"{PPCALL} exec INC-1 1 && echo ok"), "deny")
expect("exec kroku 2 (nie istnieje)", decision(f"{PPCALL} exec INC-1 2"), "deny")

print("4. Zmiana planu po akceptacji")
plan_file.write_text(PLAN.replace("14:00", "15:00"), encoding="utf-8")
expect("exec po zmianie planu", decision(f"{PPCALL} exec INC-1 1"), "deny")
plan_file.write_text(PLAN, encoding="utf-8")
expect("exec po przywroceniu planu", decision(f"{PPCALL} exec INC-1 1"), "ask")

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
expect("ponowny exec tego samego kroku", decision(f"{PPCALL} exec INC-1 1"), "deny")
log = (WORK / "zgloszenia/INC-1/wykonanie.jsonl").read_text(encoding="utf-8")
expect("dziennik wykonania", '"sukces": true' in log, True)

print("6. Odwolanie")
r = prompt("ODWOŁUJĘ INC-1")
expect("odwolanie", "odwolal" in json.dumps(r or {}, ensure_ascii=False), True)
expect("brak akceptacji po odwolaniu", (TMP / "state/akceptacje/INC-1.json").exists(), False)

print("7. Brak Pythona -> blokada")
r = subprocess.run(["/bin/bash", str(SCRIPTS / "run-py"), "hook_guard"], input="{}", capture_output=True, text=True,
                   env={"PATH": "/nonexistent", "HOME": os.environ["HOME"]})
expect("kod wyjscia przy braku Pythona", r.returncode, 2)

print()
print("WSZYSTKO OK" if not FAILS else f"NIEUDANE: {FAILS}")
sys.exit(1 if FAILS else 0)

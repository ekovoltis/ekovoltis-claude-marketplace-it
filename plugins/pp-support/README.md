# pp-support 1.2.1

Plugin Claude Code wspierający dyżurnego IT w obsłudze zgłoszeń dotyczących automatyzacji Power Platform w Ekovoltis. Claude diagnozuje odczytem, przygotowuje z pracownikiem plan naprawy i wykonuje go wyłącznie po jawnej akceptacji człowieka.

Wersja 0.1 to fundament: harness, test dostępów i migawka rejestru. Dokumentacja automatyzacji, nad którą pracują programiści, zostanie podłączona przez ustawienie `docs_dir`, gdy będzie gotowa. Plugin jej nie edytuje.

## Zawartość

```
pp-support/
├── .claude-plugin/plugin.json      manifest i ustawienia (userConfig)
├── hooks/hooks.json                SessionStart, UserPromptSubmit, PreToolUse, PostToolUse
├── scripts/
│   ├── ppcall, ppcall.py           jedyna droga do chmury Microsoft (odczyt, plan, wykonanie, test dostępów)
│   ├── common.py                   wspólna logika: walidacja planu, skróty, akceptacje, audyt
│   ├── hook_guard.py               strażnik poleceń i zapisów
│   ├── hook_prompt.py              rejestracja akceptacji i odwołań z wiadomości człowieka
│   ├── hook_audit.py               dziennik audytowy
│   ├── hook_session_start.py       konfiguracja i kontrola wymagań
│   └── run-py                      uruchamianie hooków, blokada przy braku Pythona i awarii hooka
├── skills/
│   ├── zgloszenie/                 przebieg obsługi zgłoszenia i szablon planu
│   └── sprawdz-dostepy/            test i interpretacja dostępów
├── rejestr/automatyzacje-snapshot.json   migawka rejestru z 2026-10-15 (71 pozycji)
└── tests/test_harness.py           testy harnessu bez dostępu do chmury
```

## Jak działa akceptacja

1. Claude diagnozuje wyłącznie odczytami (`ppcall get`) i zapisuje dowody w `zgloszenia/<ID>/notatki.md`.
2. Claude wyjaśnia pracownikowi przyczynę i możliwe naprawy, zadaje pytania sprawdzające zrozumienie.
3. Claude pisze `zgloszenia/<ID>/plan.md`: objaw, przyczyna, dowody, naprawa, ryzyko, wycofanie, weryfikacja i kroki w postaci maszynowej. `ppcall plan-check` waliduje plan i liczy skrót.
4. Pracownik wpisuje w oknie rozmowy:
   ```
   AKCEPTUJĘ <ID> <skrót>
   Przyczyna: <własnymi słowami>
   Naprawa: <własnymi słowami>
   ```
   Hook `UserPromptSubmit` widzi tylko tekst wpisany przez człowieka, więc model nie ma jak wytworzyć akceptacji. Hook odrzuca zły skrót, zbyt krótkie uzasadnienie i zdania przepisane z planu.
5. Claude porównuje opis pracownika z diagnozą. Przy rozbieżności nie wykonuje.
6. Każdy krok to osobne `ppcall exec <ID> <nr>`. Hook sprawdza akceptację, zgodność skrótu, ważność czasową i kolejność, a Claude Code dodatkowo pyta pracownika o zgodę na ten konkretny krok. `ppcall` sprawdza wszystko jeszcze raz przed wysłaniem.
7. Nieudany krok zatrzymuje cały plan. Zmiana planu po akceptacji zmienia skrót i unieważnia akceptację. `ODWOŁUJĘ <ID>` cofa akceptację w każdej chwili.

## Co blokuje strażnik

| Blokowane | Zamiast tego |
|---|---|
| `curl`, `Invoke-RestMethod`, skrypty z adresami usług Microsoft | `ppcall get` |
| `az account get-access-token`, `az login`, `Connect-*`, MSAL | logowanie robi człowiek, tokeny pobiera `ppcall` w swoim procesie |
| `az rest`, zmieniające `az` i `pac`, PowerShell `Set-/New-/Remove-*` dla Az, Power Apps, Flow, PnP, Graph | krok planu i `ppcall exec` |
| odczyt wartości sekretów Key Vault | brak, poza zakresem pluginu |
| narzędzia MCP do Dataverse, Power Platform, Fabric, SharePoint, Azure z operacją zapisu | krok planu |
| zapis do stanu akceptacji, kodu pluginu, ustawień Claude Code, dziennika wykonania | brak |
| zapis i odczyt kopii definicji flow oraz całego stanu `~/.claude/pp-support-state/` (narzędzia Read, Grep, Write, Edit i polecenia powłoki); Grep obejmujący dom albo `.claude`; w rozmowie dyżurnej rekurencyjne przeszukiwanie, kopiowanie i archiwizacja domu albo `.claude` (`grep -r`, `rg`, `find`, `tar`, `Get-ChildItem -Recurse` itp.) | kopie robi i czyta wyłącznie `ppcall`; przeszukuj konkretny katalog |
| w planie: krok `http` zmieniający `workflows` inaczej niż włączenie albo wyłączenie flow, zapis w Power Automate API inny niż `start`, `stop`, `run`, `resubmit`, `cancel`, `$batch`, operacje na rozwiązaniach, `cli` z argumentami ze znakami cmd.exe (`&`, `|`, `%`, cudzysłowy), `cli` z `az` innym niż `logic workflow enable`/`disable`, `webapp restart`, `functionapp restart` (lista `common.AZ_PLAN_COMMANDS`), `cli` z `import`, `upgrade`, `publish`, `deploy`, argumenty z pliku (`@plik`) | zmiana definicji krokami `flow-patch` i `flow-definition`; wdrożenia rozwiązań robi człowiek |
| w planie: DELETE, argumenty delete/remove/purge/secret, zmiana definicji flow poza krokami `flow-patch` i `flow-definition`, hosty spoza listy | usuwanie zasobów, zmiany wyzwalaczy i połączeń robi człowiek |

Dozwolone hosty dla tokenów: Graph, ARM, Power Automate, BAP, Power Platform API, Fabric, środowiska Dataverse (`*.crm*.dynamics.com`), SharePoint (`*.sharepoint.com`). Na inne adresy `ppcall` nie wyśle tokenu. Wyjątek bez tokenu: treść wejść i wyjść akcji z historii przebiegu (`inputsLink`/`outputsLink` na `*.environment.api.powerplatformusercontent.com`), którą `ppcall get` pobiera tylko metodą GET, bo podpis SAS jest w adresie. Do dziennika audytu adres trafia bez podpisu.

## Kiedy plugin działa

Plugin jest włączony na stałe, ale strażnik pilnuje tylko rozmów dyżurnych. W innych rozmowach (np. praca nad kodem w innym projekcie) nie blokuje `curl`, `az`, zapisu ustawień ani przeglądarki. Rozmowa staje się dyżurna i zostaje taka do końca, gdy:

- uruchomisz skill pluginu (`pp-support:...`),
- Claude pierwszy raz użyje `ppcall`,
- rozmowa jest otwarta w folderze z ustawienia `support_dirs`,
- napiszesz akceptację albo odwołanie w ich formacie (`AKCEPTUJĘ <ID> ...`, `ODWOŁUJĘ <ID>`) albo zaczniesz wiadomość od słowa „dyżur”.

Wyjście z folderu dyżurnego w trakcie rozmowy nie wyłącza pilnowania. Znaczniki rozmów są usuwane po 30 dniach bez użycia. Hook strażnika uruchamia się też przy każdym odczycie pliku i przy Grep, ale poza stanem pluginu (i przejściowo kopiami w `backup/`) kończy od razu.

Zawsze, także poza rozmową dyżurną, działają: akceptacja i wykonanie (`ppcall exec` sprawdza akceptację sam), ochrona stanu akceptacji, dziennika wykonania, wyników weryfikacji i kopii definicji. Bez identyfikatora rozmowy strażnik pilnuje zawsze.

## Salesbook

- **API:** `ppcall get` czyta z API Salesbooka (tylko GET), gdy ustawione są `salesbook_api_url` i `salesbook_auth_flow`. Dane logowania Basic `ppcall` pobiera w swoim procesie z akcji HTTP flow integracji (`<adres Dataverse>|<workflowid>|<nazwa akcji>`), nie wypisuje ich i wysyła tylko pod adres API, bez przekierowań. Pliki dokumentów z `*.salesbook-ent.com` pobiera bez danych logowania. `curl` do Salesbooka blokuje strażnik.
- **Aplikacja:** Claude czyta ją w przeglądarce pracownika, który loguje się sam.
- **Przeglądarka (Claude in Chrome i przeglądarka w aplikacji):** narzędzia i akcje odczytu (tekst strony, zrzut ekranu, przewijanie, karty) bez pytań; każde inne działanie, także nowe narzędzie, za zgodą człowieka; wgrywanie plików zablokowane; adresy tylko http(s). Dotyczy każdej strony i każdej sesji, bo plugin jest włączony na stałe, więc przy testowaniu aplikacji w przeglądarce każde kliknięcie wymaga zgody.
- **Hasła w odpowiedziach** (dane logowania w definicji flow, nagłówki `Authorization`, klucze w adresach, pola z hasłem i tokenem) `ppcall get` i `ppcall runs` zastępują `***`, na ekranie i w plikach. Kopie definicji są pełne, dlatego leżą w chronionym stanie (`~/.claude/pp-support-state/kopie/<ID>/`), a strażnik blokuje ich odczyt.

## Zmiany w definicji flow (`flow-patch`, `flow-definition`)

Decyzja Michała Kaszty z 2026-11-04 (ITS-420): w supporcie automatów naprawy w definicji flow nie idą ścieżką deweloperską. Zdiagnozowaną naprawę w definicji flow dyżurny robi razem z Claude, z akceptacją, kopią i wycofaniem. Oba kroki są domyślnie wyłączone i działają tylko dla flow niezarządzanych.

### `flow-patch`: wartości w istniejących akcjach

Włącza go ustawienie `flow_patch_dataverse_urls`, osobno dla każdego środowiska.

- Zmienić można istniejącą wartość tekstową pod `inputs/parameters` albo `inputs/body` istniejącej akcji, a jako dowolny JSON: całe `inputs` akcji Compose, wartość zmiennej (`InitializeVariable`, `SetVariable`), `inputs/select` (Select), `inputs/where` i `inputs/from` (Filter array), `inputs/columns` (Table), `expression` (warunek, Until), `foreach`, `inputs/runStatus` i `inputs/runError` (Terminate) oraz `runAfter` dowolnej akcji. Najwyżej 5 zmian w jednym flow na krok. Typ akcji sprawdzany jest na aktualnej definicji, wartości JSON porównywane bez względu na kolejność kluczy. `runAfter` może wskazywać tylko akcje z tego samego zakresu, bez cykli. Wyzwalacze, `type`, połączenia i klucze przekierowujące wywołanie (`uri`, `method`, `headers`, `host`, `authentication`, `path`, `pathTemplate`, `function` i podobne) są odrzucane.
- Pytanie Claude Code przed wykonaniem pokazuje wartości „było” i „ma być”.
- Plan musi mieć sekcje „Analiza wpływu” i „Test”.
- `plan-check` sprawdza na aktualnej definicji `modifiedon`, ścieżki i wartości `bylo`, wypisuje różnice i krok wycofania.
- `exec` sprawdza to samo ponownie, zapisuje kopię w stanie (`kopie/<ID>/`), wysyła `PATCH workflows(<id>)` z `If-Match`, a potem porównuje definicję z kopią. Różnica pod inną ścieżką przywraca kopię i kończy krok błędem.
- Wycofanie to nowy plan z krokiem odwrotnym i nową akceptacją.

### `flow-definition`: dodanie, usunięcie, przestawienie akcji

Włącza go osobne ustawienie `flow_definition_dataverse_urls`.

- Krok wskazuje plik `zgloszenia/<ID>/flow-<workflowid>-nowa.json` z pełną nową definicją i jego skrót SHA-256. Skrót jest w planie, więc zmiana pliku po akceptacji blokuje wykonanie (sprawdza hook i `ppcall`).
- `plan-check` porównuje plik z aktualną definicją i wypisuje dodane, usunięte, zmienione i przeniesione akcje (nazwa, typ, zakres).
- Dodawać i zmieniać można tylko akcje wbudowane: Compose, warunek, Switch, Scope, pętle, zmienne, Parse JSON, Filter array, Select, Table, Join, Terminate, Delay i Expression.
- Odrzucane:
  - każda zmiana poza akcjami (wyzwalacze, `connectionReferences`, parametry, `$connections`);
  - więcej niż 10 akcji na krok;
  - zmiana typu akcji;
  - dodanie lub zmiana akcji innego typu (HTTP, konektor, webhook, podrzędny flow, funkcja, kod);
  - każda zmiana kluczy adresu, metody, nagłówków, uwierzytelnienia i połączenia;
  - zmiana, dodanie lub usunięcie akcji albo zmiennej, z której akcja wysyłająca bierze adres, nagłówki albo uwierzytelnienie;
  - wyrażenia `actions(...)` i pole `authentication`;
  - tekst `***` w zmienianych akcjach;
  - `runAfter` poza zakresem albo z cyklem;
  - powtórzone nazwy akcji i klucze różniące się tylko wielkością liter.
- `ppcall get` maskuje hasła, więc pliku nowej definicji nie da się zbudować dla flow z hasłem w akcji, którą trzeba zmienić. Taki flow poprawia się krokiem `flow-patch` albo przez developera.
- `plan-check` pokazuje dodane, usunięte, zmienione i przeniesione akcje razem z treścią dodanych i zmienionych akcji.
- `exec`: kopia w stanie, `kopie/<ID>/` (jej skrót trafia do rejestru kopii), `PATCH` z `If-Match`, odczyt po zapisie. Gdy zapisana definicja różni się od pliku albo zmienił się stan flow, `ppcall` przywraca kopię.
- Wycofanie to nowy plan z krokiem `flow-definition` wskazującym kopię (`kopia_plan`, czyli `backup/<plik>`, i `kopia_sha256` z `wykonanie.jsonl`). `backup/<plik>` to odnośnik: `ppcall` i strażnik szukają kopii w stanie, najpierw w tym zgłoszeniu, potem po jednoznacznej nazwie w innych zgłoszeniach, więc wycofanie może być w nowym zgłoszeniu. Kopia z rejestru kopii może przywrócić usunięte akcje HTTP i konektorów, bo wraca do stanu sprzed zmiany; plik spoza rejestru podlega pełnym ograniczeniom.

### Korekty danych Dataverse

Korekta lookupu (np. `eko_meteringpoint@odata.bind`) to zwykły krok `http` PATCH na rekordzie. Wartość sprzed zmiany odczytuje się w diagnozie i opisuje w planie wycofania. Wzorzec jest w skillu `zgloszenie`.

Zapis `clientdata` włączonego flow nie wymaga wyłączania: flow zostaje włączony, a Power Automate od razu pokazuje nową definicję (sprawdzone na produkcji 02.11.2026 na trzech flow).

## SharePoint: tylko odczyt przez Graph, na koncie Azure CLI

`ppcall` czyta SharePoint na koncie, na którym zalogowano Azure CLI w konfiguracji dyżuru (test dostępów pokazuje je w wierszu „konto ppcall”). To konto, a nie konto z przeglądarki, musi mieć odczyt w witrynie (stan z 04.11.2026: e2vitservice bez dostępu do BazadokumentowEkovoltis, ITS-418).

- Graph: witryna działa. Biblioteki, pliki (`/sites/<id witryny>/drive/root:/<folder>:/children`; id witryny z `/sites/<host>:/sites/<witryna>`, bo ścieżki pliku nie da się dopisać do adresu witryny podanego ścieżką) i wersje (`/drive/items/<id>/versions`) działają, gdy konto ppcall ma dostęp i token Azure CLI obejmuje zawartość witryny. Do sprawdzenia po nadaniu dostępu.
- SharePoint REST (`/_api`): token aplikacji Azure CLI jest odrzucany (401 `invalid_request`). REST będzie dostępny po wdrożeniu własnej aplikacji Entra (wniosek z 16.10.2026).
- Przy 403 z Graph i 401 z REST `ppcall get` dopisuje wyjaśnienie z nazwą konta zamiast samego kodu błędu.

## Ograniczenia, które trzeba znać

Hooki chronią przed pomyłką i dryfem modelu, a nie przed świadomym obejściem. Rozbudowane polecenie powłoki może przejść przez filtr tekstowy. Rzeczywistą granicą bezpieczeństwa jest tożsamość:

- Na co dzień dyżurny pracuje kontem z rolami tylko do odczytu (Reader w Azure, odczyt w środowiskach Power Platform, role odczytu w Fabric).
- Uprawnienia do zmian nadaje PIM na czas zgłoszenia, z uzasadnieniem odwołującym się do ID zgłoszenia.
- Wtedy nawet obejście hooka nie da możliwości zmiany bez aktywacji roli przez człowieka.

Przy obowiązkach KSC/NIS2 ten układ daje rozliczalność: dziennik `~/.claude/pp-support-state/audyt.jsonl`, `zgloszenia/<ID>/wykonanie.jsonl` z treścią akceptacji pracownika oraz logi Entra ID i Power Platform z tożsamością człowieka.

Zalecane uzupełnienie w managed settings Claude Code (do weryfikacji z bieżącą dokumentacją ustawień):

```json
{
  "permissions": {
    "deny": [
      "Read(~/.claude/pp-support-state/**)",
      "Edit(~/.claude/pp-support-state/**)"
    ],
    "disableBypassPermissionsMode": "disable"
  }
}
```

Katalog `zgloszenia/` może zawierać dane klientów odczytane z Dataverse, a stan pluginu (`kopie/`) pełne definicje flow. Kopie sprzed 1.2.2 w `zgloszenia/<ID>/backup/` (wszystkich zgłoszeń) `ppcall plan-check` i `ppcall exec` przenoszą do stanu przy pierwszym uruchomieniu. Trzymaj go lokalnie albo w miejscu z kontrolą dostępu, nie w ogólnodostępnym repozytorium.

## Wymagania na stanowisku

- Claude Code (na Windows z Git Bash)
- Python 3.9 lub nowszy (bez dodatkowych pakietów)
- Azure CLI, zalogowane przez pracownika: `az login`
- Power Platform CLI: `dotnet tool install --global Microsoft.PowerApps.CLI.Tool`, profil przez `pac auth create`

## Instalacja testowa

```bash
claude --plugin-dir ./pp-support
```

Plugin ma `defaultEnabled: false`, bo jego hooki działają w całej sesji. Po instalacji z marketplace włącz go świadomie w sesjach dyżurnych: `claude plugin enable pp-support`, i uzupełnij ustawienia w `/plugin` (adresy Dataverse, ID środowisk, host i witryna SharePoint, obszary Fabric).

Instalacja w zakresie `user` daje plugin w każdym folderze. Włączony plugin uruchamia strażnika w każdej sesji, także przy zwykłej pracy z kodem: blokuje ruch do usług Microsoft z pominięciem `ppcall` (`curl`, `az rest`), zmieniające `az` i `pac` oraz zapis `.claude/settings*.json`. Git, npm i dotnet działają normalnie. Ustawienie `incidents_dir` wskazuje stały katalog zgłoszeń, niezależny od folderu sesji. Puste oznacza `zgloszenia/` w folderze sesji.

## Przygotowanie stanowiska dyżurnego na Windows

1. Osobny folder dyżurny poza OneDrive i poza repozytoriami, np. `C:\Users\<login>\pp-dyzur`. Trafiają tam notatki ze zgłoszeń, czyli także dane klientów.
2. Instalacja dla całego konta, włączona na stałe (plugin działa w każdym folderze i każdej sesji):
   ```bash
   claude plugin marketplace add ekovoltis/ekovoltis-claude-marketplace-it
   claude plugin install pp-support@ekovoltis-it --scope user --config dataverse_urls=https://<org>.crm4.dynamics.com --config pp_environment_ids=<GUID> --config sharepoint_host=<tenant>.sharepoint.com --config sharepoint_site=/sites/<Witryna> --config incidents_dir=C:\Users\<login>\pp-dyzur\zgloszenia
   claude plugin enable pp-support@ekovoltis-it --scope user
   ```
   Automatyczne aktualizacje: w sesji `/plugin`, zakładka Marketplaces, `ekovoltis-it`, Enable auto-update. Kto woli plugin tylko w sesjach dyżurnych, instaluje go z `--scope local` w folderze dyżurnym.
3. Osobna konfiguracja Azure CLI dla dyżuru: w `C:\Users\<login>\.claude\settings.json` dopisz `"env": {"AZURE_CONFIG_DIR": "C:\\Users\\<login>\\.azure-pp-dyzur"}`. Przy instalacji `--scope local` wpis idzie do `<folder>\.claude\settings.local.json`, wtedy `az` w innej pracy zostaje bez zmian.
4. Logowanie wykonuje człowiek, w PowerShell, z tą samą zmienną:
   ```bash
   $env:AZURE_CONFIG_DIR = 'C:\Users\<login>\.azure-pp-dyzur'
   az config set core.enable_broker_on_windows=false
   az login --tenant <tenant-id> --allow-no-subscriptions
   pac auth create --name <nazwa> --environment https://<org>.crm4.dynamics.com
   ```
   Wyłączenie menedżera kont Windows (WAM) sprawia, że logowanie idzie przez przeglądarkę i nie dodaje konta do systemu. `--allow-no-subscriptions` jest potrzebne dla kont bez subskrypcji Azure.
5. Nowa sesja Claude Code, potem `/pp-support:sprawdz-dostepy`. Pracę nad kodem samego pluginu prowadź w sesji z wyłączonym pluginem.

Testy harnessu, bez dostępu do chmury:

```bash
python3 tests/test_harness.py
```

## Test dostępów: kolejność

W sesji Claude Code: `/pp-support:sprawdz-dostepy`, albo bezpośrednio `ppcall dostepy`. Sprawdzane są:

| Obszar | Test | Najbardziej prawdopodobny problem |
|---|---|---|
| Azure | `az account show`, lista subskrypcji (ARM) | brak roli Reader |
| Power Platform | profil `pac`, środowiska przez BAP | audience tokenu dla BAP (do potwierdzenia w teście) |
| Power Automate | lista flow w skonfigurowanych środowiskach | dyżurny nie jest współwłaścicielem flow ani adminem |
| Dataverse | WhoAmI, tabela `workflow`, tabela `flowruns` | brak użytkownika w środowisku, `flowruns` niedostępne |
| SharePoint | witryna przez Graph i przez REST | Azure CLI bez zgody na SharePoint |
| Fabric | lista obszarów roboczych i elementów | rola w obszarze, ustawienia dzierżawy |

Jeśli SharePoint albo Power Automate nie przyjmą tokenów Azure CLI, kolejny krok to własna rejestracja aplikacji w Entra ID z uprawnieniami delegowanymi (np. `Sites.Read.All`) i zgodą administratora. Daje to też możliwość objęcia pluginu osobną polityką dostępu warunkowego.

## Decyzje odłożone

- **Źródło prawdy rejestru.** Teraz migawka z artefaktu claude.ai. Docelowo lista SharePoint, Dataverse albo repozytorium, z odczytem przez `ppcall`.
- **Dataverse MCP.** Celowo pominięty w 0.1: udostępnia narzędzia usuwające, a od 15.12.2025 jest płatny dla agentów spoza Copilot Studio. Odczyt przez Web API w `ppcall` daje jeden punkt kontroli.
- **Dokumentacja i znane problemy.** Podłączenie przez `docs_dir`, gdy programiści skończą. Propozycje nowych wpisów plugin przygotowuje w `raport.md`, nie edytuje dokumentacji.
- **Jira.** Komentarze i zamykanie zgłoszeń przez konektor Atlassian, po ustaleniu procesu.

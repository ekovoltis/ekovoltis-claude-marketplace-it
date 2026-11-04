# pp-support: notatka przekazania (stan na 2026-11-03, 1.1.0)

Ten plik opisuje, skąd wziął się plugin, jakie decyzje już zapadły i co jest do zrobienia. Powstał w rozmowie w claude.ai, której sesja Claude Code nie widzi.

## Cel

Plugin Claude Code dla dyżurnych IT (programistów) obsługujących zgłoszenia dotyczące automatyzacji Power Platform w Ekovoltis. Kontekst biznesowy: z firmy odchodzą specjaliści Power Platform. Przed odejściem przygotowują inwentaryzację automatyzacji i dokumentację ze znanymi problemami. Plugin ma pomóc pracownikowi IT zrealizować zgłoszenie: zdiagnozować problem, zrozumieć przyczynę, przygotować naprawę i wykonać ją po jego jawnej akceptacji.

Docelowo plugin trafia do firmowego marketplace Claude Code połączonego z GitHubem.

## Zasada nadrzędna

Claude nie wykonuje żadnej zmiany w środowisku, dopóki pracownik nie zrozumie problemu, przyczyny i sposobu naprawy oraz nie zaakceptuje konkretnej wersji planu. Każda zmiana w kodzie pluginu musi tę zasadę zachować. Ułatwienia, które ją osłabiają, są odrzucane.

## Niezmienniki harnessu (nie osłabiać bez decyzji Michała Kaszty)

1. Ruch do chmury Microsoft i do Salesbooka wyłącznie przez `scripts/ppcall`. Tokeny i dane logowania pobiera ppcall w swoim procesie, nie wypisuje ich i wysyła tylko na hosty z listy w `common.py` (dane logowania Salesbooka tylko pod `salesbook_api_url`, bez przekierowań).
2. Akceptację tworzy wyłącznie hook `UserPromptSubmit` z wiadomości człowieka. Format: `AKCEPTUJĘ <ID> <skrót>` oraz pola `Przyczyna:` i `Naprawa:` napisane własnymi słowami (min. 30 znaków, odrzucane kopie z planu).
3. Akceptacja jest związana ze skrótem `plan.md`. Zmiana planu unieważnia akceptację. Ważność ograniczona czasowo.
4. Kroki wykonywane po kolei, każdy osobnym `ppcall exec`, z dodatkowym pytaniem Claude Code (`ask`). Nieudany krok zatrzymuje plan.
5. Warunki wykonania sprawdzane są dwa razy: w hooku i w ppcall (`common.can_execute`). Zmiana logiki tylko w jednym miejscu jest błędem. Warunki wymagające odczytu ze środowiska (stan definicji przy `flow-patch` i `flow-definition`) sprawdza tylko ppcall, bo hook nie wychodzi do sieci.
6. Nigdy, nawet z akceptacją: usuwanie zasobów (flow, rekordów, połączeń, rozwiązań), sekrety, ponowne uwierzytelnianie połączeń, zmiana definicji flow poza krokami `flow-patch` i `flow-definition`. Wyjątek dla sekretów (decyzja Michała z 2026-11-03): ppcall odczytuje dane logowania API Salesbooka z akcji HTTP flow integracji (`salesbook_auth_flow`) wyłącznie po to, żeby czytać z API Salesbooka, i nigdy ich nie wypisuje; wartości haseł w odpowiedziach `get` i `runs` są zastępowane `***`. Po przeniesieniu danych logowania do Key Vault źródło trzeba zmienić. `flow-patch` (ITS-417, 2026-11-03, rozszerzony w 1.2.0 po ITS-420) zmienia istniejące wartości w istniejących akcjach: tekst pod `inputs/parameters` i `inputs/body`, wartości Compose i zmiennych, wyrażenia i mapowania akcji Select, Query, Table, If, Until, Foreach, Terminate oraz `runAfter`, najwyżej 5 na krok, z typem akcji sprawdzanym na żywej definicji. `flow-definition` (decyzja Michała Kaszty z 2026-11-04, ITS-420; support automatów nie podlega standardom SWO) zapisuje pełną nową definicję z pliku związanego skrótem z planem. Może dodawać, usuwać i przestawiać akcje, najwyżej 10 na krok. Nie zmienia wyzwalaczy, `connectionReferences`, `$connections` ani połączeń akcji. Dodane i zmienione akcje nie mogą dodać, usunąć ani zmienić kluczy adresu, metody, nagłówków, uwierzytelnienia i połączenia, więc nie powstaje nowa akcja HTTP ani akcja konektora (dodawać i zmieniać można tylko akcje wbudowane z listy w `common.FLOW_SAFE_TYPES`); wyjątkiem jest powrót do kopii zapisanej przez `ppcall exec` (rejestr kopii w stanie). Żaden z tych kroków nie zmienia zmiennych ani akcji, z których akcja wysyłająca bierze adres, nagłówki albo uwierzytelnienie, i nie wprowadza odczytu wejść innej akcji (`actions(...)`, `authentication`). Zależności liczone są z domknięciem (Compose → Select → Http, zmienne, pętle; nazwa wyliczana blokuje każdą zmianę). Adresat akcji konektora (np. odbiorca maila albo czatu Teams pod `parameters`) nie jest kluczem routingu: `flow-patch` od 0.2.0 może go zmienić wprost i to jest założenie projektu, nie luka. Krok `http` nie zmienia definicji flow (w `workflows` tylko `statecode` i `statuscode`, w Power Automate API tylko `start`, `stop`, `run`, `resubmit`, `cancel`), nie używa `$batch` ani operacji na rozwiązaniach. Oba kroki: tylko flow niezarządzane, tylko środowiska z osobnych list (`flow_patch_dataverse_urls`, `flow_definition_dataverse_urls`), z kopią w `backup/`, `If-Match`, porównaniem po zapisie i przywróceniem kopii przy różnicy. Wycofanie to nowy plan z nową akceptacją. Zmian w aplikacjach, pipeline'ach i wdrożeń rozwiązań plugin nie wykonuje.
7. Błąd wewnętrzny hooka kończy się odmową (fail closed). Brak Pythona blokuje operacje.
8. Hooki to zabezpieczenie przed pomyłką, nie granica bezpieczeństwa. Granicą jest tożsamość: role tylko do odczytu na co dzień, uprawnienia do zmian przez PIM na czas zgłoszenia.

## Decyzje już podjęte

- Forma: plugin Claude Code (potrzebne wykonywanie `pac`, `az`, wywołań API i hooki). Claude.ai i Copilot Studio rozważone i odrzucone.
- Użytkownicy: dyżurni programiści, nie użytkownicy biznesowi.
- `defaultEnabled: false`, bo hooki działają w całej sesji. Dyżurny włącza plugin świadomie.
- Dataverse MCP pominięty w 0.1: ma narzędzia usuwające, a od 15.12.2025 jest płatny dla agentów spoza Copilot Studio. Odczyt Dataverse przez Web API w ppcall.
- Rejestr automatyzacji: migawka z artefaktu claude.ai „Portfolio Automatyzacji” (71 pozycji, 38 aktywnych, pole instrukcji puste we wszystkich). Źródło prawdy nie jest ustalone, wcześniej była to lista SharePoint.
- Dokumentacji przygotowywanej przez programistów plugin nie edytuje. Podłączenie przez ustawienie `docs_dir`, gdy będzie gotowa. Propozycje wpisów „znany problem” trafiają do `raport.md` zgłoszenia.
- Ekovoltis podlega KSC/NIS2, więc rozliczalność (dziennik audytu, dziennik wykonania z treścią akceptacji) jest wymaganiem.

## Uwaga przy pracy nad samym pluginem

Strażnik blokuje zapis do katalogu pluginu w sesji, w której plugin jest załadowany. Kod pluginu edytuj w zwykłej sesji bez pluginu. Testy działania prowadź w osobnej sesji: `claude --plugin-dir ./plugins/pp-support`.

## Zweryfikowane

- `claude plugin validate --strict`: przechodzi.
- `python3 tests/test_harness.py`: przechodzi na Linuksie i Windows (strażnik, walidacja planu, akceptacja, zmiana planu po akceptacji, wykonanie z podmienionym HTTP, odwołanie, brak Pythona, `ppcall get --out`, polecenia z PowerShell, awaria hooka).
- Od 0.1.1: `ppcall get --out` zapisuje tylko w `zgloszenia/<ID>/` (bez `plan.md` i `wykonanie.jsonl`), sprawdzane w hooku i w ppcall. Każdy niezerowy kod hooka to odmowa; UserPromptSubmit przy awarii blokuje tylko wiadomości z akceptacją albo odwołaniem (decyzja Michała z 2026-10-15). Ścieżka do ppcall w innej formie niż bezpośrednie wywołanie (`bash -c`, zmienna, `python ppcall.py`) jest odrzucana.

Test dostępów na produkcji 21.10.2026 (Windows, Git Bash, konto e2vitservice, tokeny Azure CLI):

- Azure CLI wydaje tokeny przyjmowane przez ARM, BAP, Power Automate, Dataverse i Fabric. Tabela `flowruns` w środowisku ekovoltis jest dostępna.
- `claude plugin install --config klucz=wartość` zapisuje ustawienie z `multiple: true` jako zwykły tekst; `common._as_list` go obsługuje.
- Launchery `run-py` i `ppcall` działają w Git Bash. Git Bash zamieniał wartości ustawień zaczynające się od `/` na ścieżki Windows (`/sites/X` na `C:/.../Git/sites/X`); od 0.1.2 launchery wyłączają tę konwersję, a `common.site_path` naprawia wartość, która już przyszła zniekształcona.

## Do zweryfikowania na prawdziwym środowisku

- SharePoint (Graph i REST): test z 21.10.2026 nie doszedł do zapytania przez opisany wyżej błąd ścieżki. Jeśli Azure CLI nie dostanie przyjmowanego tokenu, następny krok to własna rejestracja aplikacji w Entra ID (zgłoszona do administratora).
- Nazwa narzędzia i pole polecenia dla PowerShell na Windows (strażnik zakłada `PowerShell` i `command`).
- Składnia fragmentu managed settings z README (`permissions.deny`, `disableBypassPermissionsMode`).

Od 1.1.0 strażnik pilnuje tylko rozmów dyżurnych (decyzja Michała z 2026-11-03: plugin włączony na stałe, ale bez wpływu na inne projekty). Rozmowa staje się dyżurna przy skillu pluginu, pierwszym `ppcall`, w folderze z `support_dirs` albo po wiadomości z akceptacją, odwołaniem lub słowem „dyżur”; znacznik leży w chronionym stanie. Niezależnie od tego zawsze działają: akceptacja i `ppcall exec` (niezmienniki 2–5), ochrona stanu, dziennika wykonania, weryfikacji i kopii. Poza rozmową dyżurną Claude może sięgnąć do usług Microsoft z pominięciem `ppcall`; to świadomie przyjęte w ramach niezmiennika 8. Od 1.1.1 ochrona stanu przed obejściami (globy na nazwie, podkatalogi `akceptacje` i `sesje`) działa tylko wtedy, gdy polecenie wskazuje dom albo `.claude` lub bieżący katalog to dom albo `.claude`; pełna nazwa stanu, także sklejona z części, i ścieżka stanu są blokowane zawsze. Obejście bez żadnego odniesienia do domu przejdzie, co mieści się w niezmienniku 8.

Od 0.3.0 plugin może być zainstalowany w zakresie user i włączony na stałe (decyzja Michała z 2026-11-03), ze stałym `incidents_dir`. Odczyt hurtowy (`get --all`, `runs`) i `verify` są tylko do odczytu. `flow-patch` obejmuje też całe `inputs` akcji Compose i wartości zmiennych; typ akcji sprawdza ppcall na żywej definicji.

Krok `flow-patch` (0.2.0, 2026-11-03): testy z podmienionym Dataverse przechodzą na Windows. Na prawdziwym środowisku do sprawdzenia: test ITS-417 na kopii flow w `ekovoltis-test`, który wymaga wcześniejszej synchronizacji rozwiązania Billing (w sandboxie `[MAIN] 6` ma `modifiedon` 2026-01-21).

Od 1.2.0 (2026-11-04, ITS-420): szerszy `flow-patch` i krok `flow-definition`, testy z podmienionym Dataverse. Na prawdziwym środowisku do sprawdzenia: (a) zapis `clientdata` z dodaną akcją Compose na kopii flow w środowisku testowym (czy Power Automate nie wyłącza flow i nie zmienia definicji przy zapisie, np. dopisując metadane, co skończy się przywróceniem kopii); (b) który identyfikator flow przyjmuje Flow API dla flow z rozwiązania (`ppcall runs <adres Dataverse> <workflowid>` próbuje kolejno `workflowid`, `resourceid`, `workflowidunique` i wypisuje, który zadziałał).

## Kolejne zadania

1. Zrobione (0.1.0): plugin w repozytorium `ekovoltis/ekovoltis-claude-marketplace-it`, marketplace `ekovoltis-it`.
2. Zrobione (0.1.0): GitHub Actions z `claude plugin validate --strict` i testami na Linuksie i Windows.
3. Zrobione 21.10.2026: test dostępów na produkcji, wyniki wyżej. Do powtórzenia dla SharePoint po wdrożeniu 0.1.2.
4. W toku: poprawki z punktu 3 i z listy „do zweryfikowania”, z testami (0.1.2: ścieżki z Git Bash, walidacja ustawień).
5. Pilot na 3 do 4 aktywnych automatach o różnym charakterze (np. 007 AKDW, 003 MailBOK, 002 ImportUmow), wyłącznie diagnoza bez wykonywania zmian.

## Teksty dla ludzi

Komunikaty, README i dokumentacja po polsku, bez długich myślników i typowych markerów tekstu generowanego.

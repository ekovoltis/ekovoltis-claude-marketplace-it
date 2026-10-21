# pp-support: notatka przekazania (stan na 2026-10-15)

Ten plik opisuje, skąd wziął się plugin, jakie decyzje już zapadły i co jest do zrobienia. Powstał w rozmowie w claude.ai, której sesja Claude Code nie widzi.

## Cel

Plugin Claude Code dla dyżurnych IT (programistów) obsługujących zgłoszenia dotyczące automatyzacji Power Platform w Ekovoltis. Kontekst biznesowy: z firmy odchodzą specjaliści Power Platform. Przed odejściem przygotowują inwentaryzację automatyzacji i dokumentację ze znanymi problemami. Plugin ma pomóc pracownikowi IT zrealizować zgłoszenie: zdiagnozować problem, zrozumieć przyczynę, przygotować naprawę i wykonać ją po jego jawnej akceptacji.

Docelowo plugin trafia do firmowego marketplace Claude Code połączonego z GitHubem.

## Zasada nadrzędna

Claude nie wykonuje żadnej zmiany w środowisku, dopóki pracownik nie zrozumie problemu, przyczyny i sposobu naprawy oraz nie zaakceptuje konkretnej wersji planu. Każda zmiana w kodzie pluginu musi tę zasadę zachować. Ułatwienia, które ją osłabiają, są odrzucane.

## Niezmienniki harnessu (nie osłabiać bez decyzji Michała Kaszty)

1. Ruch do chmury Microsoft wyłącznie przez `scripts/ppcall`. Tokeny pobiera ppcall w swoim procesie, nie wypisuje ich i wysyła tylko na hosty z listy w `common.py`.
2. Akceptację tworzy wyłącznie hook `UserPromptSubmit` z wiadomości człowieka. Format: `AKCEPTUJĘ <ID> <skrót>` oraz pola `Przyczyna:` i `Naprawa:` napisane własnymi słowami (min. 30 znaków, odrzucane kopie z planu).
3. Akceptacja jest związana ze skrótem `plan.md`. Zmiana planu unieważnia akceptację. Ważność ograniczona czasowo.
4. Kroki wykonywane po kolei, każdy osobnym `ppcall exec`, z dodatkowym pytaniem Claude Code (`ask`). Nieudany krok zatrzymuje plan.
5. Warunki wykonania sprawdzane są dwa razy: w hooku i w ppcall (`common.can_execute`). Zmiana logiki tylko w jednym miejscu jest błędem.
6. Nigdy, nawet z akceptacją: usuwanie, sekrety, zmiana definicji flow na produkcji (standard SWO: zmiany logiki przez ścieżkę deweloperską), ponowne uwierzytelnianie połączeń.
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

## Do zweryfikowania na prawdziwym środowisku

- Czy Azure CLI wyda przyjmowane tokeny dla: BAP (`https://service.powerapps.com/`), Power Automate (`https://service.flow.microsoft.com/`), SharePoint (Graph i REST), Fabric. Najbardziej prawdopodobne problemy: SharePoint i BAP. Jeśli tak, następny krok to własna rejestracja aplikacji w Entra ID z uprawnieniami delegowanymi i zgodą administratora.
- Czy tabela `flowruns` jest dostępna w środowiskach Dataverse.
- W jakim formacie Claude Code przekazuje do hooków wartości `userConfig` z `multiple: true` (`common._as_list` obsługuje JSON i listy rozdzielane przecinkami).
- Nazwa narzędzia i pole polecenia dla PowerShell na Windows (strażnik zakłada `PowerShell` i `command`).
- Działanie launcherów `run-py` i `ppcall` w Git Bash na Windows (ścieżki `/c/...`, `py -3`).
- Składnia fragmentu managed settings z README (`permissions.deny`, `disableBypassPermissionsMode`).

## Kolejne zadania

1. Umieścić plugin w repozytorium marketplace (`plugins/pp-support/`), dodać wpis w `.claude-plugin/marketplace.json`.
2. Dodać GitHub Actions: `claude plugin validate --strict` oraz `python3 tests/test_harness.py`.
3. Uruchomić test dostępów (`/pp-support:sprawdz-dostepy`) na stanowisku z `az login` i `pac auth create`, przeanalizować raport `dostepy-*.json`, przygotować listę spraw dla administratorów.
4. Poprawić to, co wyjdzie z punktu 3 i z listy „do zweryfikowania”, z dopisaniem testów.
5. Pilot na 3 do 4 aktywnych automatach o różnym charakterze (np. 007 AKDW, 003 MailBOK, 002 ImportUmow), wyłącznie diagnoza bez wykonywania zmian.

## Teksty dla ludzi

Komunikaty, README i dokumentacja po polsku, bez długich myślników i typowych markerów tekstu generowanego.

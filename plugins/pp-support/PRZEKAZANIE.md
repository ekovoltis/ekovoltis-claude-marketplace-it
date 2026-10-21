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

Test dostępów na produkcji 21.10.2026 (Windows, Git Bash, konto e2vitservice, tokeny Azure CLI):

- Azure CLI wydaje tokeny przyjmowane przez ARM, BAP, Power Automate, Dataverse i Fabric. Tabela `flowruns` w środowisku ekovoltis jest dostępna.
- `claude plugin install --config klucz=wartość` zapisuje ustawienie z `multiple: true` jako zwykły tekst; `common._as_list` go obsługuje.
- Launchery `run-py` i `ppcall` działają w Git Bash. Git Bash zamieniał wartości ustawień zaczynające się od `/` na ścieżki Windows (`/sites/X` na `C:/.../Git/sites/X`); od 0.1.2 launchery wyłączają tę konwersję, a `common.site_path` naprawia wartość, która już przyszła zniekształcona.

## Do zweryfikowania na prawdziwym środowisku

- SharePoint (Graph i REST): test z 21.10.2026 nie doszedł do zapytania przez opisany wyżej błąd ścieżki. Jeśli Azure CLI nie dostanie przyjmowanego tokenu, następny krok to własna rejestracja aplikacji w Entra ID (zgłoszona do administratora).
- Nazwa narzędzia i pole polecenia dla PowerShell na Windows (strażnik zakłada `PowerShell` i `command`).
- Składnia fragmentu managed settings z README (`permissions.deny`, `disableBypassPermissionsMode`).

## Kolejne zadania

1. Zrobione (0.1.0): plugin w repozytorium `ekovoltis/ekovoltis-claude-marketplace-it`, marketplace `ekovoltis-it`.
2. Zrobione (0.1.0): GitHub Actions z `claude plugin validate --strict` i testami na Linuksie i Windows.
3. Zrobione 21.10.2026: test dostępów na produkcji, wyniki wyżej. Do powtórzenia dla SharePoint po wdrożeniu 0.1.2.
4. W toku: poprawki z punktu 3 i z listy „do zweryfikowania”, z testami (0.1.2: ścieżki z Git Bash, walidacja ustawień).
5. Pilot na 3 do 4 aktywnych automatach o różnym charakterze (np. 007 AKDW, 003 MailBOK, 002 ImportUmow), wyłącznie diagnoza bez wykonywania zmian.

## Teksty dla ludzi

Komunikaty, README i dokumentacja po polsku, bez długich myślników i typowych markerów tekstu generowanego.

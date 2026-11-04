# Zmiany w pp-support

## 1.2.2 (2026-11-04)

Po przeglądzie bezpieczeństwa 1.2.0.

- Kopie definicji flow (mogą zawierać hasła) `ppcall exec` zapisuje w chronionym stanie, `~/.claude/pp-support-state/kopie/<ID>/`, a nie w `zgloszenia/<ID>/backup/`. W planie zostaje odnośnik `backup/<plik>`; `ppcall` i strażnik (sprawdzenie sha256) szukają kopii w stanie: w tym zgłoszeniu, a potem po jednoznacznej nazwie w innych.
- Kopie sprzed 1.2.2 `ppcall plan-check` i `ppcall exec` przenoszą do stanu ze wszystkich zgłoszeń (oryginał znika po sprawdzeniu skrótu; plik o tej samej nazwie i innej treści zostaje z ostrzeżeniem; dowiązanie albo junction w miejscu `backup/` jest pomijane). Do czasu przeniesienia działa odczyt ze starego miejsca i dotychczasowe blokady `backup/`.
- `wykonanie.jsonl` ma pola `kopia_plan` (`backup/<plik>`, do wpisania w plan wycofania) i `kopia_sha256`, także dla `flow-patch`.
- Strażnik: narzędzia Read i Grep nie sięgają do stanu (zawsze, odmowa trafia do dziennika audytu); Grep obejmujący dom albo `.claude` jest blokowany (hook obejmuje teraz Grep). Wzorce na `kopie` obok `akceptacje` i `sesje` przy odniesieniu do domu (`cd ~/.claude && cat */kopie/*/*`). W rozmowie dyżurnej rekurencyjne `grep -r`, `rg`, `find`, `tar`, `zip`, `cp -r`, `Get-ChildItem -Recurse`/`-Depth`, `Compress-Archive` i podobne, obejmujące dom, `.claude` albo dysk, są blokowane, także po `cd`/`Set-Location` w tym samym poleceniu i pod nakładkami (`command`, `env`, `timeout`, `xargs`, `cmd /c`).
- Krok `cli` z `az` tylko z jawnej listy: `logic workflow enable`/`disable`, `webapp restart`, `functionapp restart`. Polecenia `update`, `set`, `create` i każde inne polecenie `az` są odrzucane w hooku i w `ppcall`. Argumenty z pliku (`@plik`) i znaki interpretowane przez cmd.exe (`&`, `|`, `^`, `%`, cudzysłowy, spacje; `az` na Windows to `az.cmd`) są odrzucane dla `az` i `pac`.

## 1.2.1 (2026-11-04)

- ITS-418: przy 403 z Graph dla zawartości witryny SharePoint i przy 401 z SharePoint REST `ppcall get` dopisuje wyjaśnienie: na jakim koncie działa (Azure CLI, nie konto z przeglądarki), kto nadaje odczyt i że REST nie przyjmuje tokenu Azure CLI (czytaj przez Graph). Konto bierze z `az account show`, bez dotykania tokenu.
- `ppcall dostepy`: wiersz „konto ppcall” i test „Graph: biblioteka dokumentów”; skill `sprawdz-dostepy` i README opisują ograniczenia SharePoint.

## 1.2.0 (2026-11-04)

Naprawy w definicji flow bez ścieżki SWO (decyzja Michała Kaszty z 2026-11-04, ITS-420). Niezmiennik 6 zmieniony: zmiana logiki flow jest dozwolona z akceptacją, w granicach poniżej.

- `flow-patch` obejmuje też `inputs/select` (Select), `inputs/where` i `inputs/from` (Filter array), `inputs/columns` (Table), `expression` (warunek, Until), `foreach`, `inputs/runStatus` i `inputs/runError` (Terminate) oraz `runAfter` dowolnej akcji. Wartości poza `parameters` i `body` mogą być dowolnym JSON, porównywanym bez względu na kolejność kluczy. `runAfter` tylko na akcje z tego samego zakresu, bez cykli, ze znanymi statusami. Porównanie po zapisie dopuszcza różnice wyłącznie pod zadeklarowanymi ścieżkami.
- Nowy krok `flow-definition`: pełna nowa definicja z pliku `flow-<workflowid>-nowa.json`, której skrót SHA-256 jest w planie. Może dodawać, usuwać i przestawiać akcje (najwyżej 10 na krok). Nie zmienia wyzwalaczy, połączeń ani niczego poza akcjami i nie dodaje akcji HTTP ani konektorów. `plan-check` pokazuje zmiany akcji. Po zapisie różnica wobec pliku albo zmiana stanu flow przywraca kopię. Wycofanie przez kopię z rejestru kopii. Nowe ustawienie `flow_definition_dataverse_urls`, domyślnie puste.
- Kopie definicji mają unikalne nazwy także przy dwóch zapisach w tej samej sekundzie, a ich skróty trafiają do rejestru kopii w chronionym stanie.
- `ppcall get` z Dataverse zwraca nazwy lookupów i opcji (`Prefer: odata.include-annotations=...FormattedValue`).
- `ppcall runs <adres Dataverse> <workflowid>`: środowisko z BAP i identyfikator flow w Flow API ustala `ppcall`.
- `ppcall rejestr`: dokładne trafienie kodu albo akronimu jest pierwsze (AKDW przed AKDWT); `--flow <adres Dataverse> <workflowid>` szuka po numerze z nazwy flow.
- `ppcall konfiguracja`: odczyt ustawień (alias `config`), zamiast `cat` pliku w chronionym stanie.
- Skill i README bez odwołań do SWO; wzorzec korekty lookupu Dataverse krokiem `http`.
- Po przeglądzie bezpieczeństwa:
  - `flow-patch` i `flow-definition` nie zmieniają zmiennych ani akcji, z których akcja wysyłająca (Http, konektor) bierze adres, nagłówki albo uwierzytelnienie. Bez tego zmiana zmiennej przekierowałaby istniejące wywołanie z danymi logowania. Zależności są liczone z domknięciem: łańcuchy przez Compose, Select, Parse JSON, zmienne i pętle, nazwy bez względu na wielkość liter. Nazwa wyliczana w wyrażeniu blokuje każdą zmianę akcji i zmiennych w tym flow. `result(...)` też liczy się jako odczyt wejść.
  - Zakazane są wyrażenia `actions(...)` i pole `authentication`, czyli odczyt wejść innej akcji z hasłem.
  - `flow-definition` dodaje i zmienia tylko akcje z listy typów wbudowanych. Typ jest czytany bez względu na wielkość liter, a klucze różniące się tylko wielkością liter są odrzucane.
  - Zakres akcji jest rozpoznawany po ścieżce, nie po nazwie, więc zakres o nazwie `else` nie myli się z gałęzią warunku. Kontenery akcji innego typu niż obiekt są odrzucane, a tekst `***` (zamaskowane hasło) w zmienianych akcjach blokuje zapis.
  - `plan-check` pokazuje treść dodanych i zmienionych akcji, z zamaskowanymi hasłami.
  - Krok `http` w `workflows` może tylko włączyć albo wyłączyć flow (`statecode`, `statuscode`). W Power Automate API może tylko `start`, `stop`, `run`, `resubmit` i `cancel`. `$batch` i operacje na rozwiązaniach są zabronione. Krok `cli` nie przyjmuje `import`, `upgrade`, `publish` ani `deploy`.
  - Strażnik blokuje polecenia powłoki sięgające do `zgloszenia/<ID>/backup` (odczyt, kopiowanie).
  - Skrót kopii jest liczony z zapisanych bajtów, a status `runAfter` innego typu niż tekst nie wywołuje już wyjątku.

## 1.1.1 (2026-11-04)

- Poprawka 1.1.0: ochrona stanu akceptacji blokowała zwykłą pracę także poza rozmowami dyżurnymi, np. `cd ".../plugins/pp-support"` (cudzysłów po nazwie brany za sklejanie), każde polecenie ze słowem „akceptacje” (zapis notatki heredociem) i ścieżki z katalogiem `sesje`. Wzorce obejść (globy na nazwie stanu, podkatalogi `akceptacje` i `sesje`) działają teraz tylko, gdy polecenie wskazuje katalog domowy albo `.claude` (`~`, `$HOME`, `USERPROFILE`, `.claude`, także sklejone z części, wzorce typu `.c?aude`, `..` w ścieżce, wzorzec tuż za katalogiem domowym) albo gdy bieżący katalog to dom lub `.claude`. `akceptacje` i `sesje` liczą się tylko jako część ścieżki.
- Sklejanie nazwy z części (`'pp-support-'state`, `"pp-support-" + "state"`) wykrywa porównanie pełnej nazwy po usunięciu cudzysłowów, zawsze. Nowe: każde polecenie powłoki uruchomione z wnętrza katalogu stanu jest odrzucane. Bez kotwicy, jak w 1.1.0, nadal odrzucane są wzorce po `pp-supp` (`pp-supp*`), wzorce z co najmniej 5 znakami nazwy (`*-state`) i fragment nazwy w kodzie podanym interpreterowi (`python -c`, `node -e`, `pwsh -Command`).

## 1.1.0 (2026-11-03)

- Strażnik pilnuje tylko rozmów dyżurnych. Rozmowa staje się dyżurna przy skillu pluginu, pierwszym `ppcall`, w folderze z nowego ustawienia `support_dirs` albo po wiadomości z akceptacją lub odwołaniem w ich formacie albo zaczynającej się od słowa „dyżur”. Wyjście z folderu dyżurnego nie wyłącza pilnowania; nieczytelna konfiguracja albo błąd odczytu stanu oznacza pilnowanie. W innych rozmowach plugin nie blokuje pracy. Akceptacja, `ppcall exec` i ochrona stanu, dzienników i kopii działają zawsze.
- Poprawka 1.0.0: hook strażnika nie był podpięty pod `Read`, więc blokada odczytu kopii w `backup/` nie działała. `hooks.json` obejmuje teraz `Read` i `Skill`, a test sprawdza pokrycie narzędzi.
- Ochrona stanu akceptacji także po rzeczywistej ścieżce katalogu stanu, nie tylko po nazwie.

## 1.0.0 (2026-11-03)

- Salesbook: `ppcall get` czyta z API (tylko GET) z danymi logowania pobieranymi z akcji HTTP flow integracji (`salesbook_auth_flow`, decyzja Michała z 2026-11-03), wysyłanymi tylko pod `salesbook_api_url`, bez przekierowań; pliki dokumentów bez danych logowania. Strażnik blokuje `curl` do Salesbooka.
- Przeglądarka (Claude in Chrome, przeglądarka w aplikacji): odczyt bez pytań, każde inne działanie za zgodą człowieka, wgrywanie plików zablokowane, adresy tylko http(s). Skill: porównanie API, aplikacji i PDF Salesbooka.
- Hasła w odpowiedziach `ppcall get` i `ppcall runs` (klucze z hasłem i tokenem, nagłówki Basic/Bearer, klucze w adresach) zastępowane `***`; odczyt kopii w `backup/` zablokowany. Token Microsoft nie idzie za przekierowaniem na inny adres.
- `flow-patch` obejmuje całe `inputs` akcji Compose, `inputs/variables/<n>/value` w InitializeVariable i `inputs/value` w SetVariable. Typ akcji jest sprawdzany na aktualnej definicji, ścieżki mogą zawierać numery pozycji list.
- `ppcall get --all` łączy wszystkie strony wyniku (tylko ten sam host). `ppcall runs` zapisuje historię przebiegów flow do pliku, z wynikami akcji i treścią wejść lub wyjść wskazanych akcji.
- `ppcall verify <ID>` wykonuje odczyty z sekcji „Weryfikacja po naprawie” i zapisuje wynik w `weryfikacja.jsonl`, chronionym tak jak dziennik wykonania.
- Skill: analiza skali na plikach zamiast pojedynczych odczytów, `verify` po naprawie, komentarz do Jiry po zgodzie pracownika, zasady ograniczania pracy ręcznej. README: instalacja w zakresie user, włączona na stałe.

## 0.2.0 (2026-11-03)

- Nowy krok planu `flow-patch`: zmiana istniejących wartości tekstowych pod `inputs/parameters` albo `inputs/body` akcji jednego flow (najwyżej 5), domyślnie wyłączony, włączany per środowisko ustawieniem `flow_patch_dataverse_urls`. `plan-check` sprawdza go na aktualnej definicji i wypisuje różnice oraz krok wycofania. `exec` robi kopię, zapisuje z `If-Match` i porównuje definicję po zapisie; nieoczekiwana różnica albo błąd sprawdzenia po zapisie przywraca kopię. Plan z tym krokiem wymaga sekcji „Analiza wpływu” i „Test”. Zmiana niezmiennika 6.
- `ppcall get` czyta treść wejść i wyjść akcji z historii przebiegu (`*.environment.api.powerplatformusercontent.com`) bez tokenu i tylko metodą GET, a do dziennika audytu trafia adres bez podpisu. Strażnik blokuje ten host dla `curl` i innych poleceń.
- Spacje i polskie znaki w adresie są kodowane, a niepoprawny adres daje komunikat zamiast wyjątku.
- Wyjście `ppcall` w UTF-8 z zastępowaniem znaków. Błąd zapisu do konsoli (Windows, Errno 22) nie przerywa polecenia.
- Ustawienie `incidents_dir`: stały katalog zgłoszeń niezależny od folderu sesji.

## 0.1.2 (2026-10-21)

- Git Bash na Windows zamieniał ustawienia zaczynające się od `/` na ścieżki Windows, przez co test SharePoint wysyłał zapytania pod błędne adresy. Launchery wyłączają tę konwersję dla ustawień pluginu, a `common.site_path` naprawia wartość, która już przyszła zniekształcona.
- Błędne ustawienia (witryna SharePoint bez `/`, adres Dataverse spoza `*.crm*.dynamics.com`, obcy host SharePoint) są zgłaszane na starcie sesji i w teście dostępów jako błąd konfiguracji, a nie jako brak uprawnień.
- Migawka rejestru automatyzacji odświeżona z Portfolio Automatyzacji (stan na 21.10.2026).
- README: przygotowanie stanowiska dyżurnego na Windows. PRZEKAZANIE: wyniki testu dostępów z 21.10.2026.

## 0.1.1 (2026-10-21)

- `ppcall get --out` zapisuje tylko w `zgloszenia/<ID>/`, bez `plan.md` i `wykonanie.jsonl`; sprawdzane w hooku i w ppcall.
- Każdy niezerowy kod hooka kończy się odmową (fail closed). UserPromptSubmit przy awarii blokuje tylko akceptacje i odwołania.
- Strażnik rozpoznaje ppcall w ścieżkach Windows i w PowerShell, a odrzuca inne formy wywołania.
- Launchery wymuszają UTF-8: przy cp1252 hooki z polskimi znakami kończyły się błędem i akceptacja się nie rejestrowała.
- CI na `windows-latest`.

## 0.1.0 (2026-10-15)

- Pierwsza wersja: harness (strażnik, akceptacja związana ze skrótem planu, wykonanie krok po kroku przez ppcall), test dostępów, migawka rejestru, skille `zgloszenie` i `sprawdz-dostepy`.
- Opis skilla `zgloszenie` w cudzysłowie, bo niecytowany dwukropek psuł YAML frontmattera.

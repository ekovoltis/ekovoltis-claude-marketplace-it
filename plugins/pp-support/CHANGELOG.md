# Zmiany w pp-support

## 0.3.0 (2026-11-03)

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

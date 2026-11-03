# Plan naprawy: zgłoszenie <ID>

<!-- Ten plik czyta pracownik przed akceptacją. Pisz konkretnie i prostym językiem.
     Każda zmiana tego pliku po akceptacji unieważnia akceptację (zmienia się skrót). -->

## Automatyzacja
<!-- Kod, nazwa, status z rejestru, środowisko (dev/test/prod), właściciel biznesowy, developer. -->

## Objaw
<!-- Co zgłoszono i co widać w systemie. Od kiedy. Kogo dotyczy. -->

## Przyczyna
<!-- Jedna przyczyna źródłowa, potwierdzona dowodami. Jeśli to nadal hipoteza, plan nie jest gotowy. -->

## Dowody
<!-- Lista faktów z diagnozy: co sprawdzono (URL lub polecenie), kiedy, jaki był wynik.
     Odróżnij fakty od wniosków. Odwołuj się do notatki.md. -->

## Proponowana naprawa
<!-- Co zmieniamy i dlaczego to usuwa przyczynę, a nie tylko objaw.
     Jakie alternatywy rozważono i dlaczego odrzucone. -->

## Ryzyko i wpływ
<!-- Co może pójść źle. Kogo dotknie wykonanie (klienci, inne automaty, dane).
     Czy krok jest odwracalny. Czy wymaga okna serwisowego lub uprzedzenia właściciela. -->

## Analiza wpływu
<!-- Wymagana przy kroku flow-patch. Które przypadki zmienią wynik, a które nie, z liczbami z danych
     (np. "zmienia 54 korekty korekt, 332 pojedyncze korekty dają identyczny wynik"). Inne plany: usuń sekcję
     albo opisz krótko. -->

## Test
<!-- Wymagana przy kroku flow-patch. Wynik testu tej samej zmiany na kopii flow w środowisku testowym
     albo uzasadnienie, dlaczego test nie jest możliwy. -->

## Plan wycofania
<!-- Jak cofnąć każdy krok. Jeśli krok jest nieodwracalny, napisz to wprost i wyjaśnij, czym to grozi. -->

## Weryfikacja po naprawie
<!-- Jakim odczytem sprawdzimy, że naprawa zadziałała (konkretny URL lub polecenie i oczekiwany wynik).
     Opcjonalnie blok ```json z odczytami dla `ppcall verify <ID>` (tylko GET, hosty z listy dozwolonych):
     [{"opis": "Flow włączony", "url": "https://api.flow.microsoft.com/...?api-version=2016-11-01",
       "sciezka": ["properties", "state"], "oczekiwane": "Started"}]
     "sciezka" to klucze (albo numery pozycji list) w odpowiedzi; bez "oczekiwane" liczy się tylko HTTP 200. -->

## Kroki
<!-- Tylko operacje zmieniające. Odczyty i weryfikacje nie są krokami.
     typ "http": metoda POST, PATCH albo PUT, url na dozwolonym hoście, opcjonalnie body.
     typ "cli": argv jako lista, program pac albo az.
     typ "flow-patch": zmiana wartości tekstowych pod inputs/parameters albo inputs/body istniejących akcji jednego flow,
     całego inputs akcji Compose, inputs/variables/<n>/value w InitializeVariable albo inputs/value w SetVariable,
     najwyżej 5 zmian:
       {"nr": 1, "opis": "...", "typ": "flow-patch",
        "srodowisko": "https://<nazwa>.crm4.dynamics.com", "workflowid": "<GUID>",
        "oczekiwany_modifiedon": "<modifiedon odczytany z workflows, np. 2026-08-25T09:52:49Z>",
        "zmiany": [{"sciezka": ["properties", "definition", "actions", "<akcja>", "inputs", "parameters", "<klucz>"],
                    "bylo": "<dokładna aktualna wartość>", "ma_byc": "<nowa wartość>"}]}
       Ścieżka to lista kluczy po nazwach akcji, z elementami else, cases/<przypadek> i default dla
       zagnieżdżonych akcji. Zabronione: wyzwalacze, runAfter, type, expression, uri, method, headers,
       host, authentication, path, pathTemplate, function i klucze połączeń. Krok działa tylko w środowiskach z ustawienia flow_patch_dataverse_urls.
     DELETE, usuwanie, sekrety i inne zmiany definicji flow są odrzucane przez walidację. -->
```json
[]
```

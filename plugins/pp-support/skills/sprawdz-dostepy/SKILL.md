---
name: sprawdz-dostepy
description: Test dostępów pluginu pp-support do Azure, Power Platform (Power Automate, środowiska), Dataverse, SharePoint i Fabric z tożsamością bieżącego użytkownika. Używaj przy pierwszym uruchomieniu pluginu na stanowisku, po zmianie uprawnień, gdy odczyt w trakcie zgłoszenia kończy się błędem 401 lub 403, albo na prośbę „sprawdź dostępy”, „czy mam dostęp do…”, „dlaczego ppcall nie działa”.
---

# Test dostępów

Uruchom:

```
"${CLAUDE_PLUGIN_ROOT}/scripts/ppcall" dostepy
```

Test wykonuje wyłącznie odczyty i zapisuje raport `dostepy-<czas>.json` w bieżącym katalogu. Nie zawiera tokenów.

## Jak przedstawić wynik

Pokaż pracownikowi tabelę wyników pogrupowaną według obszaru. Dla każdego niepowodzenia podaj prawdopodobną przyczynę i kto może ją usunąć. Typowe przypadki:

| Objaw | Prawdopodobna przyczyna | Kto naprawia |
|---|---|---|
| `az account show` nie działa | brak logowania Azure CLI | pracownik: `az login` |
| brak aktywnego profilu `pac` | brak `pac auth create` | pracownik |
| 401 przy BAP lub Power Automate | token Azure CLI dla tej usługi odrzucony | administrator Entra ID, ewentualnie własna rejestracja aplikacji |
| 403 przy liście flow | użytkownik nie jest współwłaścicielem flow ani administratorem środowiska | administrator Power Platform |
| 403 przy Dataverse | brak użytkownika lub roli w środowisku | administrator Power Platform |
| 404 przy `flowruns` | tabela historii uruchomień niedostępna w środowisku | do wyjaśnienia, diagnoza przez API Power Automate |
| 403 przy „Graph: biblioteka dokumentów” (witryna 200) | konto ppcall (wiersz „konto ppcall”, zwykle konto serwisowe, nie konto z przeglądarki) nie ma dostępu do biblioteki; jeśli ma, tokenowi Azure CLI brakuje zakresów Sites/Files | właściciel witryny: odczyt dla konta ppcall; potem administrator Entra ID: własna aplikacja |
| 401 przy „REST: witryna” (`invalid_request`) | SharePoint REST nie przyjmuje tokenu aplikacji Azure CLI; to nie jest brak uprawnień | nic do zrobienia teraz: pliki czytamy przez Graph; REST po wdrożeniu własnej aplikacji Entra |
| 401 lub 403 przy Fabric | brak roli w obszarze roboczym albo blokada API w ustawieniach dzierżawy Fabric | administrator Fabric |

`ppcall` działa na koncie, na którym zalogowano Azure CLI w konfiguracji dyżuru. To, co pracownik widzi w przeglądarce na swoim koncie, nie mówi nic o dostępie `ppcall`. Przy odmowie SharePoint `ppcall get` sam dopisuje wyjaśnienie z nazwą konta.

Nie próbuj samodzielnie nadawać uprawnień, logować się ani zmieniać konfiguracji. Przygotuj pracownikowi listę rzeczy do załatwienia z administratorami, z dokładną treścią błędu z raportu.

Jeśli brakuje konfiguracji (adresy Dataverse, ID środowisk), poproś pracownika o uzupełnienie ustawień pluginu w `/plugin` i ponowne uruchomienie sesji.

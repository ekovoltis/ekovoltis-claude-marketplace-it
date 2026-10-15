# pp-support 0.1.0

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
│   └── run-py                      uruchamianie hooków, blokada przy braku Pythona
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
| w planie: DELETE, argumenty delete/remove/purge/secret, zmiana definicji flow, hosty spoza listy | usuwanie i zmiany logiki robi człowiek, logika przez ścieżkę deweloperską (SWO) |

Dozwolone hosty dla tokenów: Graph, ARM, Power Automate, BAP, Power Platform API, Fabric, środowiska Dataverse (`*.crm*.dynamics.com`), SharePoint (`*.sharepoint.com`). Na inne adresy `ppcall` nie wyśle tokenu.

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

Katalog `zgloszenia/` może zawierać dane klientów odczytane z Dataverse. Trzymaj go lokalnie albo w miejscu z kontrolą dostępu, nie w ogólnodostępnym repozytorium.

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

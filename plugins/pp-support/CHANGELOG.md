# Zmiany w pp-support

## 1.4.2 (2026-11-09)

Decyzja Michała Kaszty z 2026-11-09 (przegląd bezpieczeństwa 1.4.1, opcja a).

- `flow-patch`: zmiana `runAfter` akcji, od której zależy adres, nagłówki, uwierzytelnienie albo połączenie akcji wysyłającej (`sensitive_hit` po `defines`), jest odrzucana jak każda inna zmiana takiej akcji. Wcześniej `runAfter` było z tej kontroli wyjęte, więc zamiana kolejności dwóch akcji ustawiających tę samą zmienną adresu (np. `SetGood` i `SetEvil`) albo zmiana statusu w `runAfter` wybierała inną, już istniejącą wartość adresu. `flow-definition` odrzucało przestawienie takich akcji już wcześniej (test regresji dodany). Zmiany `runAfter` innych akcji, także w ITS-418 (plan A), przechodzą jak dotąd.
- Scope obejmujący akcję ustawiającą wartość adresu też jest wrażliwy (jak If, Switch, Until, Foreach), więc przestawienie takiego zakresu jest odrzucane.
- Kolejność wykonania (decyzja Michała Kaszty z 2026-11-09, opcja b): `flow-patch` i `flow-definition` porównują w starej i nowej definicji relację „wykonuje się przed” między każdą akcją ustawiającą wartość wrażliwą a każdą akcją wysyłającą oraz między akcjami ustawiającymi tę samą wartość (z uwzględnieniem zakresów: Scope, warunki, pętle). Zmiana tej relacji jest odrzucana, także gdy dokonuje jej akcja niewrażliwa (przepięcie `runAfter` samej akcji Http, akcja pośrednicząca przy równoległej gałęzi). Pary są brane tylko wtedy, gdy adres danej akcji wysyłającej zależy od wartości ustawianej przez tę akcję. Gdy akcja ustawiająca i wysyłająca (albo dwie ustawiające) wykonują się równolegle, krok nie może zmienić ani dodać akcji, od której zależy czas wykonania obu gałęzi (przodkowie i `runAfter` w zakresie: Wait, Until, pętla, także akcje wewnątrz przodka). Akcje pośredniczące, których wynik trafia do adresu (np. Compose czytający zmienną z adresem), są sprawdzane jak akcje wysyłające. Znane ograniczenie: zmiana statusów w `runAfter` akcji pośredniczącej (np. `Succeeded` na `Failed`), która decyduje, czy akcja ustawiająca w ogóle się wykona, nie jest wykrywana.

## 1.4.1 (2026-11-09)

ITS-418, decyzja Michała Kaszty z 2026-11-09.

- Strażnik zależności (`sensitive_refs`): reguła „item() w pętli” działa tylko wtedy, gdy wartość, od której zależy adres, nagłówki, uwierzytelnienie albo połączenie akcji wysyłającej, zawiera `item()`, `items('<pętla>')` albo `iterationIndexes('<pętla>')`. Sprawdzane są pola akcji wysyłającej i treść każdego źródła w domknięciu (Compose, zmienna ustawiana w pętli). `item()` wskazuje najbliższą pętlę `foreach` obejmującą akcję z wyrażeniem (w Select, Filter array i Table tylko w polu `from`). Dla tak dodanej pętli śledzone jest tylko jej wyrażenie `foreach`, bez akcji w środku. Odwołania po nazwie (`result`, `outputs`, `body`, `actions`) nadal obejmują cały zakres. Wcześniej każdy konektor w pętli blokował zmiany wszystkich akcji w tej pętli (ITS-418: plan A i krok `flow-patch` na `$expand` i zapytaniu SQL były odrzucane).
- `flow-patch` w zapytaniu SQL (`ExecutePassThroughNativeQuery*`, parametr z `query`): gdy stare zapytanie jest tylko odczytem według parsera `ppcall sql`, nowe też musi nim być. Wyrażenia `@{…}` są przy sprawdzaniu zastępowane stałą, a jeden końcowy średnik pomijany; nowe zapytanie w postaci całego wyrażenia jest odrzucane. Zapytania, które już zmieniają dane, zachowują się jak dotąd. `plan-check` oznacza każdą taką zmianę wierszem „ZAPYTANIE SQL”.
- Po przeglądzie bezpieczeństwa:
  - Nowe wyrażenie `@{…}` w zapytaniu SQL przechodzi tylko wtedy, gdy było już w starym zapytaniu w tym samym miejscu (w literale albo poza nim) albo operuje wyłącznie na danych: odwołania po nazwie do zmiennych i wyników akcji (bez `triggerBody()` i `triggerOutputs()`), funkcje z listy `SQL_EXPR_DATA_FUNCS`, literały tylko z cyfr, spacji, przecinków, kropek, nawiasów i parzystej liczby apostrofów. Wyrażenie nie może być sklejone z tekstem polecenia (`DEL@{…}ETE`, `-@{…}`); parser sprawdza zapytanie także z wyrażeniami usuniętymi. `@{'1; DELETE …'}`, `concat` z tekstem polecenia, `workflow()` i formaty tekstowe są odrzucane.
  - Parametry akcji SQL inne niż tekst zapytania (`server`, `database`) to adres: `flow-patch` ich nie zmienia, a ich źródła są wrażliwe (luka sprzed 1.4.1).
  - Odwołanie poprzedzone nową linią albo tabulatorem (`@{\nitem()}`, `@{\tvariables('x')}` w zapisie JSON) jest widoczne dla strażnika (luka sprzed 1.4.1).
  - Wyrażenia warunków i pętli (If, Switch, Until, Foreach), które obejmują akcję ustawiającą wartość dla adresu, są wrażliwe.
  - Źródła wartości wstawianych do zapytania SQL (decyzja Michała Kaszty z 2026-11-09): `plan-check` oznacza krok, który zmienia akcję albo zmienną, z której wartość trafia do zapytania akcji SQL (`[zmienia dane wstawiane do zapytania SQL: …]`), w `flow-patch` i `flow-definition`. Krok jest odrzucany, gdy nowa stała wartość w takim źródle (tekst poza `@{…}` i literały w wyrażeniach) zawiera `;`, apostrof albo słowo zapisu (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `EXEC`). Wartości wyliczane w czasie działania z danych (np. z Dataverse albo z wejścia wyzwalacza) to znane ograniczenie: strażnik ich nie widzi.
  - Po trzecim przeglądzie: literał z apostrofem w nowym wyrażeniu tylko jako całość w `if`, `coalesce`, `concat` (bez `substring('''''', 0, 1)`); sklejenie sprawdzane także po wyrażeniu (`@{…}-`); położenie wyrażenia rozróżnia polecenie, literał, nazwę w `[]`/`""` i komentarz. Źródła wstawek liczone ze starej i nowej definicji, a nowe wyrażenie w zapytaniu sprawdza stałe w akcjach wbudowanych, z których bierze wartość. Stała w źródle sprawdzana także po sklejeniu literałów (`DEL@{'ETE'}`); wyrażenia w nowej wartości źródła tylko z funkcji na danych (bez `base64ToString`, `uriComponentToString`, `json`, `toUpper`); lista słów zapisu obejmuje też `INTO`, `GRANT`, `REVOKE`, `DENY`, `DECLARE`, `WAITFOR`, `BULK`, `COPY`, `OPENROWSET` i podobne. Stałe sprawdzane są w akcjach wbudowanych; wynik konektora (np. `$expand` w List rows) to dane z czasu działania.
  - Po czwartym przeglądzie: w nowym wyrażeniu w zapytaniu funkcje liczbowe (`sub`, `add`, `length` itp.) tylko jako argument liczbowy (długość i pozycja w `substring`, porównania), bo liczba ujemna wstawiona jako tekst daje `--`; dwa wyrażenia obok siebie (`}@{`) są sklejeniem. W źródłach wstawek literały w wyrażeniach tylko z cyfr, spacji, przecinków i kropek, a stała nie może zawierać `--`, `/*`, `*/`, `"`, `[`, `]`, nowej linii ani zaczynać się lub kończyć na `-`, `/`, `*`.
  - Po piątym przeglądzie: nowe wyrażenie w zapytaniu tylko w poleceniu albo w literale `'…'` (w nazwie w `[]`/`""` i w komentarzu tylko wyrażenia ze starego zapytania); `split` tylko bezpośrednio w `first`, `last`, `join`, `length`, `empty` (tablica wstawiona jako tekst to JSON z `]` i `"`); źródło wstawki nie może dostać wartości będącej tablicą albo obiektem.
  - Do osobnej decyzji: inne operacje konektora SQL (np. `ExecuteProcedure_V2`: nazwa procedury, `server`, `database`) nie są objęte regułą; zachowanie `@@{` w środku tekstu nie zostało sprawdzone na Power Automate.
  - Odczyt odwołań z surowych tekstów wartości (zamiast zapisu JSON), więc nazwy z `"` albo `\` są porównywane tak samo jak w `defines()`.

## 1.4.0 (2026-11-09)

ITS-426, decyzja Michała Kaszty.

- `ppcall sql <serwer> <baza> "<SELECT ...>" --out <katalog zgłoszenia>/plik.json [--max-wierszy N] [--limit-czasu S]`: odczyt z hurtowni Fabric bez planu i akceptacji. Endpointy tylko z nowego ustawienia `fabric_sql_endpoints` (`<serwer>/<baza>`, hosty SQL Fabric), na start hurtownia `ekovoltis`.
- Zapytanie sprawdzają strażnik i `ppcall` przed wysłaniem: najpierw usuwają komentarze (także zagnieżdżone) i literały, potem przepuszczają tylko jedno `SELECT` albo `WITH … SELECT`. Odrzucane są `;`, kilka poleceń, sekwencje ODBC `{…}`, znaki niewidoczne oraz słowa zmieniające dane, schemat i stan sesji w dowolnym miejscu (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `EXEC`, `SELECT INTO`, `OPENROWSET`, `OPENQUERY`, `OPENDATASOURCE`, `WAITFOR`, `DECLARE`, `SET`, transakcje i inne).
- Połączenie przez `pyodbc` i ODBC Driver 18 z `ApplicationIntent=ReadOnly`, w transakcji zawsze wycofywanej. Token dla `database.windows.net` ppcall przekazuje sterownikowi jako atrybut połączenia, nigdy na ekran, do plików ani do dziennika. Brak `pyodbc` albo sterownika kończy się czytelnym komunikatem. Przy odmowie logowania komunikat podaje konto Azure CLI (e2vitservice).
- Wynik tylko do pliku w katalogu zgłoszenia z `_DANE_OSOBOWE` w nazwie; na ekranie liczba wierszy, kolumny i trzy pierwsze wiersze. Limit 5000 wierszy (do 50000) i 120 s (do 600 s). Dziennik audytu: zapytanie i liczba wierszy, bez danych.
- Strażnik blokuje `sqlcmd`, `Invoke-Sqlcmd`, `bcp` i inne klienty SQL.
- `sprawdz-dostepy`: kontrola sterownika i `SELECT TOP 1` na każdym endpoincie.
- Po przeglądzie bezpieczeństwa:
  - Słowo sklejone z liczbą (`WHERE 1=1DELETE …`, `1=1COMMIT`, `0xAAGRANT`, `$1DROP`) jest rozdzielane jak w lekserze T-SQL, więc nie omija listy zakazanych słów. Na liście są też `ADD`, `GET` i hinty blokad (`TABLOCKX`, `HOLDLOCK` i inne). Komentarz `--` kończy się także na samym `\r`, a separatory wiersza Unicode są odrzucane.
  - Tylko hosty `*.datawarehouse.fabric.microsoft.com` (bez Fabric SQL database). Nazwy trzy- i czteroczęściowe tylko z bazą z listy (bez odczytu innych baz hurtowni i serwerów połączonych); nazwa w `[]` albo `""` jest jednym identyfikatorem (`[ekovoltis.x].dbo.t` i `[x ekovoltis].dbo.t` to inne bazy).
  - Dziennik audytu zawiera pełny tekst zapytania (decyzja Michała Kaszty); filtry z danymi osobowymi zostają w `audyt.jsonl`.
  - `ppcall` sprawdza w `@@OPTIONS`, że sesja jest w trybie transakcji niejawnych; bez tego zapytanie nie jest wykonywane.
  - Strażnik dopuszcza polecenie `ppcall` bez pytania tylko z argumentami, które czyta tak samo jak powłoka. Odrzuca podstawienia (`$(...)`, `` `...` ``, `${...}`, `<(...)`), `$'...'`, komentarze `#`, nowe linie, typograficzne i niezamknięte cudzysłowy, w PowerShell `$`, nawiasy, here-stringi i `--%` poza `'...'`, a także `\`, wzorce i nawiasy klamrowe poza cudzysłowem w bash, odstępy inne niż spacja i każde przekierowanie poza `2>&1`, `>&2`, w bash `>/dev/null`, w PowerShell `2>$null` (luka sprzed 1.4.0, np. `ppcall get … > plan.md`); w `ppcall sql` także zmienne powłoki. `>&` i `&>` nie dzielą już polecenia na części.
  - Odczyt `pac` i `az` przechodzi bez pytania tylko przy tej samej prostej składni; z podstawieniem albo przekierowaniem (`pac org list $(…)`, `pac org list > plik`) Claude Code pyta człowieka (luka sprzed 1.4.0).
  - Klienci SQL są rozpoznawani jako program (także pod `env`, `timeout`), a nie po słowie w dowolnym miejscu; doszły polecenia `*-Sql*` i `*-Dba*` z PowerShell.

## 1.3.0 (2026-11-04)

ITS-418, decyzja Michała Kaszty z 2026-11-04 (wariant B).

- `flow-patch`: wyjątek dla nazwy pliku czytanego przez akcję SharePoint tylko do odczytu (`GetFileContentByPath`, `GetFileMetadataByPath`). Zmienna albo tekst `path` może zmienić sam ostatni segment ścieżki przy stałym folderze i witrynie, gdy wartość nie trafia do żadnej innej akcji, a stara i nowa wartość to sama nazwa pliku z tym samym rozszerzeniem. `plan-check` i `exec` sprawdzają przez Graph, czy nowy plik istnieje (404 odrzuca); bez dostępu wymagana jest sekcja „Dowód istnienia pliku”. Krok jest oznaczony w `plan-check` i w dzienniku wykonania.
- `dataset` jest kluczem zabronionym w `flow-patch` i `flow-definition`. W akcjach zapisu, przeniesienia i usuwania SharePoint wszystkie parametry poza treścią pliku liczą się jako adres, więc zmiennej użytej w `CreateFile` albo `DeleteFile` nie zmieni się krokiem planu.
- Po przeglądzie bezpieczeństwa:
  - Wyjątek nie działa, gdy wynik akcji odczytu trafia do adresu, identyfikatora albo uwierzytelnienia innej akcji (np. `id` w `DeleteFile`, adres Http), także przez Compose. Nie działa też, gdy wynik akcji ustawiającej zmienną czyta inna akcja, i przy kolizji nazwy akcji z nazwą zmiennej.
  - Wyjątek obejmuje tylko `OpenApiConnection` z dokładnym `apiId` SharePoint.
  - Nazwa pliku nie może zawierać znaków niewidocznych, sterujących ani zastępczych (Unicode), nie może też być nazwą zarezerwowaną Windows.
  - W akcjach zapisu SharePoint `flow-patch` zmienia tylko treść (`body`). Spłaszczone klucze OpenApiConnection (`parameters/method`, `parameters/uri`) są sprawdzane segment po segmencie.
  - `flow-diff` maskuje wartości pod `authentication`, `headers` i kluczami z hasłami (na ekranie i w pliku) i tworzy plik wyniku jako nowy.
  - Kontrola istnienia pliku traktuje błędy sieci jako „nie sprawdzono”.
  - Wynik akcji odczytu i wszystko z niego wyliczone może trafić tylko do akcji wbudowanych i do treści (`body`) zapisu SharePoint. Inne użycie, np. odbiorca maila, wyłącza wyjątek.
  - Pętla (Foreach, Until) po wartości wyliczonej z pliku i `result()` zakresu z taką wartością liczą się jako jej użycie. Wynik `flow-diff` ma unikalną nazwę także przy dwóch porównaniach w tej samej sekundzie.
  - `url`, `baseurl`, `siteurl` i `resourceuri` są kluczami zabronionymi (np. `request/url` w akcji HTTP z Entra ID, luka sprzed 1.3.0). `result('<zakres>')` liczy się jako odwołanie do wszystkich akcji w zakresie.
- `ppcall flow-diff <ID> <adres Dataverse> <workflowid> <plik>`: porównanie definicji po zmianie ręcznej, z różnicami merytorycznymi osobno od zmian serializacji designera i ze sprawdzeniem `connectionReferences`; wynik w katalogu zgłoszenia.

## 1.2.5 (2026-11-04)

- Strażnik, rozmowa dyżurna: grupa opcji bash zawierająca `o` i `c` (`bash -oc pipefail "…"`, `-eoc`) jest odrzucana przed rozpoznaniem `-c`, bo kod jest wtedy w drugim argumencie, nie w pierwszym (poprawka nie zdążyła do #12).

## 1.2.4 (2026-11-04)

- Strażnik, rozmowa dyżurna: rekurencja w PowerShell uruchomionym bez `-Command` (`powershell Get-ChildItem ~ -Recurse`; Windows PowerShell 5.1 traktuje argumenty jak polecenie) jest blokowana. Opcje `pwsh`/`powershell` są rozpoznawane po prefiksie, jak w PowerShell (`-Execution Bypass`, `-ExecutionPolicy:Bypass`, `-cwa`, `/command`), a nieznana opcja przed poleceniem kończy się odmową zamiast zgadywania, gdzie polecenie się zaczyna. `-File skrypt.ps1` nie jest traktowane jak polecenie.
- Argumenty powłoki czytane po kolei: flaga `-c`/`-Command` liczy się tylko przed pierwszym argumentem pozycyjnym (`bash skrypt.sh -c x` to skrypt). `-WorkingDirectory`/`-wd` zmienia katalog, względem którego sprawdzana jest rekurencja. Kod ze stdin (`-File -`, `-Command -`, `bash -s`, `… | bash`) i brak kodu po `-c` kończą się odmową, bo nie da się ich sprawdzić. Opcje bash z `+` (`+o pipefail`) i opcje PowerShell zapisane przez `/` (`/wd`, `/WorkingDirectory:`) są rozpoznawane.

## 1.2.3 (2026-11-04)

- Nazwy kopii definicji są unikalne w całym stanie, a nie tylko w zgłoszeniu. Dwie kopie tego samego flow z różnych zgłoszeń zapisane w tej samej sekundzie miały tę samą nazwę, przez co odnośnik `backup/<plik>` w planie wycofania z innego zgłoszenia był niejednoznaczny i nie działał. Przy kilku kopiach o tej samej nazwie (sprzed poprawki) wybierana jest jedyna o skrócie z planu.
- Ochrona stanu porównuje także rozwiniętą ścieżkę stanu (nazwy 8.3, np. `RUNNER~1`, i dowiązania). Bez tego Read i Grep na stanie przechodziły, gdy ścieżka stanu była podana w postaci 8.3 (CI na Windows).
- W rozmowie dyżurnej rekurencja w zagnieżdżonej powłoce (`bash -c "grep -r … ~"`, `pwsh -Command "Get-ChildItem ~ -Recurse"`) jest blokowana; zakodowane polecenie (`-EncodedCommand`) też, bo nie da się go sprawdzić.
- Przenoszenie starych kopii pomija zgłoszenie, którego katalog (a nie tylko `backup/`) jest dowiązaniem albo junction.
- Testy dysku jako ścieżki (`/c`, `C:`) tylko na Windows. Testy 1.2.2 nie przechodziły w CI na Linuksie ani na Windows.

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

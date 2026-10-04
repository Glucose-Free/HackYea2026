# Silnik polityki (checkpoint 2)

Python 3.12. Rdzeń (`middleware.py`) i serwer stdio korzystają tylko z biblioteki
standardowej, serwer HTTP z SDK `mcp`. Żaden z nich nie zna domeny.
Demo korzysta z domeny banku (`examples/bank_demo.py`); `examples/aml.py`
i `examples/transactions.py` to mniejsze domeny, które pokazują, że jeden silnik
obsługuje kilka katalogów narzędzi naraz.

## Podział odpowiedzialności

| Plik | Odpowiedzialność |
|---|---|
| `policy_engine/middleware.py` | Interpreter dodatniego Datalogu, historia odbiorcy, preflight/postflight, audyt i kontrakty narzędzi |
| `policy_engine/mcp_stdio_server.py` | JSON-RPC, lifecycle MCP, stdio i katalog pobierany z rejestru middleware |
| `policy_engine/mcp_http_server.py` | Serwer MCP (streamable HTTP) dla gatewaya: użytkownik z `_meta` każdego wywołania, wielu użytkowników w jednym procesie |
| `examples/bank_demo*.py` | Domena demo: narzędzia banku, zapytania SQL, generator danych i reguły |
| `examples/aml.py` | Narzędzia AML, schematy odpowiedzi, planowanie ujawnień i fikcyjne dane |
| `examples/aml_rules.json` | Przykładowe reguły AML |
| `examples/transactions.py` | Narzędzia ujawniające jedną stronę transakcji i fikcyjne dane |
| `examples/transaction_rules.json` | Reguła zabraniająca poznania obu stron tej samej transakcji |

Rdzeń nie importuje przykładów, nie zna nazw ich narzędzi, relacji, ról ani
formatów identyfikatorów. Nie ma domyślnego katalogu narzędzi ani domyślnej
polityki biznesowej. Zaufany host przekazuje jawny `ToolRegistry`.

## Rozszerzanie

**Nowa reguła dotycząca istniejących relacji:** dodaj regułę do zarządzanego przez
administratora pliku JSON. Middleware wczytuje i waliduje politykę przy każdym
nowym wywołaniu. Nie trzeba zmieniać rdzenia ani transportu MCP.

**Nowe narzędzie, pole danych lub relacja:** dodaj definicję `ToolDefinition`
w module swojej domeny. Każda definicja zawiera:

| Element | Kontrakt |
|---|---|
| `name`, `description`, `input_schema` | Unikalna nazwa i zamknięty schemat argumentów dla MCP |
| `allowed_roles` | Jawny zbiór uprawnionych ról; brak automatycznego wildcardu |
| `validate_arguments(args)` | Czysta funkcja, odrzuca nieobsługiwane argumenty i zwraca znormalizowany słownik |
| `plan_disclosure(args, history)` | Opisuje wszystkie możliwe fakty, używając tylko argumentów i wcześniej ujawnionej wiedzy |
| `validate_response(args, result)` | Ściśle sprawdza rzeczywistą odpowiedź i zwraca `(data, facts)` |

`ToolRegistry` jest wspólnym katalogiem dla middleware i MCP. Nowe narzędzie
po rejestracji pojawia się automatycznie w `tools/list` dla uprawnionej roli.
Nie ma drugiej listy narzędzi utrzymywanej w serwerze. Zduplikowane nazwy są
odrzucane. Dodawanie rejestracji i zmiana polityki nie są narzędziami modelu.

Walidatory w adapterze muszą egzekwować schemat i semantykę danych. Rdzeń
ogranicza rozmiar i dopuszcza wyłącznie poprawny JSON, lecz nie jest uniwersalnym
walidatorem całego JSON Schema. `input_schema` służy opisowi narzędzia dla MCP;
kontrola wykonania nie może polegać wyłącznie na walidacji przez klienta.

Przykład składania katalogów przez zaufany host:

```python
from policy_engine.middleware import ToolRegistry
from examples import aml, transactions

registry = ToolRegistry([
    *aml.tool_definitions(),
    *transactions.tool_definitions(),
])
```

Role nadal są sprawdzane osobno dla każdego narzędzia. W przykładach
`restricted_analyst` jest rolą AML, a `transaction_reader` rolą transakcyjną.
Host musi nadać właściwe uprawnienia we własnych rejestracjach, aby jego role
mogły korzystać z obu domen. Sama obecność narzędzia w rejestrze nie nadaje dostępu.
Plik polityki takiego wdrożenia powinien zawierać reguły wszystkich aktywnych domen
oraz ewentualne reguły łączące relacje między nimi. Do łączenia domen używaj
spójnych identyfikatorów podmiotów i nazw relacji; unikaj przypadkowych kolizji.

## Fakty i planowanie

Wiedza ma postać `knows(recipient_scope, subject, relation, value)`.
Relacje są dowolnymi nazwami domenowymi, np. `transaction.sender`,
`document.classification` lub `profile.location`. Liczby nie są wymagane.
Reguły wyprowadzają kolejne `knows/4` lub `violation/3`.

Przykładowa reguła Datalog w zapisie poglądowym:

```prolog
violation(U, T, both_parties) :-
    knows(U, T, 'transaction.sender', Sender),
    knows(U, T, 'transaction.recipient', Recipient).
```

W pliku JSON zmienne zapisuje się jako `?u`, `?s` itd. Wszystkie atomy reguły
muszą zachować odbiorcę `?u`; wszystkie zmienne głowy muszą występować w ciele.
Reguły są dodatnie, bez negacji, agregacji i dowolnych funkcji. Plik musi zawierać
przynajmniej jedną aktywną regułę blokującą. Przykłady pokazują pełny format JSON.

Nieznane jeszcze wartości opisuje `ValueDomain`, bez odczytu prywatnych danych:

```python
PlannedFact(transaction_id, "transaction.sender", ValueDomain())
PlannedFact(document_id, "document.classification",
            ValueDomain(values=frozenset({"public", "private"})))
PlannedFact(ValueDomain(pattern=r"person-[0-9]+"), "profile.location", "disclosed")
```

Domena może wystąpić w podmiocie, relacji lub wartości. Rdzeń uwzględnia
pasujące znane stałe, literały nowych reguł oraz wspólny symbol nieznanej wartości.
To konserwatywne przybliżenie: może blokować bezpieczne operacje, ale nie podejmuje
decyzji o preflight na podstawie rzeczywistej tajnej wartości. W przeciwnym razie
sama odmowa mogłaby ujawniać tę wartość.

Po wykonaniu fakty z odpowiedzi muszą być objęte deklarowanym planem.
Nieopisany fakt blokuje całą odpowiedź. Adapter nadal jest częścią zaufaną:
rdzeń nie potrafi automatycznie odkryć, że adapter pominął wrażliwy fakt lub
niepoprawnie przypisał znaczenie pola. Pełna semantyka każdego udostępnianego pola
musi być opisana w adapterze, również dla późniejszych nowych reguł.

## Integracja z backendem

```python
from policy_engine.middleware import (
    KnowledgeStore, MCPRequest, PolicyConfigStore, PolicyMiddleware, TrustedPrincipal,
)
from examples.transactions import build_registry, demo_executor

# Wyłącznie po uwierzytelnieniu w backendzie. Wartości poniżej są demonstracyjne.
principal = TrustedPrincipal("bank-a", "employee-17",
                             role="transaction_reader", dataset_id="stable-bank-data")
middleware = PolicyMiddleware(
    KnowledgeStore("knowledge.sqlite3"),
    PolicyConfigStore("examples/transaction_rules.json"),
    build_registry(),
)
result = middleware.handle(
    MCPRequest("request-1", "conversation-a", tool_name="get_transaction_sender",
               arguments={"transaction_id": "wire-transfer-17"}),
    principal=principal,
    execute=demo_executor,  # Zastąp zaufanym odczytem z banku, przypisanym do principal.
)
```

Executor sprawdza bazowe ACL, organizację i dostęp do zasobu dla tego samego
uwierzytelnionego odbiorcy. Musi wykonywać wyłącznie odczyty i mieć timeout
na poziomie bazy/API. Preflight poprzedza executor, ale odrzucenie odpowiedzi
w postflight nie cofnęłoby skutków zapisu.

`BEGIN IMMEDIATE` obejmuje historię, preflight, odczyt danych, postflight, zapis
zatwierdzonych faktów i audyt. Odpowiedź trafia do wywołującego po commit.
Odmowa nie dodaje faktów do wiedzy. Wyjątki i odmowy mają ogólny komunikat;
zaufany administrator może odczytać dowód przez `store.audit_events(principal)`
z rolą `security_admin`, wyłącznie w swojej organizacji.

## Uruchamianie MCP

Serwer obsługuje profil stdio protokołu `2025-11-25`: `initialize`,
`notifications/initialized`, `ping`, `tools/list`, `tools/call`. Klient musi
zaakceptować wersję zwróconą w negocjacji. Jedna linia UTF-8 zawiera jeden
komunikat JSON-RPC; brak nagłówków `Content-Length`. Ten serwer nie ma transportu HTTP (ma go `mcp_http_server.py`),
resources, prompts ani zadań asynchronicznych.

Przykłady z fikcyjnymi danymi, z katalogu projektu:

```bash
python3 -m examples.transactions --db demo_transactions.sqlite3 --rules examples/transaction_rules.json
python3 -m examples.aml --db demo_aml.sqlite3 --rules examples/aml_rules.json
```

Klient MCP uruchamia wybrane polecenie jako subprocess. Moduły przykładów mogą
jawnie utworzyć brakującą przykładową politykę. Ogólny serwer nigdy jej nie tworzy:

```bash
python3 -m policy_engine.mcp_stdio_server --backend examples.transactions:create_demo_backend --db demo_transactions.sqlite3 --rules examples/transaction_rules.json
python3 -m policy_engine.mcp_stdio_server --backend bank_backend:create_backend --db knowledge.sqlite3 --rules bank_policy.json
```

Zaufany moduł `bank_backend` i jego `create_backend()` muszą zostać
zaimplementowane w Twoim backendzie. Fabryka zwraca **trzy** elementy:
`(verified_principal, registry, recipient_bound_executor)`. Uwierzytelnienie
musi nastąpić przed utworzeniem principal; podanie user ID nie jest uwierzytelnieniem.
Launcher kontroluje moduł fabryki, środowisko, ścieżki i politykę. Parametry
konfiguracji wdrożenia nie mogą pochodzić z rozmowy z modelem.

Jeden proces/połączenie obsługuje jednego uwierzytelnionego odbiorcę. MCP nie
przyjmuje jego tożsamości, zakresu danych, identyfikatora historii ani faktów
z argumentów. `_meta` jest ignorowane przy uwierzytelnianiu. MCP nie udostępnia
zmiany/wyłączenia reguł, snapshotów ani audytu. Wyniki narzędzi zawierają
`structuredContent` i równoważny tekst JSON; odmowy mają `isError: true`.

Serwer waliduje JSON-RPC, obsługuje błędy linii i nie odpowiada na notyfikacje.
`tools/call` bez ID nie wykonuje odczytu; ponowne ID w jednym połączeniu jest
odrzucane. ID audytu jest osobnym UUID. Zwykłe wydruki z executora i fabryki są
wyciszane, aby nie obchodziły kontroli. Zaufany backend nie może pisać bezpośrednio
do deskryptora stdout ani logować prywatnych wyników do strumieni klienta.

## Ograniczenia

- Historia jest wspólna dla sesji tego samego odbiorcy. Jej zakres wynika
  z organizacji, użytkownika i jawnego, stabilnego dataset ID. Dodanie domeny,
  reguły lub nowego połączenia nie uzasadnia zmiany dataset ID ani nowej pustej bazy.
- Reguły wielu domen działają nad tą samą historią w danym zakresie. Wartości
  relacji i identyfikatory muszą być spójne. Zmiana ich znaczenia wymaga migracji
  historycznych faktów, a nie nadania nowej etykiety w celu obejścia kontroli.
- Cache retry nie wykonuje ponownie tego samego żądania i payloadu. Zmieniony
  payload jest odrzucany; aktualne uprawnienia do narzędzia są sprawdzane także
  przed odtworzeniem cache. MCP retry po reconnect jest nowym żądaniem.
- Cache zatwierdzonych odpowiedzi zawiera dane biznesowe. SQLite jest chroniony
  uprawnieniami 0600 na POSIX; trzeba go zabezpieczać jak źródłowe dane.
- Domyślnie: do 512 reguł, 512 narzędzi, 1000 planowanych faktów na odczyt,
  10 000 faktów wnioskowania, 100 000 dopasowań i 2 s wnioskowania.
  Limity konfiguracji i rejestru można jawnie zmieniać po stronie hosta.
  Dopasowania korzystają z indeksów predykatów i związanych argumentów.
  Przekroczenie limitu blokuje operację, nie usuwa części historii.
- Więcej domen i reguł nie oznacza gotowości do dużego ruchu. SQLite serializuje
  operacje także podczas odczytu backendu. Większe wdrożenie wymaga magazynu
  z blokadą odbiorcy i dokładniejszych pomiarów wydajności silnika reguł.
- MCP ma limit wejścia 64 KiB, burst 20 wywołań narzędzi i uzupełnianie 1/s,
  oraz 10 000 ID żądań na połączenie. Limit odbiorcy obejmujący wiele procesów
  należy egzekwować w backendzie. Podczas synchronicznego odczytu serwer nie
  przetwarza kolejnego ping ani anulowania. Przekierowanie stdout dotyczy całego
  procesu: bridge nie nadaje się do osadzania w wielowątkowym serwerze aplikacji.
- Ochrona obejmuje opisane relacje i uwzględnioną wiedzę. Wcześniej wydane dane,
  publiczne mapowania i wiedzę bazową trzeba uwzględnić osobno. Nie ma tu ochrony
  dowolnego SQL, tekstu, reidentyfikacji po czasie/liczbach ani współpracy wielu osób.

## Testy

```bash
uv run pytest tests/policy_engine
```

Testy silnika obejmują dwa adaptery i ich wspólne użycie, role per narzędzie,
reguły dodane bez zmiany adapterów, nieznane wartości i podmioty, pokrycie planu,
limity, cache oraz historię po restarcie. Oba przykłady są sprawdzane przez
rzeczywisty subprocess MCP. Test 150 niezależnych reguł przechodzi przy budżecie
500 dopasowań; nie jest to benchmark wydajności.

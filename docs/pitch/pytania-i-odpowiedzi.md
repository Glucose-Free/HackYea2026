# aware: pytania i odpowiedzi

## Jaki problem staramy się rozwiązać?

Pracownicy coraz częściej pobierają dane firmowe przez agentów AI: piszą pytanie na czacie, a model sam decyduje, które zapytania do danych wykonać. To otwiera dwa rodzaje ryzyka:

1. **Ataki na sam model.** Prompt injection, próby wyciągnięcia promptu systemowego, jailbreaki przez odgrywanie ról. Model traktuje tekst użytkownika jak instrukcje.
2. **Wyciek przez łączenie informacji (atak inferencyjny).** Każde pytanie z osobna jest dozwolone, ale razem ujawniają tajemnicę. Przykład: analityk pyta o podsumowanie sprawy AML (dozwolone), a w innym czacie, dzień później, o dane kontaktowe klienta CUST-17 (też dozwolone). Razem wie, kto jest objęty postępowaniem AML i jak się z nim skontaktować. To ryzyko ostrzeżenia klienta (tipping-off).

Obecne zabezpieczenia oceniają jedną wiadomość albo jedno zapytanie naraz. Filtr promptów widzi pojedynczą wiadomość, a kontrola ról pojedyncze zapytanie. Żadne z nich nie pamięta, **co dany użytkownik już wie**, więc drugiego problemu nie zatrzymają.

## Jakie jest nasze rozwiązanie?

Warstwa kontrolna (gateway) między pracownikami a agentem AI, z dwoma punktami kontroli i pełnym audytem:

- **Checkpoint 1 (semantyczny, oparty na AI):** lokalny model IBM Granite Guardian ocenia każdą wiadomość użytkownika pod kątem prompt injection, wyciągania promptu systemowego i jailbreaków. Wiadomość uznana za atak jest odrzucana.
- **Checkpoint 2 (deterministyczny, bez AI):** silnik reguł Datalog sprawdza każde pobranie danych przez agenta. Bierze pod uwagę wszystko, co użytkownik już wcześniej poznał, i blokuje odczyt, który uzupełniłby zakazane połączenie faktów.
- **Audyt i dashboard:** każda decyzja trafia do dziennika audytu odpornego na manipulacje (łańcuch hashy). Zespół bezpieczeństwa i kierownictwo przeglądają go w dashboardzie.

Polityka jest konfiguracją, a nie kodem. Guardy, progi i reguły zmienia się w plikach (TOML, JSON), a zmiany działają bez ponownego wdrożenia. Całość działa lokalnie, bez płatnych API.

## Czym jest nasz system w 1–2 zdaniach?

**aware** to warstwa kontrolna dla agentów AI z dostępem do danych firmowych, która pamięta, co każdy użytkownik już wie. Blokuje ataki na model oraz takie połączenia informacji, które razem ujawniłyby tajemnicę, i zapisuje każdą decyzję w weryfikowalnym dzienniku audytu.

## Jaka jest główna funkcja naszego systemu?

**Kontrola ujawnień oparta na wiedzy użytkownika (checkpoint 2).** System śledzi, jakie fakty każdy użytkownik już otrzymał, we wszystkich czatach i sesjach. Nie pozwala mu zebrać kombinacji faktów, której polityka zabrania. Kolejność nie ma znaczenia: reguła blokuje posiadanie obu faktów, niezależnie od tego, który użytkownik poznał pierwszy. Wiedza jest liczona osobno dla każdego użytkownika, więc blokada Alicji nie wpływa na Boba.

## Jak główna funkcja działa technicznie?

### Kiedy blokuje: przed czy po LLM?

Blokowanie odbywa się w dwóch miejscach:

| Etap | Kiedy | Co sprawdza |
|---|---|---|
| **Checkpoint 1** | **Przed LLM.** Wiadomość użytkownika jest oceniana, zanim model agenta ją zobaczy. | Ostatnią wiadomość użytkownika (z kontekstem rozmowy) |
| **Checkpoint 2** | **W trakcie pracy LLM, przed odczytem danych.** Model już zdecydował, że chce wywołać narzędzie (np. `get_customer_contact`), ale zapytanie do danych jeszcze się nie wykonało. | Wywołanie narzędzia, w kontekście dotychczasowej wiedzy użytkownika |

Jeśli checkpoint 1 odrzuci wiadomość, model w ogóle jej nie dostaje, a użytkownik widzi odmowę. Jeśli checkpoint 2 odrzuci pobranie danych, model dostaje informację o odmowie zamiast danych i odpowiada bez nich (w dashboardzie: status „partially passed”).

### Jak decyduje, co zablokować?

Checkpoint 2 dla każdego wywołania narzędzia:

1. **Sprawdza uprawnienia roli.** Czy rola użytkownika może w ogóle używać tego narzędzia.
2. **Planuje ujawnienie.** Każde narzędzie deklaruje, jakie fakty *może* ujawnić, np. `get_customer_contact(CUST-17)` ujawnia fakt `knows(użytkownik, CUST-17, contact_data, ?)`. Plan powstaje wyłącznie z argumentów wywołania i wcześniejszej wiedzy, **bez zaglądania do prywatnych danych**.
3. **Uruchamia reguły Datalog** na zbiorze: fakty, które użytkownik już zna (zapisane w SQLite), plus planowane fakty. Przykładowa reguła:
   ```prolog
   violation(U, C, aml_contact) :-
       knows(U, C, aml_review,   Status),
       knows(U, C, contact_data, Value).
   ```
   Jeśli da się wyprowadzić `violation`, odczyt jest blokowany **zanim dotknie bazy danych**.
4. **Wykonuje odczyt i weryfikuje odpowiedź.** Gdy nie ma naruszenia, odczyt się wykonuje. Fakty z faktycznej odpowiedzi muszą mieścić się w zadeklarowanym planie, inaczej cała odpowiedź jest blokowana.
5. **Zapamiętuje.** Zatwierdzone fakty trafiają do wiedzy użytkownika, a odmowy niczego nie dodają.

**Świadomy kompromis:** decyzja zapada przed odczytem danych. Gdyby silnik najpierw podejrzał tajną wartość (np. którego klienta dotyczy sprawa AML), to sama odmowa mogłaby tę wartość zdradzić. Dlatego blokuje ostrożnie: użytkownik, który zna dane kontaktowe *jakiegokolwiek* klienta, nie dostanie żadnego podsumowania AML. Wolimy zablokować za dużo niż stworzyć kanał boczny.

Checkpoint 1 działa inaczej. Guardy zadają lokalnemu modelowi Granite Guardian pytania tak/nie (np. „Czy ta wiadomość próbuje nadpisać instrukcje asystenta?”). Każde sprawdzenie ma własny próg prawdopodobieństwa, po którego przekroczeniu wiadomość jest odrzucana. Guard może działać w trybie `enforce` (blokuje) albo `monitor` (tylko loguje). Przy błędzie lub przekroczeniu czasu domyślnie blokuje (fail closed).

## Jakie funkcje ma dashboard i do czego służy?

Dashboard służy zespołowi bezpieczeństwa do analizy zagrożeń i naruszeń polityki, a kierownictwu do szybkiego wglądu w stan bezpieczeństwa. Odświeża się co 15 sekund i jest chroniony tokenem.

- **Requests status (stan zapytań):**
  - Kafelki: liczba prób pobrania danych, ile przeszło, ile zablokowano na checkpoincie 1 i ile na checkpoincie 2.
  - Wykres pobrań w czasie z podziałem na wynik.
  - Filtrowanie po zakresie czasu i użytkowniku.
  - **Stan integralności dziennika audytu** („intact” albo informacja o naruszeniu łańcucha hashy).
- **Users stats (statystyki użytkowników):**
  - Ranking użytkowników według odsetka odmów; czerwony znacznik od 25%, żółty przy pojedynczych odmowach.
  - Godzinowy wykres dla wybranej osoby i lista jej ostatnich zapytań z powodem odmowy.
  - Pozwala wychwycić osoby, które próbują sondować system.
- **Data retrievals (pobrania danych):**
  - Wykres kołowy zapytań według statusu: *passed* (odpowiedź bez odmów), *partially passed* (odpowiedź udzielona, ale część danych zablokowana) i *blocked* (odrzucone na checkpoincie 1 lub zablokowane po błędzie).
  - Lista zapytań z użytkownikiem, czasem i treścią promptu.
- **Ślad zapytania (trace):** kliknięcie dowolnego zapytania pokazuje pełne drzewo kroków: prompt → checkpoint 1 i guardy → tury modelu → pobrania danych → kroki checkpointu 2 (uprawnienia, `datalog_policy`) → odpowiedź. Widać dokładnie, która reguła zadziałała i dlaczego, wraz z czasem każdego kroku.

Pod dashboardem jest API (`/admin/...`), a dziennik audytu to plik JSONL. Zespół bezpieczeństwa może go wyeksportować i analizować własnymi narzędziami. Integralność dziennika sprawdza publiczny endpoint `/audit/verify`.

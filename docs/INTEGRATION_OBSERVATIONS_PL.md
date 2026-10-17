# Centralny przegląd integracji (263R)

Nowy core Venoma może odczytać moduł przez opcjonalne pole
`backend.integration_provider_import` w `module.json`:
`venom_module_rider.integrations:observe_integration`.
Dotychczasowy panel `/rider` i API `/api/v1/rider` nie zależą od tego hooka.
Stary core może go pominąć; router i główny pakiet go nie importują, a wynik
jest zwykłym słownikiem, bez zależności od nowych klas schematu core.

## Wyłącznie odczyt

Funkcja asynchroniczna czyta istniejący serwis `app.state.rider`. Nie tworzy
serwisu ani klienta HTTP, nie wybiera/odtwarza urządzenia, nie wywołuje
`status()`, nie odpytuje Pi, nie ładuje modelu i nie zmienia kamery, sesji, ARM,
ruchu ani zbierania danych. Nie ma własnego workera ani zamykania sprzętu.
Przy braku serwisu wyłącznie waliduje istniejącą konfigurację urządzeń serwera.
Błędna konfiguracja daje niepełny wynik z `device_configuration_invalid`;
pusta lista daje pełne zero z `device_not_configured`. Błąd nie jest
przedstawiany jako fikcyjny robot.

Widoczne są wszystkie skonfigurowane cele. Tylko wybrane urządzenie może mieć
bieżący stan z cache. Drugie pozostaje `unverified` z `device_not_selected`,
nawet gdy skonfigurowano dla niego ten sam adres. ID jest stabilne w module;
core nadaje mu przestrzeń nazw we wspólnym przeglądzie. Wynik nie zawiera URL,
referencji i wartości poświadczeń, prywatnych odpowiedzi cache ani sesji
sterowania. Etykiety publiczne mają limit długości; wklejone adresy, URL i tekst
przypominający poświadczenia są zastępowane nazwą `Rider-Pi`.

## Pochodzenie i świeżość

Zwykłe odczyty panelu zapisują czas zakończenia i wynik, bez dodatkowej pracy
transportu. Agregacja statusu zachowuje bierną obserwację połączenia z naprawdę
wykonanych odczytów. Trafienie w cache nie odnawia dowodu. Zmiana wyboru i
zamknięcie serwisu czyszczą obserwacje poprzedniego celu.

Zewnętrzny `observed_at` to czas odczytu listy. Znacznik zasobu to czas ostatniej
zapisanej obserwacji połączenia, również nieudanej przy `offline`; nie jest nowym
pingiem ani czasem z zegara Pi. Istniejący `last_seen` serwisu nadal oznacza
ostatni udany kontakt i błędy go nie odświeżają. Wiek jest liczony zegarem
monotonicznym hosta. Po 60 sekundach stan przechodzi w `stale`, a łączność w
`unknown`. Sama konfiguracja nigdy nie daje stanu online. Demo jest jawne:
`mode=demo` i `demo_observation` nie potwierdzają działania fizycznego robota.
Mieszany wynik świeżych odczytów jest zdegradowany.

Standardowa konfiguracja dopuszcza maksymalnie 16 urządzeń. Adapter dodatkowo
ogranicza wstrzyknięty serwis do 64 wpisów; ucięta lista jest niepełna.
Timeout, współdzielenie wywołań i cache hooków należą do core.

## Możliwości a zgoda

`supported_capabilities` opisuje interfejsy modułu: sterowanie ręczne, kamerę,
wizję, próbki i nawigację. `reported_capabilities` zawiera tylko jawne fakty ze
świeżego cache urządzenia: kontrakt sterowania ręcznego i toru kamery. Bez takiego
dowodu jest `null`; lista nie zastępuje pełnej negocjacji możliwości urządzenia.

`available_capabilities` pozostaje `null`: kontakt i obecność funkcji nie
dowodzą własności zasobów, kalibracji, gotowości modelu ani zgody Pi na ruch.
Dostępność operacji nadal wyznaczają istniejące kontrakty Pi i panelu. Online
nie uzbraja robota i nie nadaje zgody na jazdę. Mapa integracji nie steruje napędami.

## Weryfikacja

`tests/test_integrations.py` obejmuje online, offline, stary pomiar, niewybrane
urządzenie, brak/błąd konfiguracji, odczyt cache, nieodnawiane znaczniki czasu,
capabilities, redakcję danych, brak skutków ubocznych, import routera bez hooka
oraz schemat i normalizację nowego core. Testy korzystają z fixture i mocków.
Polecenie sprawdzenia z istniejącym środowiskiem hosta:

```sh
make HOST=/sciezka/do/venom check
```

Aby nowe obserwacje pojawiły się na mapie, najpierw należy wydać kompatybilny
przegląd integracji w core. Firmware Rider-Pi nie wymaga zmian.

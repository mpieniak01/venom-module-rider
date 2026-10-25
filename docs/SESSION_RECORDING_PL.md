# Nagrywanie i archiwum sesji

W panelu Venom **Nagraj sesję** tworzy odrębną, trwałą sesję danych z własnym identyfikatorem. Zapis obejmuje dostępną telemetrię, polecenia operatora, próbki obrazów oraz źródłowe wideo MJPEG. Nagrywanie nie włącza kamery, modelu ani ruchu. Operator osobno włącza potrzebne źródła.

## Przebieg operatora

1. Włącz kamerę w panelu, jeżeli sesja ma zawierać obraz. Wybierz potrzebne ustawienia i model.
2. Kliknij **Nagraj**. Sprawdź identyfikator sesji i stan **Wideo: Nagrywanie**. Oczekiwanie na obraz lub brak klatek są pokazane jawnie; sam zapis telemetrii nie oznacza, że powstał film.
3. W szczegółach nagrywania można otworzyć archiwum oraz przeglądać wcześniejsze sesje bez kończenia bieżącej. Zwykła pauza ruchu również nie kończy nagrania.
4. Kliknij **Zakończ** i poczekaj na zakończenie zapisu. Dopiero po zamknięciu writera dostępne jest pobranie filmu i pełnego pakietu tej sesji.
5. Kolejne **Nagraj** tworzy kolejny identyfikator. Poprzednich sesji nie zastępuje się ani nie usuwa automatycznie. Archiwum pozostaje dostępne po odświeżeniu strony i restarcie hosta.

Dotychczasowa zasada pozostaje jawna: ukrycie lub opuszczenie karty przeglądarki, zmiana urządzenia/sesji urządzenia albo zamknięcie strony próbuje zakończyć bieżące nagranie jako przerwane. Nagrywanie nie jest usługą działającą po zamknięciu panelu. Przy awarii hosta zapisane pliki pozostają na dysku; odzyskiwanie sprawdza potwierdzone klatki i oznacza niedokończoną sesję jako przerwaną.

## Obraz, czas i limity

- MJPEG zawiera otrzymane bajty JPEG z istniejącej ścieżki RAW, bez ponownej kompresji przez recorder. Nie zapisuje dźwięku ani nie udaje MP4.
- Docelowo pobieranych jest do 5 klatek/s. Rzeczywiste czasy odbioru, liczba klatek, rozmiar, luki i identyfikacja źródła są w indeksie. Nie dopisuje się sztucznych klatek ani nie twierdzi, że zapis jest bezstratnym przechwyceniem kamery. Odtwarzacz surowego MJPEG może przyjąć własną prędkość; dokładny przebieg analizy należy odtwarzać według `frames.jsonl`.
- Próbki zdjęć są niezależne od filmu: zapisują się do 1/s, maksymalnie 150. Osiągnięcie tego limitu zdjęć nie zatrzymuje filmu.
- Wspólny limit sesji to 128 MiB dla obrazu, indeksu, zdjęć i danych. Limit archiwum wynosi 2 GiB, minimalne wolne miejsce 256 MiB, a czas sesji do 1800 s. Osiągnięcie limitu filmu daje jawny stan zatrzymania z zachowanymi dotychczasowymi klatkami; nie pozoruje ciągłego nagrania.
- Zmiana źródła, transportu lub rewizji pipeline nie miesza klatek w jednym filmie. Kamera wyłączona, stare klatki i błędy zapisu mają osobne stany. Zakończenie bez żadnej klatki jest błędem filmu, a nie udanym nagraniem.

## Pliki i eksport

Archiwum jest własnością Venoma, poza checkoutem i poza robotem. Domyślnie używa prywatnego katalogu `~/.local/share/venom/rider/samples` albo istniejącego `RIDER_SAMPLE_ROOT`. Każde wideo ma własny katalog `videos/<session_id>`:

- `capture.mjpeg` — źródłowe klatki JPEG,
- `frames.jsonl` — granice, skróty SHA-256, rzeczywiste czasy i kontekst klatek,
- `video.json` — stan, liczniki i ograniczenia nagrania.

Istniejąca lista `GET /api/v1/rider/samples/sessions` i szczegóły sesji zwracają opcjonalne pole `video`. `GET /api/v1/rider/samples/sessions/<id>/video` pobiera zamknięte nagranie; aktywne daje 409, brak klatek 404. Pakiet ZIP tej samej sesji zawiera dane i wszystkie trzy pliki wideo w `video/`, z rozmiarami i skrótami w manifeście. Uszkodzone lub aktywne nagranie nie jest eksportowane jako poprawne. Uszkodzona metadana jednej sesji nie ukrywa pozostałych sesji.

**Ograniczenie importu:** importer próbek w tej iteracji jawnie odmawia importu pakietu zawierającego wideo (`samples_video_import_unsupported`). Pełny ZIP pozostaje kompletnym, samodzielnym archiwum; importer nie pomija po cichu jego filmu.

## Sprawdzenie przed publikacją

Automatyczne regresje korzystają z tymczasowego dysku i źródła fixture: dwie sesje, restart, odzyskanie indeksu, brak kamery, zmiana źródła, luka czasowa, cofnięty czas, limity, uszkodzenie, pełny eksport i jawny brak importu wideo. Osobny scenariusz przeglądarkowy sprawdza archiwum podczas nagrania, dwa niezależne identyfikatory i listę po odświeżeniu. Nie są to testy fizycznego nagrania robota. Przed publikacją operator wykonuje ręcznie dwa krótkie nagrania, kończy każde i sprawdza obrazy oraz dane obu pakietów.

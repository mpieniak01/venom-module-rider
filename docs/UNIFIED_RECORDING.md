# Wspólny zapis badania w Venomie

W nagłówku Rider-Pi jest jeden zestaw: **Nagraj badanie**, **Zdjęcie**,
**Zakończ zapis** i niezależne **Zatrzymaj ruch**. Dolny panel tworzący odrębną
kolekcję obrazów został usunięty z podglądu. Zaawansowany autozapis zdarzeń jest
nadal dostępny w szczegółach górnej belki i używa tej samej kolekcji.

START tworzy jedną ręczną sesję motion-study-v1 z record_images=true.
Telemetria i polecenia są zbierane jak dotąd. Jeśli kamera już działa, Venom
odczytuje RAW co najwyżej raz na sekundę i zapisuje je wraz z istniejącymi
metadanymi pochodzenia. To seria klatek, nie kodowany plik wideo. Zapis działa
również poza zakładką kamery; ukrycie karty przeglądarki lub opuszczenie modułu
kończy sesję zgodnie z dotychczasowym kontraktem.

Kamera OFF nie jest włączana przez rejestrator. Jej włączenie w trakcie badania
pozwala zbierać obrazy do tej samej sesji; wyłączenie nie kończy telemetrii.
Zakończenie zapisu nie wyłącza kamery, nie wykonuje ARM i nie zmienia ruchu.
Przycisk Zdjęcie zapisuje dokładnie widoczną, zdekodowaną klatkę; bez podglądu
jest niedostępny. Nie wykonuje dodatkowej akwizycji.

Automatyczna seria ma limit 150 przyjętych żądań zdjęcia na sesję, jeden odczyt
na sekundę, bez nakładania odczytów. Po limicie telemetria trwa dalej. Zdjęcia
ręczne i jawnie uruchomiony autozapis zdarzeń nadal podlegają globalnym limitom
magazynu: 128 MiB/sesję, 2 GiB łącznie, minimum 256 MiB wolnego miejsca. Licznik
na belce pokazuje zapisane zdjęcia; błędy obrazu są widoczne w szczegółach.
Stare sesje i zwykłe kolekcje zachowują record_images=false, bez autostartu.

Zdjęcia nie zapewniają synchronizacji ekspozycji z IMU. Zachowujemy identyfikatory
źródeł, metadane jakości i niezależne zegary; nie deklarujemy pomiaru prędkości.
Cały zapis i dane pozostają na hoście Venom. Rider-Pi nie staje się archiwum.

## Walidacja wydania

- make check HOST=/home/ubuntu/venom: 1448 Python PASS, 10 istniejących skipów,
  coverage 93,32%; 222 frontend PASS, Ruff/mypy/TS/ESLint PASS.
- Po końcowym cleanup: 16 testów samplera i wspólnego zapisu PASS;
  check-web, lint i format PASS.
- Chrome Playwright na izolowanym DemoPi: jedna sesja, dwie trwałe klatki RAW
  i telemetria po zmianie zakładki, zakończenie wspólnym STOP PASS.
- Szerokości 1440/390 px: bez poziomego overflow. Fixture nie obejmuje pełnego
  stylowania hosta i jego stare endpointy automatyzacji zwracają 503; nie jest
  to pełny odbiór pozostałych funkcji. Bez ARM/ruchu ani testu fizycznej kamery.
- Kontrakt hosta: opublikowany Venom main 4ef755a6 (wspólny helper transportu).
  Brak nowych zależności, migracji bazy i zmian repo Rider-Pi.


Po recenzji RAW jest dodatkowo sprawdzany względem świeżego stanu pipeline:
camera_session, transport_epoch i revision muszą odpowiadać nagłówkom klatki.
Zmiana dowolnego pola lub zatrzymany pipeline odrzuca spóźniony obraz przed
zapisem na dysk. Dla starszego Pi bez kontraktu pipeline zachowujemy istniejącą
ścieżkę legacy i jej jawnie ograniczone metadane; nie deklarujemy identycznej
weryfikacji pochodzenia. Test browser czeka na zakończenie START/STOP po API.

Końcowy gate po recenzji: make check PASS — 1453 Python, 10 istniejących skipów,
coverage 93,34%; 222 frontend PASS. Ponowny Chrome fixture PASS: jedna zamknięta
sesja, 2 obrazy i 23 rekordy dowodów po przejściu do Pomiarów.

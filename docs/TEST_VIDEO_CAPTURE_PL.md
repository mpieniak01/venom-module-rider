# Lekki zapis testowy na Venomie

Narzędzie lokalne: `python -m venom_module_rider.video_capture`.
Nie wymaga zmian Pi, nowych pakietów ani restartu API/UI. Korzysta ze
współdzielonego natywnego strumienia `/api/v1/rider/camera/stream/raw`.
Wykonuje tylko GET: nie uruchamia kamery, analizy, jazdy ani ARM.
Kamerę i jazdę operator uruchamia osobno, istniejącymi kontrolkami.

## Zapis z PowerShell Windows
Ścieżka w prompt zaczynająca się od \\wsl.localhost nie zmienia PowerShella
w konsolę Linux. Nie uruchamiaj w nim cd /home/ubuntu/venom ani .venv/bin/python.
Z dowolnego katalogu PowerShell użyj jawnie dystrybucji aplikacji:

```powershell
wsl.exe -d Ubuntu-Venom -u ubuntu --cd /home/ubuntu/venom -- /home/ubuntu/venom/.venv/bin/python -m venom_module_rider.video_capture check
wsl.exe -d Ubuntu-Venom -u ubuntu --cd /home/ubuntu/venom -- /home/ubuntu/venom/.venv/bin/python -m venom_module_rider.video_capture record --seconds 60
```

Ubuntu-24.04 to środowisko Rider-Pi; ścieżka Venoma nie istnieje w tej dystrybucji.
Aby otworzyć właściwą konsolę Linux z PowerShell:

```powershell
wsl.exe -d Ubuntu-Venom -u ubuntu --cd /home/ubuntu/venom
```

## Zapis wewnątrz Ubuntu-Venom
Dopiero w tej konsoli Linux, z istniejącego checkoutu:

```sh
cd /home/ubuntu/venom
.venv/bin/python -m venom_module_rider.video_capture check
.venv/bin/python -m venom_module_rider.video_capture record --seconds 60
```

Program wypisze katalog zapisu w
`~/.local/share/venom/rider/test-videos/<czas-UTC>-<id>`.
Można podać nowy `--output /pełna/ścieżka`. Istniejących katalogów nie nadpisuje.
`check` zgłosi `camera_not_active_mjpeg`, dopóki operator nie włączy kamery
z istniejącym transportem MJPEG. Helper sam tego nie zmienia.
Adres `--url` musi wskazywać lokalny origin HTTP; klient nie korzysta z proxy
środowiska ani nie podąża za przekierowaniami.
Jeśli API wymaga tokenu, używa wyłącznie zmiennej procesu `RIDER_ACCESS_TOKEN`;
nie przekazuj tokenu w URL lub argumentach.

Limity: domyślnie 60 s / 128 MiB / 1000 klatek. Maksymalnie można ustawić 300 s
i 1024 MiB (`--max-mib`); limit 1000 klatek pozostaje zgodny z importem archiwum.
Zapis kończy pierwszy osiągnięty limit, brak miejsca, utrata strumienia lub
zmiana tożsamości kamery. Ctrl+C kończy zapis i zachowuje kompletne klatki.
Brak automatycznego reconnect/resume. Nagłówki i jedna klatka mają istniejące
ograniczenia parsera. Pi nie otrzymuje nowego enkodera ani drugiego producenta.

## Wynik i wyodrębnienie klatek
Katalog zawiera:
- `capture.mjpeg`: kolejne JPEG z RAW API Venoma, bez ponownej kompresji helpera;
- `frames.jsonl`: offset, rozmiar, SHA zapisanych bajtów, źródło, numer klatki,
  czas źródła, czas odbioru i widoczne luki w numeracji;
- `recording.json`: konfiguracja z początku, stan zakończenia i liczniki.

Po zakończeniu (wstaw ścieżkę zwróconą przez record):
```sh
.venv/bin/python -m venom_module_rider.video_capture extract /ścieżka/zapisu --output /nowa/ścieżka/klatek
```

Powstają `frames/*.jpg`, manifest importu istniejącego archiwum i pomocnicze
`replay-manifest.json`/`transport.json` dla dotychczasowego replay depth.
JPEG są weryfikowane SHA i wyodrębniane bez dekodowania/rekompresji;
widoczne JPG i bloby importu współdzielą dane przez hardlink.
Przykładowy import po teście:
```sh
.venv/bin/python -m venom_module_rider.samples_cli import /nowa/ścieżka/klatek
```

Import nie zmienia aktywnego modelu. Porównania modeli wykonuj po teście,
istniejącym panelem badań/API. Przebieg analizy obejmuje do 200 klatek naraz;
dla dłuższego materiału wybierz kolejne zakresy offset/count.

## Telemetria i granice wersji testowej
Helper zapisuje obrazy, metadane i początkową konfigurację. Nie uruchamia drugiej
pętli odczytu IMU/decyzji. Dla wspólnej osi ruchu/IMU użyj istniejącego przycisku
rejestracji badania w panelu; jego identyfikator można podać przez
`--study-session <32-znakowy-id>`. To odnośnik, nie automatyczny eksport telemetrii.
Sam helper nie tworzy badania ani nie gwarantuje ciągłego zapisu decyzji.

RAW API Venoma może już uwzględniać orientację i kompresję obrazu kamery.
Zachowujemy oddzielnie SHA oryginalnego źródła oraz otrzymanych bajtów RAW.
Strumień jest latest-only: wolny odbiorca lub producent może pomijać numery.
Luki są raportowane, a brak luki nie dowodzi pełnej rejestracji ekspozycji sensora.
Czas źródła nie jest potwierdzonym czasem ekspozycji. Nie dorabiamy stałego FPS
ani nie ukrywamy braków duplikowaniem klatek. MJPEG ma czasy w pliku towarzyszącym;
FPS wyświetlany przez odtwarzacz samego pliku nie jest miarą kamery.
Dodatkowa inferencja/enkodowanie MP4 podczas jazdy nie są wykonywane.

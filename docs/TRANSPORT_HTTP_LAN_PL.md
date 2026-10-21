# Jawny transport HTTP w zaufanym LAN

[English](TRANSPORT_HTTP_LAN.md)

Domyślny `transport_profile="secure"` zachowuje dotychczasowe reguły: żądania
z poświadczeniami wymagają HTTPS lub loopback, także lokalnego końca nadzorowanego
tunelu. Stare wpisy zachowują ID, wybór Demo/Prod i zachowanie. `trusted_lan`
świadomie dopuszcza HTTP wyłącznie do skonfigurowanego peer. Nie wyłącza tokenów.
HTTP nie szyfruje tokenu bearer; `peer_id` służy do ciągłości tożsamości, nie jest
uwierzytelnieniem kryptograficznym. Poza zaufaną siecią używaj HTTPS.

Prywatny wpis serwera w `RIDER_DEVICES_JSON` może mieć postać:

```json
[{"id":"robot","label":"Rider-Pi","url":"http://rider.example:8080","mode":"device","transport_profile":"trusted_lan","expected_peer_id":"operator_configured_peer_id"}]
```

Adres i ID są przykładami. Profil LAN wymaga poprawnego, jawnego
`expected_peer_id` (`[A-Za-z0-9_-]{1,64}`). Odczytaj je osobnym, kontrolowanym
kanałem. Stała tożsamość Pi przeżywa restart API i zmianę DHCP. Przeglądarka
wybiera wyłącznie ID z konfiguracji, nigdy URL ani token. `RIDER_AGENT_TOKEN`
i token compute pozostają w istniejącej prywatnej konfiguracji obu stron.

Przed użyciem API moduł odczytuje pasywne, nieuwierzytelnione
`GET /api/v1/transport/identity` Pi:

```json
{"protocol_version":1,"peer_id":"operator_configured_peer_id","service_role":"rider_pi"}
```

Rozwiązywana jest tylko skonfigurowana nazwa; brak skanowania podsieci. Adres
wyniku DNS zostaje przypięty do generacji transportu, z zachowaniem kanonicznego
Host i SNI. Niezależne pule JSON, STOP i mediów używają tego samego celu.
Po błędzie transportu próbowane są kolejne unikalne wyniki A/AAAA w kolejności
resolvera, każdy z własnym identity przed użyciem. Obce lub niepoprawne identity
oraz przekierowanie kończą próbę. Adresy IPv6 wymagające scope interfejsu są
jawnie nieobsługiwane i pomijane; scope nigdy nie jest usuwany przed połączeniem.
Zapewnij wynik A/AAAA bez scope. Brak użytecznego celu daje `pi_name_unresolved`.
Weryfikacja poprzedza pierwszy odczyt/medium generacji oraz ponownie każde
polecenie i żądanie z poświadczeniami. Preflight nie zawiera autoryzacji.
Niezgodne ID, rola/protokół, zbyt duża odpowiedź i przekierowanie blokują dalsze
żądanie. Obsługiwany jest serwer zamykający TCP po każdej odpowiedzi. Ponowna
weryfikacja wykrywa także inny peer na starym IP bez wcześniejszego błędu sieci.
Nie ma automatycznego ponawiania POST.

Pojedynczy timeout puli, połączenia lub odczytu pasywnego JSON GET nadal zwraca
błąd, lecz nie zamyka niezależnego MJPEG, gdy wspólny hub ma świeżą ramkę
z dokładnie tego zweryfikowanego upstreamu i generacji. Dowód wymaga istniejących
kontroli hasha, tożsamości źródła, rosnącej sekwencji i konserwatywnego wieku.
Wygasa, gdy ramka osiągnie niezmieniony limit przyjęcia 500 ms. Same bajty ani
powtórzona ramka nie przedłużają go. Limit połączenia nadal wynosi 500 ms.
Błędy identity, poleceń i mediów, jawny Refresh, inne awarie sieci JSON oraz
timeout bez tego dowodu nadal unieważniają całą generację. POST nie jest
ponawiany, a pilny STOP
zachowuje osobny tor i preflight tożsamości.

Awaria zamyka stare pule. Kolejne zwykłe odczyty zgodnie z zapotrzebowaniem mają
ograniczony backoff z jitterem, około 1/2/4/8/16/30 s, maksymalnie 30 s.
Przy nowej próbie nazwa jest rozwiązywana ponownie, a tożsamość weryfikowana.
Częstotliwość zwykłego pollingu pozostaje bez zmian. Pilny tor STOP omija ten
czas oczekiwania i blokadę zwykłego resolvera, zachowując własną weryfikację.
Lookup ma limit 750 ms, pilny identity 500 ms, z limitem oczekiwania na slot
100 ms. Wspólny budżet pilnego połączenia wynosi 1,25 s i obejmuje przyjęcie,
DNS oraz wszystkie preflighty kandydatów (2,25 s dla zwykłego połączenia).
Kolejny wynik DNS nie odnawia budżetu. Limit TCP connect nadal wynosi 500 ms.
Zamykanie zasobów i późniejsze żądanie STOP są osobne; nie jest to pomiar czasu
fizycznego hamowania ani zgoda na obcy peer.

**Odśwież** scala równoczesne żądania, usuwa stare obserwacje i zasoby mediów,
unieważnia oczekujące operacje, jednorazowo omija backoff i odczytuje bieżący
stan Pi. W profilu LAN nigdy nie wywołuje `reconnect_command` ani SSH, nawet gdy
stary helper pozostał w prywatnym wpisie. Profil secure zachowuje swój flow
helpera. Refresh nie zmienia ID, zapisanej selekcji, sesji panelu, kalibracji ani
zgody Pi. Nowa generacja odrzuca stare odpowiedzi i gesty; osobna tożsamość
instancji bezpieczeństwa/executora nadal pochodzi z Pi.

Wracają wyłącznie pasywne odczyty/subskrypcje. Nie wracają ARM, ruch, start
kamery, zbieranie próbek, inferencja ani scenariusze. Zachowane są TTL, lease,
sekwencje/epoki i wiek przechwycenia. Wspólny MJPEG pozostaje jednym upstreamem
z ograniczonym fan-out. Utrata transportu unieważnia oczekujące intencje rozmowy
i zbierania. STOP ruchu, zatrzymanie kamery/pętli i globalne rozbrojenie zachowują
odrębne znaczenia.

`connection.transport` zawiera profil, etap, generację, `identity_verified`, błąd
i czas do ponowienia. UI PL/EN/DE odróżnia brak nazwy (`pi_name_unresolved`),
port/połączenie (`pi_connect_failed`), timeout (`pi_connection_timeout`), obce ID
(`pi_identity_mismatch`) i brak poprawnego kontraktu identity
(`pi_identity_invalid` / `pi_identity_unavailable`). Odmowa API/uprawnień nie jest
błędem DNS. Nieznany wynik operacji pozostaje jawny. Historia CPU/RAM zachowuje
wiek i oznaczenie błędu; zgoda sterowania ze starej generacji jest odrzucana.

Panel korzysta z originu Venoma i względnych URL. Publiczny helper hosta
`createBrowserUuid` używa CSPRNG `getRandomValues`, kiedy HTTP nie udostępnia
`randomUUID`, zachowując format UUID v4. Otwarcie panelu nie przyznaje sterowania
ani nie startuje urządzeń. Mikrofon przeglądarki wymaga bezpiecznego kontekstu;
audio Pi jest osobnym torem.

Compute Pi→Venom ma własny zapisany URL i oczekiwany peer Venoma
(`service_role="venom"`), z niezmienionym bearer i kontraktem sesji. Heartbeat
nie zmienia tego wyboru. Wdrażaj zgodne zmiany Pi/core/modułu. SSH pozostaje do
serwisu; tunel jest jawnym wariantem rollback, nie częścią odzyskiwania LAN.
Rollback przywraca poprzedni profil/URL z nowymi sesjami i bez replay.

`make check HOST=<zgodny-checkout-core>` sprawdza kontrakty backendu i UI.
`tests/test_lan_transport.py` używa wyłącznie mocków i serwerów loopback: m.in.
Connection-close, obcy peer na tym samym IP, zmiana DNS, Refresh, pilny STOP i
zamykanie pul. `tests/test_lan_media_isolation.py` obejmuje także izolację świeżych
mediów, wygaśnięcie dowodu, podmianę peer i brak replay. Wynik fixture nie dowodzi
DNS telefonu, świeżości fizycznego obrazu,
jakości audio, hamowania ani oszczędności CPU. Odbiór rzeczywisty wymaga osobnego
sprawdzenia przypiętych rewizji trzech repo przed przełączeniem środowiska.

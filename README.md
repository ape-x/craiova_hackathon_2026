# Airport Recovery Live

Centru de operațiuni pentru recuperarea pasagerilor, cu FastAPI, React/TypeScript, Google Maps 3D și OpenAI. Aplicația pornește în modul live, cu inventar gol. Transportul și cazarea folosite în planificare trebuie să provină din cotații reale sau din servicii documentate de operator.

Problema inițială și cerințele pasagerilor pot fi ipoteze introduse de echipă. Grupurile generate păstrează această proveniență; nu devin identități verificate și nu pot primi confirmări externe de rezervare. Pentru pasageri reali se importă manifestul.

## Fluxul operațional

1. Deschide **Start recovery**, caută numărul de zbor și selectează exact cursa Flightradar24. Data căutării este ziua UTC a primei observări, nu neapărat data locală a plecării.
2. Înregistrează incidentul și leagă grupurile afectate. Datele inițiale pot proveni dintr-un manifest sau din cerințe de planificare introduse explicit. Grupurile trebuie să indice același zbor original; operațiunile existente sunt păstrate.
3. Din caz, pornește căutarea pentru grupurile selectate și stabilește limita de apeluri. Sol Ultrafast alege căutări eligibile, sunt cerute cotații reale de zbor, iar Decisions selectează dintre planurile validate. Alocările celorlalte grupuri sunt păstrate.
4. Verifică sursele, propunerile și cerințele rămase fără soluție. Cazarea și legăturile terestre pot fi căutate separat și completate prin cotații importate sau servicii documentate de operator.
5. Folosește comenzile pentru condițiile suportate sau pentru a porni o căutare durabilă. În cererea curentă, identifică explicit un caz existent prin ID sau printr-un zbor fără ambiguitate. Formularul cazului și comanda text au probe reale complete; sesiunea vocală cu microfon rămâne o verificare separată.
6. Pasagerul acceptă sau refuză o propunere. Acceptarea înregistrează preferința. Confirmarea rezervării necesită referințele externe pentru fiecare segment.

Căutările pe incident păstrează progresul și fiecare apel într-un registru persistent. Repetarea aceleiași cereri nu retrimite apelurile. Un rezultat necunoscut după întrerupere necesită o încercare nouă, explicită; nu este reluat automat. Căutarea automată acoperă momentan zborurile și inventarul deja documentat. Lipsa legăturilor până la hotel sau stație rămâne vizibilă. Cotațiile pot expira și nu rezervă locuri.

Incidentul reprezintă o condiție raportată de operator și nu modifică statusul furnizat de Flightradar24. Un aeroport declarat indisponibil este exclus și din segmentele intermediare ale zborurilor propuse grupurilor afectate. Grupurile cu rezervări deja confirmate necesită tratare separată de operator. Contextul de zbor are retenție limitată și se poate reîmprospăta numai pentru aceeași cursă; exporturile și backupurile omit datele temporare ale furnizorului.

Costul cazării păstrează separat cotația furnizorului și taxele obligatorii suplimentare. Taxele incluse nu sunt adăugate încă o dată. Cotațiile cu taxe lipsă sau fără conversie verificată în EUR sunt vizibile, dar nu intră în planuri cu buget complet calculat.

Hotelurile au pozițiile reale ale proprietăților și fusurile orare calculate din acele coordonate. O noapte de cazare nu implică un transfer gratuit până la aeroport. Timpul de așteptare este afișat separat; trecerea peste miezul nopții nu inventează automat o cerință de cazare în modul live.

Importul hotelier verifică orele șederii față de politica proprietății. O politică lipsă sau ambiguă și sosirea în afara intervalului publicat sunt motive explicite de excludere. Fără o limită de check-in publicată, numai ora exactă de început este acceptată automat. Confirmarea că operatorul a convenit alte ore cu proprietatea rămâne o declarație separată, nu o rezervare. Importul live pe baza politicii furnizorului a fost verificat prin interfață, fără această confirmare suplimentară a operatorului.

## Rulare locală: o singură aplicație

Configurează credențialele în `backend/.env`, fără a le publica în Git sau în chat. Păstrează fișierul existent dacă este deja configurat.

```bash
test -f backend/.env || cp backend/.env.example backend/.env
chmod 600 backend/.env
docker compose up -d --build
curl --fail http://127.0.0.1:8080/api/ready
```

Aplicația este disponibilă la **http://localhost:8080**. Backendul este accesibil prin același proxy, iar SQLite rămâne în volumul Docker. Actualizările folosesc aceeași comandă și aceeași instanță. `docker compose down` oprește serviciile; nu folosi `-v` dacă vrei să păstrezi datele.

Pentru dezvoltare și verificări locale sunt necesare Python 3.12 și Node.js 22.12+. [Instrucțiuni backend](docs/backend.md#run-locally).

## Integrări

| Configurație                                            | Funcție                                                                                                 |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `OPENAI_API_KEY`                                        | Interpretare Sol Ultrafast, selecție Decisions și voce GPT-Live                                         |
| `OPENAI_MODEL`, `OPENAI_SERVICE_TIER`                   | Implicit `gpt-6.1-sol`, `ultrafast`; fără schimbare automată de model sau tier                          |
| `OPENAI_DECISION_MODEL`, `OPENAI_VOICE_MODEL`           | Implicit `gpt-6-luna`, respectiv `gpt-live-1`                                                           |
| `FR24_API_KEY`                                          | Observații Flightradar24; necesită abonament API                                                        |
| `GOOGLE_MAPS_API_KEY`                                   | Cheie publică de browser, restricționată la origini și Maps JavaScript API                              |
| `GOOGLE_MAPS_SERVER_API_KEY`                            | Google Places și Routes, numai pe server                                                                |
| `LITEAPI_API_KEY`, `LITEAPI_ENVIRONMENT=live`           | Cotații Nuitee pentru cazare și zboruri, conform accesului contului                                     |
| `DUFFEL_ACCESS_TOKEN`, `DUFFEL_ENVIRONMENT=live`        | Furnizor alternativ pentru zboruri                                                                      |
| `DISTRIBUSION_API_KEY`, `DISTRIBUSION_ENVIRONMENT=live` | Autocare; necesită și activarea fluxului Offers                                                         |
| `APL_API_KEY`                                           | Cotații Airport Pickups London pentru transferuri în UK, în GBP per vehicul; fără comandă sau rezervare |
| `PUBLIC_BASE_URL`, `OPERATOR_TOKEN`                     | URL accesibil pasagerilor și autentificarea operatorilor                                                |

Prezența unei chei înseamnă configurare, nu acces verificat. Conturile sandbox nu furnizează inventar live. Căutările nu efectuează plăți sau rezervări. [Configurare și limite](docs/live-mode.md), [date geografice și hoteliere](docs/real-data.md), [cereri către furnizori](backend/app/providers/README.md).

Pozițiile Flightradar24 au timestamp și sursă. Nu indică disponibilitatea biletelor, capacitatea avionului sau anularea unui zbor absent din feed. Colectarea pornește explicit din Connections și folosește un buget persistent de cereri. Google Maps afișează geografia; acoperirea 3D depinde de furnizor. [Documentația hărții](docs/maps.md).

O rută rutieră calculată nu este un vehicul disponibil. Stațiile de autocar păstrează pozițiile reale; transferurile aeroport–stație trebuie obținute separat. Lipsa unui serviciu sau a accesului la furnizor rămâne vizibilă.

Cotațiile APL păstrează prețul exact în GBP per vehicul și afișează alături o estimare aproximativă în EUR, calculată pe server din cursul ECB. Politica produsului admite aceste estimări pentru comparație, cu sursa și data vizibile și o vechime maximă de trei zile calendaristice, evaluată în Europe/Berlin. Lipsa unui curs utilizabil păstrează cotația GBP și lasă estimarea EUR indisponibilă. Prețul și cursul trebuie reverificate înainte de rezervare; decontarea în EUR nu este garantată.

Estimarea nu adaugă transferul într-un itinerar sau în bugetul EUR. Obținerea unui traseu rutier datat, cu durată/sosire verificabile, și importul opțiunii APL rămân nefinalizate. Adaptorul Google TRANSIT are teste offline, dar nu este conectat la fluxul operațional; un orar fără locuri sau tarif complet nu devine inventar disponibil.

Lista de resurse distinge cotațiile expirate sau cu prospețime necunoscută de inventarul curent. Inspectorul arată ora sursei și fie valabilitatea explicită, fie fereastra locală de 120 de secunde, în UTC. Acest termen local nu este o expirare declarată de furnizor și nu anulează o rezervare înregistrată. Afișarea se actualizează fără apeluri noi către furnizori; backendul decide în continuare eligibilitatea.

## Decizii și voce

GPT-Live transmite cererea către backend. Sol Ultrafast interpretează cerințele; solverul construiește alternative și verifică familiile, timpii, capacitățile, accesibilitatea și bugetele. Decisions alege dintre variantele fezabile. Backendul reverifică versiunea înainte de aplicare și transmite actualizarea către operator și pasageri.

Solverul folosește o căutare limitată: validează constrângerile, fără a demonstra optimalitatea globală. Microfonul necesită HTTPS sau localhost. Confirmarea vocală de pornire anunță numai cererea înregistrată; progresul și propunerile apar în cazul corespunzător. Limita implicită a comenzii este de opt apeluri în total, incluzând interpretarea; formularul cazului permite implicit opt apeluri ale căutării. Interpretarea cu rezultat necunoscut nu este repetată automat. Erorile AI nu sunt înlocuite cu răspunsuri prestabilite.

## Structura proiectului

```text
backend/app/
  main.py          API, autentificare, SSE, colectare partajată
  models.py        contractele validate
  seed.py          scenariul sintetic
  solver.py        căutare și verificare deterministă
  service.py       incidente, planuri, oferte și acceptări
  store.py         SQLite, tranzacții, versiuni și idempotency
  providers/       OpenAI Responses, Decisions, GPT-Live, FR24
frontend/src/
  App.tsx          centrul operațional și comenzile
  ResourceRail.tsx inventar și selecție
  Inspector.tsx    explicații, alternative, itinerarii și QR
  Passenger.tsx    experiența mobilă
  scene/           modelul 3D propriu și camera
  maps/            Google Maps geografic și observațiile externe
  liveVoice.ts     ciclul de viață WebRTC
deploy/            Caddy: fișiere statice, proxy API, SSE, TLS
scripts/           pornire, verificare și backup
```

## Verificare

```bash
./scripts/check.sh
```

Testele backend acoperă alocarea și concurența, versiuni depășite, familii, cazare, accesibilitate, bugete, invalidare în cascadă, expirare, persistență, autentificare și izolarea linkurilor pasagerilor. Testele providerilor verifică forma cererilor și răspunsurilor prin transport HTTP/WebSocket simulat. Acestea nu constituie apeluri reale la conturile externe. Testele frontend acoperă recuperarea unei cereri cu rezultat incert și proveniența suprapunerilor geografice. Buildul verifică TypeScript.

Verificarea vizuală urmărește fluxul complet în browser, la dimensiuni desktop și telefon. [Raportul de verificare](docs/verification/report.md) separă testele executate de integrările încă neverificate. Capturile efective sunt în [docs/verification](docs/verification); imaginile din `docs/mockups` sunt concepte, nu rezultate ale testelor.

La 31 octombrie 2026, instanța principală a verificat fluxul real **FR24 → caz → Responses Ultrafast → Nuitee → Decisions → propunere**: două căutări Nuitee au întors 20 de cotații, iar un plan valid a propus o soluție pentru doi adulți, fără excepții și fără modificarea grupului existent din afara selecției. O singură rulare a durat aproximativ 12,1 secunde; aceasta nu este o promisiune de performanță. Pasagerul a acceptat propunerea ca preferință, iar cotația a expirat ulterior în mod normal, fără rezervare la furnizor. Interfața APL a afișat șase cotații reale GBP, fără rezervare.

Prin interfață au fost importate nouă cotații hoteliere EUR și cele nouă proprietăți reale din zona LHR, folosind politicile furnizorului; o proprietate a fost exclusă cu motiv explicit. O comandă text în română a pornit apoi căutarea pentru cazul numit, în limita de patru apeluri: o interpretare și trei apeluri ale căutării. Zece cotații Nuitee au produs o propunere pentru doi pasageri, fără excepții; căutarea a durat 7,21 secunde, fără timpul interpretării. Grupul inițial a fost păstrat și nu au fost create rezervări.

Versiunea `fe47825`, livrată pe aceeași instanță, a trecut **775 de teste backend și 203 frontend**, buildul și verificarea formatării; ambele rulări CI au trecut. Interfața reală a afișat cele șase prețuri APL originale împreună cu estimările ECB din 30 octombrie 2026; de exemplu, GBP 76 a apărut alături de aproximativ EUR 91,15, la cursul 1 EUR = 0,83380 GBP. Au fost verificate și etichetele `Validity expired` și `Quote stale`, cu fereastra de 120 de secunde în UTC. Starea existentă a fost păstrată. Importul APL într-un itinerar rămâne nefinalizat, iar un traseu complet cu hotel și transferuri fizice rămâne de verificat.

Google a randat geografia reală, iar probele anterioare au verificat accesul OpenAI și transportul/închiderea unei sesiuni GPT-Live cu sunet sintetic. Acestea nu validează o conversație reală completă, toate modelele 3D sau exploatarea continuă a conturilor. Credențialele, payloadurile FR24, tokenurile pasagerilor și capturile consolelor private nu sunt publicate.

[Ghidul backend](docs/backend.md) descrie invariantele și limitele. [Măsurătorile locale](docs/benchmark-results.json) includ 180–300 de pasageri, mediană/p95 și mediul CPU; măsoară solverul și SQLite, fără a pretinde latență AI. Poți repeta măsurarea:

```bash
backend/.venv/bin/python backend/scripts/benchmark.py --samples 12 --passengers 180 240 300 --output docs/benchmark-results.json
```

## Publicare cu HTTPS

Pentru un server cu domeniu și HTTPS:

1. Creează configurația privată fără a suprascrie una existentă: `test -f .env.production || cp .env.production.example .env.production`, apoi `chmod 600 .env.production`.
2. Setează `APP_DOMAIN`, un `OPERATOR_TOKEN` aleator de minimum 32 de caractere și cheile disponibile. Nu folosi valorile de exemplu drept credențiale.
3. Configurează DNS-ul spre server și accesul la porturile 80/443.
4. Rulează:

```bash
docker compose --env-file .env.production -f compose.production.yaml up -d --build
```

Caddy gestionează TLS și proxy-ul. Backendul refuză configurația de producție fără token suficient de lung, URL HTTPS și origini/hosturi explicite. Linkurile pasagerilor sunt capabilități individuale, cu token în fragmentul URL al paginii; nu sunt publicate în exportul de audit. Logurile de acces sunt dezactivate în container pentru a nu înregistra tokenurile din endpointurile API.

[Lista de verificare a deployului](docs/deploy-checklist.md) include validarea configurației fără afișarea secretelor, readiness, autentificarea și testul de pe telefon. Nu expune direct portul API: configurația Uvicorn are încredere în headerele proxy deoarece numai rețeaua internă Caddy/API îl poate accesa.

Nu a fost publicată nicio instanță pe internet. Operarea reală cere conturi autorizate, verificarea accesului, cote și configurația organizației. Aplicația înregistrează confirmările operatorului; nu execută rezervări externe sau controlul aeronavelor.

## Persistență și întreținere

SQLite folosește tranzacții și WAL, pe un volum local persistent. Configurația livrată rulează un worker API. Instanțe suplimentare pot folosi aceeași bază locală cu protecțiile tranzacționale; nu distribui fișierul SQLite între calculatoare sau volume de rețea. Pentru o implementare distribuită este necesară migrarea către o bază centralizată și coordonare operațională adecvată.

Backup consistent al unei baze locale active:

```bash
backend/.venv/bin/python scripts/backup.py backend/data/recovery.sqlite3 backups/recovery-snapshot.sqlite3
```

Snapshotul verificat are permisiuni `0600` și omite cache-ul observațiilor de trafic. Scriptul refuză suprascrierea unei destinații existente. Comanda de mai sus privește baza locală; baza Docker se află în volumul `recovery_data`, nu în `backend/data`. Restaurarea se face cu serviciul oprit, după salvarea stării curente. O copie simplă a fișierului `.sqlite3` în timpul rulării poate omite datele din WAL; folosește scriptul.

Feedul FR24 are un cache separat: `OBSERVATION_RETENTION_SECONDS=3600` implicit. Datele expirate sunt excluse la citire și curățate periodic chiar dacă feedul este oprit, cât timp aplicația rulează. Snapshoturile operaționale și rezultatele persistate de idempotency nu arhivează observațiile. Aceasta este politica tehnică a cache-ului, nu o garanție privind licența providerului. Pentru replay și orice altă utilizare verifică termenii contului. Istoricul operațional are o politică de retenție separată, încă de stabilit pentru un pilot de durată. Materialele 3D locale au atribuirea în manifestul lor; atribuirea Google rămâne vizibilă în hartă.

## Ce rămâne de verificat cu echipa

- O sesiune reală GPT-Live cu microfon, delegare, redare, întrerupere și închidere confirmată.
- Fluxul de poziții FR24 pe hartă, separat de lookup-ul de cursă deja verificat; limitele abonamentului echipei.
- Un itinerar complet cu hotel și legături fizice verificate; importul hotelier prin interfață este verificat, dar nu dovedește existența unui transfer.
- Finalizarea și verificarea traseelor rutiere datate și a importului APL în itinerarii; afișarea cotațiilor GBP și a estimărilor ECB este deja verificată.
- Verificările de facturare Google și restricțiile cheii; fotogrammetria detaliată a terminalului și randarea/alinierea modelelor GLB.
- Profilul vizual pe ecranul/proiectorul evenimentului și fidelitatea față de mockupurile fotorealiste.

Aceste verificări nu pot fi înlocuite de teste cu răspunsuri simulate sau de simpla prezență a unei chei.

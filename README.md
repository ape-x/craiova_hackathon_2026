# Airport Recovery Live

Centru de operațiuni pentru recuperarea pasagerilor, cu FastAPI, React/TypeScript, Google Maps 3D și OpenAI. Aplicația pornește în modul live, cu inventar gol. Transportul și cazarea folosite în planificare trebuie să provină din cotații reale sau din servicii documentate de operator.

Problema inițială și cerințele pasagerilor pot fi ipoteze introduse de echipă. Grupurile generate păstrează această proveniență; nu devin identități verificate și nu pot primi confirmări externe de rezervare. Pentru pasageri reali se importă manifestul.

## Fluxul operațional

1. Deschide **Start recovery**, caută numărul de zbor și selectează exact cursa Flightradar24. Data căutării este ziua UTC a primei observări, nu neapărat data locală a plecării.
2. Înregistrează incidentul și leagă grupurile afectate. Datele inițiale pot proveni dintr-un manifest sau din cerințe de planificare introduse explicit. Grupurile trebuie să indice același zbor original; operațiunile existente sunt păstrate.
3. Caută zboruri, autocare și cazare pentru grup, dată și destinație. Fiecare cotație păstrează furnizorul, prețul, condițiile și expirarea.
4. Importă cotațiile relevante și completează serviciile confirmate de operator, cu sursa lor.
5. Descrie condițiile prin voce sau text. AI-ul propune strategii; backendul verifică fezabilitatea și alocările.
6. Pasagerul acceptă sau refuză o propunere. Acceptarea înregistrează preferința. Confirmarea rezervării necesită referințele externe pentru fiecare segment.

Căutarea furnizorilor este în prezent explicită. Replanificarea compară inventarul importat; nu creează automat bilete, camere sau transport. Cotațiile pot expira, iar o ofertă de preț nu rezervă locuri.

Incidentul reprezintă o condiție raportată de operator și nu modifică statusul furnizat de Flightradar24. Un aeroport declarat indisponibil este exclus și din segmentele intermediare ale zborurilor propuse grupurilor afectate. Grupurile cu rezervări deja confirmate necesită tratare separată de operator. Contextul de zbor are retenție limitată și se poate reîmprospăta numai pentru aceeași cursă; exporturile și backupurile omit datele temporare ale furnizorului.

Costul cazării păstrează separat cotația furnizorului și taxele obligatorii suplimentare. Taxele incluse nu sunt adăugate încă o dată. Cotațiile cu taxe lipsă sau fără conversie verificată în EUR sunt vizibile, dar nu intră în planuri cu buget complet calculat.

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

| Configurație | Funcție |
|---|---|
| `OPENAI_API_KEY` | Interpretare Sol Ultrafast, selecție Decisions și voce GPT-Live |
| `OPENAI_MODEL`, `OPENAI_SERVICE_TIER` | Implicit `gpt-6.1-sol`, `ultrafast`; fără schimbare automată de model sau tier |
| `OPENAI_DECISION_MODEL`, `OPENAI_VOICE_MODEL` | Implicit `gpt-6-luna`, respectiv `gpt-live-1` |
| `FR24_API_KEY` | Observații Flightradar24; necesită abonament API |
| `GOOGLE_MAPS_API_KEY` | Cheie publică de browser, restricționată la origini și Maps JavaScript API |
| `GOOGLE_MAPS_SERVER_API_KEY` | Google Places și Routes, numai pe server |
| `LITEAPI_API_KEY`, `LITEAPI_ENVIRONMENT=live` | Cotații Nuitee pentru cazare și zboruri, conform accesului contului |
| `DUFFEL_ACCESS_TOKEN`, `DUFFEL_ENVIRONMENT=live` | Furnizor alternativ pentru zboruri |
| `DISTRIBUSION_API_KEY`, `DISTRIBUSION_ENVIRONMENT=live` | Autocare; necesită și activarea fluxului Offers |
| `PUBLIC_BASE_URL`, `OPERATOR_TOKEN` | URL accesibil pasagerilor și autentificarea operatorilor |

Prezența unei chei înseamnă configurare, nu acces verificat. Conturile sandbox nu furnizează inventar live. Căutările nu efectuează plăți sau rezervări. [Configurare și limite](docs/live-mode.md), [date geografice și hoteliere](docs/real-data.md), [cereri către furnizori](backend/app/providers/README.md).

Pozițiile Flightradar24 au timestamp și sursă. Nu indică disponibilitatea biletelor, capacitatea avionului sau anularea unui zbor absent din feed. Colectarea pornește explicit din Connections și folosește un buget persistent de cereri. Google Maps afișează geografia; acoperirea 3D depinde de furnizor. [Documentația hărții](docs/maps.md).

O rută rutieră calculată nu este un vehicul disponibil. Stațiile de autocar păstrează pozițiile reale; transferurile aeroport–stație trebuie obținute separat. Lipsa unui serviciu sau a accesului la furnizor rămâne vizibilă.

## Decizii și voce

GPT-Live transmite cererea către backend. Sol Ultrafast interpretează cerințele; solverul construiește alternative și verifică familiile, timpii, capacitățile, accesibilitatea și bugetele. Decisions alege dintre variantele fezabile. Backendul reverifică versiunea înainte de aplicare și transmite actualizarea către operator și pasageri.

Solverul folosește o căutare limitată: validează constrângerile, fără a demonstra optimalitatea globală. Microfonul necesită HTTPS sau localhost. Vocea relatează rezultatul verificat, inclusiv serviciile lipsă; nu inventează rezervări ori progres. Erorile AI nu sunt înlocuite cu răspunsuri prestabilite.

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

La verificarea din 31 octombrie 2026 au reușit apeluri reale pentru interpretare **Sol Ultrafast**, selecție **Decisions** și prima randare geografică Google la CRA. Acestea sunt probe limitate de acces și funcționare, nu o validare completă a vocii, a modelelor 3D sau a conturilor pentru exploatare continuă. Credențialele și capturile consolei private de facturare nu sunt publicate; fiecare instalare folosește propriile conturi.

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
- O observație FR24 autorizată și afișată cu timestamp original; limitele abonamentului echipei.
- Verificările de facturare Google și restricțiile cheii; fotogrammetria detaliată a terminalului și randarea/alinierea modelelor GLB.
- Profilul vizual pe ecranul/proiectorul evenimentului și fidelitatea față de mockupurile fotorealiste.

Aceste verificări nu pot fi înlocuite de teste cu răspunsuri simulate sau de simpla prezență a unei chei.

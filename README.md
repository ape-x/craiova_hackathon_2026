# Airport Recovery Live

Centru de operațiuni pentru recuperarea pasagerilor, cu FastAPI, React/TypeScript, Google Maps 3D și OpenAI. Aplicația pornește în modul live, cu inventar gol. Transportul și cazarea folosite în planificare trebuie să provină din cotații reale sau din servicii documentate de operator.

Problema inițială și cerințele pasagerilor pot fi ipoteze introduse de echipă. Grupurile generate păstrează această proveniență; nu devin identități verificate și nu pot primi confirmări externe de rezervare. Pentru pasageri reali se importă manifestul.

## Fluxul operațional

1. Deschide **Start recovery**, caută numărul de zbor și selectează exact cursa Flightradar24. Data căutării este ziua UTC a primei observări, nu neapărat data locală a plecării.
2. Înregistrează incidentul și leagă grupurile afectate. Datele inițiale pot proveni dintr-un manifest sau din cerințe de planificare introduse explicit. Grupurile trebuie să indice același zbor original; operațiunile existente sunt păstrate.
3. Din caz, pornește căutarea pentru grupurile selectate și stabilește limita de apeluri. Sol Ultrafast alege căutări eligibile, sunt cerute cotații reale de zbor, iar Decisions selectează dintre planurile validate. Alocările celorlalte grupuri sunt păstrate.
4. Verifică sursele, propunerile și cerințele rămase fără soluție. Cazarea și legăturile terestre pot fi căutate separat și completate prin cotații importate sau servicii documentate de operator.
5. Folosește comenzile pentru condițiile suportate sau pentru a porni o căutare durabilă. În cererea curentă, identifică explicit un caz existent prin ID sau printr-un zbor fără ambiguitate. Formularul cazului și comanda text au produs propuneri reale; acceptanța completă a vocii rămâne deschisă.
6. Pasagerul acceptă sau refuză o propunere. Acceptarea înregistrează preferința. Confirmarea rezervării necesită referințele externe pentru fiecare segment.

Căutările pe incident păstrează progresul și fiecare apel într-un registru persistent. Repetarea aceleiași cereri nu retrimite apelurile. Un rezultat necunoscut după întrerupere necesită o încercare nouă, explicită; nu este reluat automat. Căutarea obișnuită acoperă zborurile și inventarul deja documentat. Opțional, formularul poate cere un plan complet cu hotel și două transferuri pentru un singur grup de adulți, în condițiile de mai jos. Lipsa legăturilor până la hotel sau stație rămâne vizibilă. Cotațiile pot expira și nu rezervă locuri.

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
| `APL_API_KEY`                                           | Cotații Airport Pickups London în GBP per vehicul și import manual aeroport–hotel sau hotel–aeroport; fără rezervare |
| `PUBLIC_BASE_URL`, `OPERATOR_TOKEN`                     | URL accesibil pasagerilor și autentificarea operatorilor                                                |

Prezența unei chei înseamnă configurare, nu acces verificat. Conturile sandbox nu furnizează inventar live. Căutările nu efectuează plăți sau rezervări. [Configurare și limite](docs/live-mode.md), [date geografice și hoteliere](docs/real-data.md), [cereri către furnizori](backend/app/providers/README.md).

Pozițiile Flightradar24 au timestamp și sursă. Nu indică disponibilitatea biletelor, capacitatea avionului sau anularea unui zbor absent din feed. Colectarea pornește explicit din Connections și folosește un buget persistent de cereri. Google Maps afișează geografia; acoperirea 3D depinde de furnizor. [Documentația hărții](docs/maps.md).

O rută rutieră calculată nu este un vehicul disponibil. Stațiile de autocar păstrează pozițiile reale; transferurile aeroport–stație trebuie obținute separat. Lipsa unui serviciu sau a accesului la furnizor rămâne vizibilă.

Cotațiile APL păstrează prețul exact în GBP per vehicul și afișează alături o estimare în EUR, calculată pe server din cursul ECB. Politica `ecb_reference_v1` permite folosirea explicită a estimării în planificare, cu sursa și data vizibile și o vechime maximă de trei zile calendaristice, evaluată în Europe/Berlin. Lipsa unui curs utilizabil păstrează cotația GBP, dar împiedică importul. Prețul și cursul trebuie reverificate înainte de rezervare; decontarea în EUR nu este garantată.

Simpla afișare a cotației nu adaugă inventar; importul separat verifică sursele și condițiile de mai jos. Adaptorul Google TRANSIT are teste offline, dar nu este conectat la fluxul operațional; un orar fără locuri sau tarif complet nu devine inventar disponibil.

Lista de resurse distinge cotațiile expirate sau cu prospețime necunoscută de inventarul curent. Inspectorul arată ora sursei și fie valabilitatea explicită, fie fereastra locală de 120 de secunde, în UTC. Acest termen local nu este o expirare declarată de furnizor și nu anulează o rezervare înregistrată. Afișarea se actualizează fără apeluri noi către furnizori; backendul decide în continuare eligibilitatea.

## Transferuri private între aeroport și hotel

Fluxul manual este limitat la aeroporturi din Regatul Unit acceptate de furnizorii conectați. Ambele direcții păstrează prețul original APL în GBP și estimarea ECB în EUR.

Pentru **Airport meeting point → hotel**:

1. În **Operation data → Private transfers**, selectează grupul de adulți și o proprietate hotelieră deja identificată prin surse. Sunt necesare accesul APL, Google Routes pe server și metadatele Nuitee ale proprietății.
2. Introdu punctul inițial de întâlnire, coordonatele, numărul de bagaje și ora viitoare de preluare. Confirmarea punctului este o ipoteză declarată de operator despre pasageri; nu este o verificare a terminalului făcută de Google sau APL. Originea aeroportuară a grupului rămâne neschimbată.
3. Caută cotațiile. Serverul verifică adresa și coordonatele aceleiași proprietăți, apoi cere prețul APL și traseul Google pentru ora indicată. Adresa hotelului nu este text liber. Neconcordanțele și datele lipsă rămân motive explicite de blocare.
4. Importă o opțiune eligibilă. Segmentul păstrează GBP, estimarea EUR, traseul datat și punctul declarat. Poate fi folosit numai ca primul segment al acelui grup; capacitatea descrie oferta pentru grup, nu stocul flotei. Totalurile care includ conversia rămân etichetate ca estimări și în propunerea pasagerului.

Aplicația respinge punctele aflate la peste 10 km de referința aeroportului și conexiunile unde capătul rutier Google se află la peste 100 m de punctul cerut. Aceste limite sunt verificări geografice ale aplicației, nu dovezi de acces pietonal sau în terminal. Sosirea este o estimare de condus; nu inventează timp de încărcare ori mers pe jos. Cotația importată folosește o fereastră locală de 120 de secunde de la primire, păstrând necunoscute ora cotației și expirarea declarate de furnizor.

Pentru retur, alege **Hotel → airport for onward flight** în același formular. Selectează exact o cotație de zbor actuală, disponibilă și asociată exclusiv grupului; cele expirate trebuie reîmprospătate. Introdu ora preluării de la proprietate, punctul aeroportuar de coborâre și un interval de procesare de 1–720 de minute, declarat explicit de operator. Serverul verifică dacă sosirea rutieră estimată plus acest interval precedă plecarea zborului ales. Returul poate fi urmat numai de acel zbor în itinerariul validat.

Terminalul de plecare rămâne necunoscut. Punctul și intervalul sunt ipoteze de planificare ale operatorului, nu instrucțiuni ale companiei aeriene sau o garanție de îmbarcare. Traseul Google folosește coordonatele punctului declarat, separat de reperul general al aeroportului. Eventualele instrucțiuni de întâlnire APL sunt afișate separat, ca informații ale furnizorului. Prospețimea unui retur este limitată de primul termen de 120 de secunde dintre răspunsul APL și predicția Google; orele și termenul sunt afișate în UTC. Expirarea lor nu anulează o rezervare înregistrată.

Aceste două importuri rămân **manuale**. Nu reîmprospătează prețul camerei și nu verifică automat conexiunea cu terminalul. Importul unui segment nu creează o ofertă completă, o rezervare sau pasageri recuperați. Fluxul automat de mai jos folosește surse noi și validează întregul itinerar. Dovezile din browser și de la furnizori sunt consemnate separat în [raportul de verificare](docs/verification/report.md).

## Recuperare automată cu hotel

În formularul cazului, selectează un singur grup alcătuit explicit numai din adulți și activează opțiunea de cazare. Introdu orele exacte de check-in/check-out cu fus orar, camerele, odihna minimă, punctul inițial și ora preluării, bagajele, aeroportul de plecare după cazare, punctul de coborâre și intervalul de procesare aeroportuară. Aeroporturile inițial și de plecare trebuie să fie în Regatul Unit; cerințele de accesibilitate nesusținute blochează fluxul. Selectează toate cele trei moduri: zbor, hotel și transport terestru.

Sunt necesare cel puțin **10 apeluri** pentru cererea din formular. Limita implicită de opt rămâne neschimbată; aplicația nu o mărește automat. Căutarea este limitată la o singură proprietate cu costuri obligatorii complete, o singură dată de zbor — ziua locală a checkoutului — și cel mult trei opțiuni de vehicul eligibile pe direcție. Sunt contabilizate separat selecția Responses, zborurile, tarifele și metadatele hotelului, două cotații APL, două trasee Google datate, ECB și Decisions. Un hotel exclus nu declanșează căutări suplimentare ascunse.

Propunerea trebuie să conțină toate cele patru segmente: transferul inițial la hotel, cazarea, returul la aeroport și zborul spre destinație. Șederea acoperă întregul interval cerut; returul propus pornește la checkout plus zece minute, o marjă a aplicației. Prețurile GBP/estimările EUR, adresele reale, orele și politica proprietății rămân vizibile. Numai sursele alese, cerința de ședere și propunerea sunt aplicate într-o singură tranzacție, după reverificarea versiunii, prospețimii și constrângerilor. Celelalte grupuri sunt păstrate. Nu se fac rezervări și nu se garantează terminalul, accesul pietonal sau îmbarcarea.

După o propunere completă reușită, configurația exactă se salvează pentru acel grup și caz. O comandă text sau vocală explicită o poate refolosi cu toate cele trei moduri și **cel puțin 11 apeluri totale**, incluzând interpretarea. Fără profil salvat se completează mai întâi formularul; cererea vocală nu inventează coordonate sau ore. Datele nu sunt mutate automat, iar configurația și limitele curente sunt reverificate înainte de apelurile de căutare. Profilul nu păstrează o cotație valabilă și nu este înlocuit de o rulare eșuată.

O rulare reală pe API `95a0606` și interfața `03112f6` a produs un plan complet pentru doi adulți, fără cerințe nerezolvate, cu exact zece apeluri și patru resurse noi. De la înregistrare până la aplicare au trecut 9,495 secunde; este o singură măsurare, nu o promisiune de latență. Grupul din afara selecției a rămas neschimbat și nu s-a făcut nicio rezervare. Revizuirea profilului salvat în browser a păstrat orele și limita de zece apeluri, a cerut reconfirmarea celor două puncte și nu a apelat furnizorii. Cotațiile au expirat ulterior normal. [Raportul de verificare](docs/verification/report.md) păstrează dovada; recuperarea completă prin voce în browser rămâne de verificat.

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

Versiunea `fe47825`, livrată pe aceeași instanță, a trecut **775 de teste backend și 203 frontend**, buildul și verificarea formatării; ambele rulări CI au trecut. Interfața reală a afișat cele șase prețuri APL originale împreună cu estimările ECB din 30 octombrie 2026; de exemplu, GBP 76 a apărut alături de aproximativ EUR 91,15, la cursul 1 EUR = 0,83380 GBP. Au fost verificate și etichetele `Validity expired` și `Quote stale`, cu fereastra de 120 de secunde în UTC. Starea existentă a fost păstrată. Acestea sunt dovezi istorice pentru afișare și prospețime; importul manual APL și rularea automată completă cu hotel descrise mai sus au fost implementate și verificate ulterior.

Prima probă vocală, pe `fe47825`, cu vorbire sintetizată și client Python, a verificat delegarea reală, înregistrarea cererii, răspunsul vocal și închiderea GPT-Live, cu utilizare finală de 33 de secunde. A consumat exact patru apeluri backend și a primit zece cotații Nuitee, dar pasul Decisions nu a aprobat un plan; nimic nu a fost aplicat. Recunoașterea datei a greșit „31 octombrie” ca „30–31 octombrie”, iar verificarea inițială a acceptat eronat subșirul `31`. Această probă istorică nu validează recuperarea completă sau fidelitatea datei.

Protecția pentru data asociată cazului și clasificarea distinctă a răspunsurilor Decisions au fost corectate și testate offline după probă și fac parte din implementarea curentă. Proba istorică de pe `fe47825` nu verifică aceste corecții. [Raportul](docs/verification/report.md#synthesized-spoken-request--partial-verification) separă dovezile de aceste corecții. Microfonul fizic, clientul vocal din browser, redarea audibilă și întreruperea rămân de verificat. Google a randat geografia reală; aceasta nu validează toate modelele 3D sau exploatarea continuă a conturilor. Credențialele, payloadurile FR24, tokenurile pasagerilor, audio și capturile consolelor private nu sunt publicate.

A doua probă, executată la 21:32 UTC cu autorizare separată pe API `95a0606` și interfața `31f49a1`, a vizat profilul hotelier salvat. Transcrierea a corupt numele grupului și data, iar verificarea ASR din clientul privat al probei a oprit cererea înainte de backend. Închiderea a fost confirmată după 17 secunde: zero interpretări backend, zero apeluri către furnizorii de căutare, zero joburi noi și lumea neschimbată la versiunea 49. Nu a existat confirmare vocală a unui job și nu a fost verificată recuperarea vocală în patru segmente. Proba nu a fost reluată și nu demonstrează o corecție a transcrierii. [Dovada separată](docs/verification/report.md#saved-hotel-profile--single-authorized-voice-probe) păstrează aceste limite.

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

Căutările private de transfer păstrează identitatea cererii separat de cache-ul temporar, limitat la 64 de răspunsuri și cel mult 120 de secunde. Repetarea aceleiași cereri nu retrimite furnizorii; un rezultat necunoscut cere analiză și o încercare nouă explicită. Backupul elimină acest cache și marchează cererile aflate în curs ca rezultat necunoscut. Restaurarea nu reia apelurile. Importurile finalizate își păstrează confirmarea idempotentă chiar după expirarea cache-ului.

Feedul FR24 are un cache separat: `OBSERVATION_RETENTION_SECONDS=3600` implicit. Datele expirate sunt excluse la citire și curățate periodic chiar dacă feedul este oprit, cât timp aplicația rulează. Snapshoturile operaționale și rezultatele persistate de idempotency nu arhivează observațiile. Aceasta este politica tehnică a cache-ului, nu o garanție privind licența providerului. Pentru replay și orice altă utilizare verifică termenii contului. Istoricul operațional are o politică de retenție separată, încă de stabilit pentru un pilot de durată. Materialele 3D locale au atribuirea în manifestul lor; atribuirea Google rămâne vizibilă în hartă.

## Ce rămâne de verificat cu echipa

- O sesiune GPT-Live în browser cu microfon fizic, recunoaștere corectă a cererii, recuperare validată, redare și întrerupere; proba Python cu vorbire sintetizată a verificat doar etapele descrise mai sus.
- Fluxul de poziții FR24 pe hartă, separat de lookup-ul de cursă deja verificat; limitele abonamentului echipei.
- Verificarea vizuală a direcțiilor manuale APL este urmărită separat în raport; nici planul automat validat nu dovedește terminalul real sau accesul pietonal.
- Verificările de facturare Google și restricțiile cheii; fotogrammetria detaliată a terminalului și randarea/alinierea modelelor GLB.
- Profilul vizual pe ecranul/proiectorul evenimentului și fidelitatea față de mockupurile fotorealiste.

Aceste verificări nu pot fi înlocuite de teste cu răspunsuri simulate sau de simpla prezență a unei chei.

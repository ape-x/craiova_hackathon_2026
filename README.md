# StrandedOS

Consolă operațională în română pentru asistența pasagerilor după anularea unui zbor: **cazare, transfer și vouchere de masă**. MVP de hackathon, pregătit pentru evaluarea unui prim pilot în România.

**Toate datele, confirmările, rezervările, notificările și voucherele sunt simulate.** Aplicația funcționează fără chei API și fără servicii externe la rulare. Instalarea inițială descarcă dependențele.

## Pornire rapidă cu Docker

Necesită Docker și Docker Compose v2:

```bash
docker compose up --build
```

- Aplicație: **http://localhost:8080**
- API și documentație OpenAPI: http://localhost:8000/docs
- Datele SQLite persistă în volumul `stranded-data`.
- „Resetează scenariul” din aplicație reîncarcă datele fictive și regenerează linkurile pasagerilor.

## Dezvoltare locală

Necesită Node.js 22.12+ și Python 3.12+ cu modulul `venv` disponibil.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

În alt terminal:

```bash
npm ci
npm run dev
```

Deschide **http://localhost:5173**. Vite trimite `/api` către FastAPI. SQLite se creează automat în `data/stranded.sqlite3`; `STRANDED_DB` poate schimba calea.

## Arhitectura minimă

```text
React + TypeScript + Vite + Tailwind
            │ /api (același origin prin proxy)
            ▼
FastAPI — monolit Python, un container
  ├── engine.py       alocare și verificarea constrângerilor
  ├── main.py         flux, aprobări, import și răspunsuri
  ├── store.py        tranzacții SQLite + istoric + revizii
  ├── integrations.py adaptoare pentru furnizori și notificări simulate
  └── demo.py         catalog și pasageri fictivi
            │
            ▼
SQLite — un incident activ, persistat ca agregat JSON
```

Frontendul este separat de monolitul backend și este servit de Nginx în Compose. Nicio componentă nu folosește o cheie API externă. Am ales Vite + React în locul Next.js deoarece cerința explicită este un backend Python.

## Flux disponibil

Interfața include temă luminoasă/întunecată, bară laterală restrângabilă și căutare globală cu **Ctrl/⌘ K**. Căutarea găsește pagini, grupuri și pasageri; săgețile și Enter navighează rezultatele, iar Escape închide dialogul. Pe mobil, căutarea se deschide din pictograma din antet. Registrul afișează câte 20 de grupuri pe pagină. Setările de aspect se păstrează local în browser, iar animațiile respectă preferința de mișcare redusă.

1. **Incident nou:** zbor, aeroport, destinație, număr declarat, interval și buget; opțional demo de 186 de pasageri.
2. **Pasageri:** import CSV sau încărcare demo înainte de generarea planului.
3. **Generează planul:** rezervări temporare de 20 minute, explicații și costuri detaliate.
4. **Plan de alocare:** mută un grup întreg la alt hotel și precizează motivul. O mutare imposibilă este respinsă fără schimbarea planului.
5. **Aprobă bugetul:** depășirile cer o bifă explicită. Furnizorii sunt încă neconfirmați.
6. **Furnizori:** confirmă/refuză separat hotelurile și transferurile. Hotelul confirmă împreună cazarea și masa. Butonul demo din panoul incidentului confirmă ceilalți furnizori și refuză Northline Hotel.
7. **Recalculează:** elimină serviciile refuzate/expirate, păstrează camerele confirmate și transferurile încă utilizabile, afișează intervențiile și cere reaprobarea noului plan.
8. **Pagina pasagerului:** pictograma voucherului din registru deschide o previzualizare și un link `/p/<token>`. Numai serviciile confirmate sunt afișate. Notificările nu sunt trimise efectiv.

Stări: `available → held → confirmed`, cu ramuri `held → refused` și `held → expired`. Catalogul afișează capacitatea rămasă. Expirările sunt evaluate la accesarea API-ului; consola se actualizează la 15 secunde. Un buton din Furnizori permite simularea imediată a expirării.

## Motor și limite asumate

- Familia/grupul este indivizibil: un hotel și un transfer pentru toți membrii. Poate solicita mai multe camere în același hotel.
- Camerele respectă ocupanții și accesibilitatea; transferurile verifică separat locurile, bagajele, scaunele rulante și intersecția intervalelor de preluare.
- O cameră este rezervată pentru întregul interval de asistență. Un vehicul execută **o singură cursă spre un singur hotel** în incident; nu presupunem reutilizare sau rute multiple.
- Cazarea se tarifează cu `ceil(durata / 24h)`, minimum o noapte. Demo: o noapte, un transfer și un voucher de **65 RON/pasager**. Tarifele sunt totaluri fictive în RON, fără calcule fiscale separate.
- Motorul încearcă două strategii deterministe: grupuri constrânse întâi, cu evaluarea tarifelor; respectiv ordinea CSV și primul hotel/vehicul fezabil. Selectează mai întâi **acoperirea completă mai mare**, apoi **costul total mai mic**.
- Aceasta este o **euristică**, nu un optim global. Nu există garanția că o soluție fezabilă rămasă va fi găsită. Orice grup neacoperit apare în intervenții.
- Confirmarea unui vehicul rezervă capacitatea sa pentru ruta și ora confirmate; alte grupuri compatibile pot utiliza locurile rămase după reaprobarea planului. Ora unei curse confirmate nu se schimbă la recalculare.
- Refuzul de capacitate suplimentară la un hotel nu anulează camerele deja confirmate. Un transfer spre un hotel care nu mai poate caza grupul este eliberat; schimbarea destinației cere confirmare nouă.
- O singură bază SQLite și `BEGIN IMMEDIATE` serializează modificările; revizia trimisă de client previne suprascrierea unui plan vechi. Este un MVP cu **un singur incident activ**, nu un sistem multi-tenant.

Comparația folosește același catalog și aceleași confirmări existente pentru ambele reguli. „Confirmări simulate” presupune acceptarea tuturor solicitărilor; nu reprezintă confirmările operaționale curente. La acoperire diferită nu afișăm economia drept echivalentă.

## CSV

Exemplu complet: [public/demo-passengers.csv](public/demo-passengers.csv), disponibil și din dialogul de import.

```csv
passenger_id,name,group_id,room_capacity,accessible,bags,eligible
P001,Ana Popescu,F001,2,true,1,true
P002,Mihai Popescu,F001,2,false,1,true
```

`room_capacity` este numărul maxim dorit de membri ai grupului într-o cameră (1–4). Grupurile mai mari primesc mai multe camere. Membrii unui grup trebuie să aibă aceeași eligibilitate și aceeași valoare `room_capacity`. Accesibilitatea este per pasager; un pasager accesibil cere și loc pentru scaun rulant în vehicul, o presupunere conservatoare pentru demo. Importul acceptă maximum 1.000 pasageri, 12 membri/grup, 5 bagaje/pasager și 1 MB. Numărul rândurilor trebuie să coincidă cu numărul declarat în incident. Coloanele opționale `pickup_start` și `pickup_end` folosesc ISO 8601 cu fus orar.

## Verificări

```bash
source .venv/bin/activate
python -m pytest backend/tests -q
npm run build

# Prima rulare pentru browser:
npx playwright install chromium
npm run test:e2e
```

Testele Python verifică supraalocarea, integritatea familiilor, camere multiple, accesibilitatea, bagajele, intervalele, intervențiile, determinismul, aprobarea depășirilor, separarea aprobării de confirmări, păstrarea confirmărilor după refuz, expirarea, importul atomic, conflictele concurente și izolarea paginii pasagerului. Testele Playwright parcurg fluxul demonstrativ și verifică afișarea pe mobil. Folosesc porturile dedicate `5186`/`8186` și `/tmp/stranded-e2e.sqlite3`, separat de serverele obișnuite de dezvoltare.

## Livrabile de produs

- [Scenariu de prezentare de 90 secunde](docs/DEMO.md)
- [Plan de pilot comercial](docs/PILOT.md)

Pentru pilot, autentificarea operatorilor, izolarea datelor clientului și integrarea inventarului real sunt lucrări necesare. MVP-ul curent este destinat datelor fictive și rulării într-un mediu de demonstrație controlat.

Referințe tehnice: [Vite](https://vite.dev/guide/), [Tailwind cu Vite](https://tailwindcss.com/docs/installation/using-vite), [testarea FastAPI](https://fastapi.tiangolo.com/tutorial/testing/).

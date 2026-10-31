# Scenarii definite liber prin voce sau text

Scenariul inițial este doar punctul de plecare. Operatorul poate descrie o situație nouă și poate combina mai multe modificări într-o singură cerere. Exemplele rapide din interfață nu aleg răspunsuri precalculate.

## Ce poate crea și modifica agentul

- Aeroporturi noi din catalogul geografic public OurAirports, după IATA, ICAO sau nume/oraș. Orașele ambigue cer alegerea aeroportului. Un aeroport personalizat necesită nume și coordonate explicite.
- Curse simulate de avion, autocar și cazare, cu rută, orar, capacitate, tarif și accesibilitate.
- Pasageri noi, împărțiți în familii/grupuri indivizibile, cu origine, destinație, disponibilitate, termen, buget și prioritate. Serverul generează identificatorii și linkurile individuale.
- Indisponibilități, anulări, devieri, închideri/redeschideri de aeroport, eligibilitate pentru deviere, orare, rute, capacități și costuri.
- Destinația, termenul, accesibilitatea, prioritatea și bugetul grupurilor existente; bugetul total al simulării.

Datele geografice sunt reale. Disponibilitatea aeroporturilor și toate resursele, pasagerii, ofertele și rezervările create astfel sunt **simulate**. Observațiile Flightradar24 rămân separate și nu devin inventar de bilete.

## Exemplu de conversație

> Creează un scenariu nou cu 12 pasageri la Craiova care trebuie să ajungă la Lisabona.

Agentul cere informațiile esențiale care lipsesc, fără să aplice o parte a scenariului.

> La LIS până azi la 20:00, buget 200 EUR per pasager. Sunt patru familii de câte trei persoane, gata la 10:00. Adaugă un charter CRA–LIS cu 36 de locuri, plecare azi 13:20, sosire 16:40, tarif 120 EUR per pasager. Pentru celelalte câmpuri folosește valorile implicite ale simulatorului.

Agentul folosește cererea inițială și răspunsul actual, adaugă LIS, cursa și familiile, apoi calculează alternativele din starea curentă. O continuare poate modifica noua resursă:

> Charterul spre Lisabona întârzie 90 de minute. Redu capacitatea la 9 locuri și recalculează.

Nu există un scenariu „Lisabona” implementat separat. AI-ul produce operații structurate, iar motorul execută aceeași validare pentru orice combinație acceptată.

Pentru demonstrații rapide, operatorul poate autoriza explicit generarea de valori fictive: „Construiește un scenariu de demonstrație pentru această situație; alege și explică valorile simulate care lipsesc.” Fără această autorizare, modelul cere capacitățile, costurile, orarele și limitele lipsă. Nu adaugă transport alternativ numai pentru că nu găsește o soluție.

## Context și voce

Cererea curentă și contextul sunt câmpuri separate în `POST /api/commands`. Contextul include perechi recente cerere–rezultat verificat și selecția din interfață. Servește pentru răspunsuri precum „da, acolo” sau „nu, la 18:00”; nu reexecută operațiile vechi. Este păstrat în pagina curentă și se pierde la reîncărcare.

GPT-Live deleagă interpretarea după descrierea operatorului. Aplicația asociază delegarea cu intervalul transcrierii, așteaptă fragmentele întârziate și respinge delegările duplicate/depășite. Răspunsul verbal primește rezultatul backendului. Clarificările nu sunt tratate drept acțiuni reușite. Documentație oficială: [delegare](https://developers.openai.com/api/docs/guides/live-delegation), [instrucțiuni GPT-Live](https://developers.openai.com/api/docs/guides/live-prompting).

## Aplicare și limite

1. Responses interpretează cererea în operații și politici de optimizare, cu schemă strictă.
2. Backendul proiectează toate operațiile într-o copie a stării. Validează referințele, timpul, capacitatea, familiile, costurile și rezervările existente.
3. Solverul construiește și verifică alternativele pentru aeroporturile eligibile. Dacă sunt multe, păstrează pentru Decisions maximum opt variante, inclusiv câștigătorii după obiective și alternative geografice; numerele calculate și trimise sunt auditate. Decisions alege între planurile validate sau aprobă/refuză explicit unicul plan, dacă există doar unul.
4. O singură tranzacție reverifică versiunea și aplică modificările plus rezervările. Dacă un pas eșuează, cererea nu lasă entități adăugate parțial.

Acesta este un model operațional extensibil, nu un simulator universal de fizică. Meteo detaliată, combustibil, mentenanță tehnică, echipaje sau proceduri ATC nu au încă modele proprii. Agentul trebuie să explice limita și să ceară efectul operațional reprezentabil, de exemplu „închide aeroportul” sau „întârzie zborul cu 40 de minute”. Nu pretinde că a simulat efectele nemodelate.

Limite actuale: 64 de operații într-o comandă, 32 de aeroporturi, 150 de resurse, 250 de grupuri / 1.500 de pasageri și un orizont de șapte zile. Ora se exprimă relativ la ziua `world.epoch`, nu la ceasul real. Familia are maximum 12 membri. Hotelurile au capacitate în camere; câmpul istoric `cost_per_person` reprezintă costul pe cameră pentru segmentul de cazare, iar pentru avion/autocar tariful per pasager.

AI-ul nu execută cod arbitrar și nu ocolește aceste limite. O descriere liberă trebuie să poată fi reprezentată prin entitățile și constrângerile modelului. Dacă nu există itinerare fezabile pentru toți, rezultatul păstrează explicit pasagerii fără soluție.

# Pilot comercial · un operator, Otopeni

## Obiectiv și domeniu

Un pilot de patru săptămâni cu o companie aeriană sau un operator de handling, un aeroport și catalogul său existent de hoteluri și transportatori. Acoperim cazare, transfer și masă pentru anulări. Coordonatorul ia decizia finală. Reprogramarea pe alte zboruri nu intră în pilot.

Primele două săptămâni rulează în paralel cu procesul existent, pe incidente istorice și apoi în mod de observare. Următoarele două săptămâni pot include 3–5 incidente asistate, după validarea operațională și a protecției datelor. Nicio solicitare reală nu se trimite din adaptoarele demo.

## Date necesare

| Sursă                | Date minime                                                                                                                                                                   | Responsabil                 |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| Incident             | zbor, aeroport, intervalul asistenței, buget, persoana care îl poate aproba                                                                                                   | Coordonator                 |
| Registru pasageri    | identificator intern, nume necesar pentru cazare, grup/familie, eligibilitate stabilită de operator, camere necesare, bagaje, cerințe de mobilitate și intervalul de preluare | Companie aeriană / handling |
| Hoteluri contractate | tipuri și număr de camere, camere accesibile, tarife complete, intervale, reguli de ocupare și masă, persoana de contact                                                      | Achiziții / hoteluri        |
| Transport            | vehicule, locuri, bagaje, scaune rulante, intervale, durată de transfer, tarife și puncte de preluare                                                                         | Transportatori              |
| Rezultate            | momentele solicitării, aprobării și confirmării, refuzuri, costuri finale, persoane asistate și intervenții                                                                   | Operator și furnizori       |

Operatorul stabilește eligibilitatea și politica de asistență; MVP-ul nu ia decizii juridice și nu calculează compensații. Colectăm doar cerințele practice de accesibilitate necesare serviciului, fără diagnostic medical.

## Integrări și lucrări înainte de utilizare cu date reale

1. **Acces și izolare:** SSO sau conturi operator, roluri coordonator/aprobator, un client per spațiu de date, TLS, audit cu identitate verificată, acces restrâns la catalog și registru. Pentru pilot cu un singur operator poate rămâne un singur monolit și o singură instanță SQLite cu backup.
2. **Import:** maparea unui CSV/SFTP exportat din sistemul operatorului; validarea familiilor și a eligibilității cu acesta. Evităm o integrare DCS în prima săptămână dacă un export este suficient.
3. **Inventar:** import contractual și confirmare manuală la început; ulterior API sau portal furnizor cu inventar datat. Pentru incidente simultane, adăugăm rezervări comune între incidente și tranzacții pe resurse înainte de activare.
4. **Furnizori:** înlocuim `SimulatedProviderGateway` cu solicitări identificabile, referință de rezervare, termen de răspuns, semnătură de callback și operații idempotente. Separăm refuzul de camere suplimentare de anularea unei rezervări confirmate; reconciliem inventarul după fiecare răspuns.
5. **Pasageri:** înlocuim `SimulatedNotificationGateway` cu SMS/email prin furnizorul clientului, linkuri cu expirare și revocare, reconfirmarea numărului de contact și dovadă de livrare. Voucherele comerciale necesită evidență de emitere, utilizare și anulare.
6. **Operare:** politici convenite pentru retenție, acces la cerințe sensibile, ștergere, backup și recuperare; export financiar și reconcilierea facturilor. Se validează cu responsabilul de date al operatorului înainte de importul real.

## Măsurare

Instrumentăm aceleași momente pentru procesul existent și pentru StrandedOS. Păstrăm numărul eligibil, profilul grupurilor și constrângerile catalogului pentru fiecare incident.

| Indicator                    | Definiție                                                                                                                        | Comparație                                    |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| Timp de coordonare activă    | minute lucrate de coordonator pentru catalog, alocare, apeluri și modificări; înregistrare cu start/stop și categorie            | mediană per incident și minute / 100 pasageri |
| Timp până la prima asistență | prima confirmare de cazare + transfer + masă minus deschiderea incidentului                                                      | cronologie comună                             |
| Timp până la acoperire       | momentul atingerii a 90% din pasagerii eligibili confirmați minus deschiderea incidentului; dacă nu se atinge, raportăm explicit | mediană și interval                           |
| Acoperire completă           | pasageri cu toate cele trei servicii confirmate / pasageri eligibili                                                             | obligatoriu alături de cost                   |
| Cost total                   | cazare + transfer + masă + taxe convenite, după facturare; estimatul se păstrează separat                                        | aceeași monedă, aceleași servicii             |
| Cost / pasager asistat       | cost total / pasageri cu asistență completă; include distinct costul serviciilor parțiale                                        | numai la acoperire și profil comparabile      |
| Calitate operațională        | supraalocări, familii împărțite, transferuri ratate, refuzuri, intervenții manuale                                               | număr și cauză                                |

Baseline: 5–10 incidente istorice comparabile, plus regula simplă rulată pe aceeași copie de inventar și aceleași constrângeri. Pentru comparațiile algoritmice folosim aceeași ipoteză de acceptare; confirmările efective se raportează separat. Nu comparăm drept economie un incident cu acoperire mai mică.

## Criterii propuse pentru continuare

Se convin cu operatorul înainte de începere: zero supraalocări și zero familii separate de sistem; acoperire cel puțin egală cu procesul existent; reducere țintă de 25% a timpului activ median de coordonare; cost per pasager cel mult egal la servicii și acoperire comparabile. Acestea sunt **ținte de validat**, nu rezultate demonstrate. Un eșantion de 3–5 incidente nu dovedește singur efecte statistice; completăm cu interviurile coordonatorilor și analiza intervențiilor.

Livrabil final: raport pe incidente, cronologii, acoperire, cost estimat versus facturat, motive de refuz și lista integrărilor justificate pentru etapa următoare.

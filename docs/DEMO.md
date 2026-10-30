# Demo StrandedOS · 90 de secunde

## Pregătire

Pornește aplicația, deschide panoul și apasă **Resetează scenariul**. Toate numele, datele, tarifele și răspunsurile sunt fictive. Folosește fereastra desktop pentru consolă; pagina pasagerului este adaptată pentru mobil.

## Prezentare

| Interval | Acțiune în aplicație                                                                                  | Ce explicăm                                                                                                                                                                                                                                                                  |
| -------- | ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0–15 s   | **Incident nou** → RO 384, OTP, 186 pasageri, buget 45.000 RON, demo bifat → **Deschide incidentul**. | „Un zbor anulat la Otopeni. Operatorul definește incidentul și preia registrul pasagerilor.”                                                                                                                                                                                 |
| 15–30 s  | **Generează planul**, apoi **Compară cu regula simplă**. Închide comparația.                          | „Recomandarea respectă familiile, camerele, accesibilitatea, bagajele și orele. Demo-ul inițial acoperă 186 de pasageri cu 43.060 RON; regula simplă acoperă 184 cu 42.385 RON. Cei 675 RON în plus asigură încă doi pasageri. Nu pretindem economii la acoperire diferită.” |
| 30–42 s  | **Verifică & aprobă** → **Aprobă 43.060 RON**.                                                        | „Am autorizat intern bugetul. Confirmările furnizorilor sunt încă zero.”                                                                                                                                                                                                     |
| 42–55 s  | **Simulează răspunsuri**.                                                                             | „Northline refuză disponibilitatea. Ceilalți confirmă. Pasagerii afectați apar imediat în intervenții.”                                                                                                                                                                      |
| 55–72 s  | **Recalculează** → verifică explicația și bugetul → aprobă noul plan.                                 | „Recalculăm solicitările afectate, păstrăm confirmările utilizabile și arătăm persoanele fără soluție. Nicio familie nu este împărțită ca să cosmetizăm acoperirea.”                                                                                                         |
| 72–80 s  | **Furnizori** → **Confirmă solicitările**.                                                            | „Furnizorii acceptă noile solicitări în simulare; confirmarea rămâne o etapă separată de buget.”                                                                                                                                                                             |
| 80–90 s  | **Pasageri** → pictograma voucherului pentru **G001**.                                                | „Familia vede pe mobil hotelul confirmat, locul și ora de preluare și voucherul de masă. Operatorul vede ce rămâne de rezolvat.”                                                                                                                                             |

## Dacă juriul întreabă

- **Este optim?** Nu. Recomandare deterministă, cu alegere după acoperire, apoi cost, dintre două euristici; constrângerile se verifică înainte de salvare.
- **Sunt rezervări reale?** Nu. Catalog, răspunsuri, vouchere și notificări simulate, marcate în interfață. Nu este necesară nicio cheie API.
- **Ce se întâmplă fără capacitate?** Grupul apare explicit în intervenții, cu motiv. Operatorul poate încerca o mutare fezabilă sau obține capacitate suplimentară în afara MVP-ului.
- **Cât durează o rezervare temporară?** 20 minute după ce este creată. Expirarea eliberează resursele neconfirmate și cere recalculare.
- **Ce aduce primul pilot?** Folosim furnizorii deja contractați ai operatorului, măsurăm timpul până la asistență confirmată și costul pentru acoperire comparabilă. Reprogramarea zborurilor rămâne în afara MVP-ului.

Rezultatele numerice de mai sus corespund catalogului inițial. După refuz sau ajustări, costul și acoperirea se recalculează; nu prezenta valorile inițiale ca rezultate comerciale observate.

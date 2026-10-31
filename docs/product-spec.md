# Airport Recovery Live

## Recuperare asistată a pasagerilor, cu date și cotații reale

**Versiune:** 2.1 — 31 octombrie 2026. Acest document descrie produsul live actual. Conceptul inițial a fost pregătit pentru secțiunea AUTOPILOT a [Craiova Hackathon](https://www.craiovahackathon.ro/); imaginile de concept rămân repere vizuale.

**Implementare:** FastAPI + React/TypeScript, disponibilă într-o singură instanță locală la `http://localhost:8080`. [README](../README.md) descrie operarea; [raportul de verificare](verification/report.md) separă probele reale, testele offline și verificările rămase.

**Promisiunea produsului:** un operator pornește de la un zbor identificat și un incident, precizează persoanele afectate și primește alternative bazate pe surse reale, cu verificări explicite și lipsuri vizibile. Aplicația asistă planificarea; nu cumpără bilete, nu rezervă camere și nu comandă vehicule.

## 1. Utilizatori și scop

Clientul comercial propus este compania aeriană, iar utilizatorul principal este coordonatorul operațional sau echipa de handling. Pasagerul primește o propunere individuală și își poate comunica preferința. Aeroportul oferă context și poate fi partener operațional; nu presupunem că deține inventarul comercial al companiilor aeriene.

Problema și cerințele inițiale pot fi ipoteze introduse de echipă. Grupurile generate sunt marcate ca date de planificare sintetice și nu devin identități verificate. Pentru persoane reale se importă un manifest. În ambele situații, transportul și cazarea admise în planificarea live provin din cotații reale sau servicii documentate de operator.

Aria automată actuală cuprinde căutarea zborurilor pentru grupuri explicit legate de un caz, evaluarea inventarului deja documentat și selecția unei propuneri. Opțional, pentru un singur grup alcătuit explicit numai din adulți, aplicația poate căuta și aplica un plan complet cu transfer inițial, hotel, retur la aeroport și zbor. Acest flux cere intrări suplimentare și aeroporturi inițial/de plecare din Regatul Unit; nu stabilește acoperire terestră în România. Cazarea și transferurile au și fluxuri manuale separate. O legătură lipsă între aeroport, stație și hotel rămâne o lipsă; coordonatele apropiate nu o înlocuiesc.

## 2. Fluxul operatorului

1. **Identifică zborul.** În **Start recovery**, introduce numărul și data UTC a primei observări. Flightradar24 poate întoarce mai multe curse; operatorul selectează exact cursa. O căutare fără rezultat nu înseamnă anulare.
2. **Înregistrează incidentul.** Alege perturbarea zborului sau indisponibilitatea destinației și descrie condiția raportată. Aceasta nu modifică afirmațiile furnizorului despre zbor.
3. **Leagă grupurile afectate.** Datele provin dintr-un manifest sau din ipoteze explicite. Grupurile trebuie să indice același zbor original. Un caz fără grupuri rămâne în starea „necesită date despre pasageri”.
4. **Pornește căutarea.** Selectează grupurile cazului, obiectivul și limita de apeluri. Pentru planul cu hotel, selectează un singur grup eligibil și toate cele trei moduri; completează intervalul de ședere, camerele, odihna minimă, punctele declarate, preluarea, bagajele și marja aeroportuară. Căutarea automată nu înlocuiește alocările grupurilor din afara selecției.
5. **Verifică rezultatul.** Urmărește etapele reale, furnizorii, cotațiile și motivele excluderii alternativelor. O propunere validă poate coexista cu alte cotații respinse. Numărul pasagerilor fără soluție este separat de istoricul căutărilor nereușite.
6. **Completează serviciile lipsă.** Hotelurile necesită date și ocupare explicite, costuri obligatorii cunoscute și conexiuni fizice independente. Un serviciu introdus de operator necesită dovezi, capacitate, preț, orar și valabilitate.
7. **Primește preferința pasagerului.** Acceptarea sau refuzul actualizează aceeași stare operațională. Acceptarea nu confirmă o rezervare la furnizor. Confirmarea externă este o acțiune distinctă, documentată pentru fiecare segment.

Contextul FR24 are retenție limitată. Reîmprospătarea unui caz trebuie să păstreze aceeași cursă; nu selectăm automat un alt zbor. Înregistrarea unui caz nou nu resetează operațiunea existentă.

## 3. Ce reprezintă fiecare sursă

| Date                            | Sursă și utilizare                                                                  | Limită explicită                                                                                                 |
| ------------------------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Cursa și observațiile aeronavei | Flightradar24, cu sursă și timp                                                     | Nu furnizează inventar de bilete; absența din rezultate nu dovedește anularea                                    |
| Incident și condiții            | Raportul operatorului                                                               | Nu constituie o confirmare externă a închiderii aeroportului                                                     |
| Grupuri de pasageri             | Manifest importat sau ipoteze de planificare marcate                                | Grupurile generate nu pot primi confirmări reale de rezervare                                                    |
| Cotații de zbor                 | Nuitee Flights; adaptor Duffel alternativ, dacă are acces autorizat                 | Disponibilitatea și prețul unei căutări nu sunt locuri reținute                                                  |
| Cotații hoteliere               | Nuitee / LiteAPI; adaptor Booking conform accesului contului                        | Date, ocupare și taxe obligatorii; niciun prebook sau plată                                                      |
| Proprietăți hoteliere           | Coordonate ale furnizorului, metadate și fus orar derivat                           | Hotelul nu este plasat la aeroport și nu implică un shuttle                                                      |
| Transferuri private UK          | Airport Pickups London, GBP per vehicul, plus proprietatea exactă și Google Routes datat | Import manual în ambele direcții și plan hotelier automat limitat; sosirea și EUR sunt estimări, nu rezervare sau stoc de flotă |
| Autocare programate             | Adaptor Distribusion, cu acces de producție separat                                 | Stații reale, vârste explicite și conexiuni până la stații; accesul nu este presupus                             |
| Hartă și rute rutiere           | Google Maps/Places/Routes; surse publice documentate când nu este configurat Google | O rută și o durată estimată nu sunt un vehicul disponibil                                                        |
| Transport public                | Adaptor pur Google TRANSIT, cu teste offline                                        | Nu este încă un flux conectat în produs și nu completează inventarul                                             |
| Estimări valutare               | ECB, conectat la cotațiile APL și costul planificat al itinerariilor                  | Prețul GBP rămâne original; EUR intră explicit ca estimare în buget, fără decontare garantată și cu revalidare înainte de rezervare |

Eșecul unui furnizor configurat rămâne vizibil. Nu este înlocuit cu inventar sintetic sau cu un răspuns AI prestabilit. O cheie prezentă indică configurare; numai verificarea reală demonstrează accesul la operația testată.

## 4. Căutări durabile pe incident

Fiecare încercare păstrează selecția grupurilor, obiectivul, limita de apeluri, etapele și rezultatele într-un registru persistent. Interfața arată apelurile expediate și stările lor, nu un procent de progres inventat.

```mermaid
flowchart TD
    F[Zbor FR24 selectat exact] --> I[Incident raportat și grupuri legate explicit]
    I --> J[Căutare durabilă cu limită de apeluri]
    J --> S[Sol Ultrafast alege căutări eligibile]
    S --> Q[Cotații reale de zbor]
    Q --> V[Solver: itinerarii complete și verificări]
    S --> H[Opțional: hotel și două transferuri UK cu intrări explicite]
    H --> V
    V --> D[Decisions selectează un plan valid]
    D --> C[Reverificare și aplicare atomică în aria selectată]
    C --> P[Propuneri către operator și pasageri]
    V --> G[Lipsuri explicite când nu există soluție]
```

Repetarea aceleiași cereri și chei recuperează încercarea existentă. O întrerupere cu rezultat necunoscut nu provoacă reluarea automată a apelurilor către furnizori. O încercare nouă este explicită și poate genera costuri noi.

Anularea oprește expedierile și aplicările ulterioare. Un apel deja în curs poate totuși termina și poate fi facturat; rezultatul lui rămâne în registru. Interfața distinge cererea de anulare de starea finală.

Hotelurile și transportul terestru selectate fără intrările sau legăturile necesare produc cerințe explicite. Ele nu declanșează o căutare automată completă doar pentru că există un checkbox.

Fluxul hotelier complet necesită cel puțin **zece apeluri** în formular; limita implicită de opt nu crește automat. Sunt contabilizate separat selecția Responses, o căutare de zbor, tarifele hoteliere, metadatele proprietății exacte, două cotații APL, două rute Google pentru orele viitoare, un curs ECB și Decisions. Căutarea este limitată la o singură proprietate cu costuri obligatorii complete, o singură dată de zbor — ziua locală a checkoutului — și cel mult trei opțiuni de vehicul eligibile pe direcție. Excluderea proprietății nu pornește automat o căutare suplimentară.

Intervalele cu fus orar, camerele/odihna minimă, punctul inițial, preluarea, bagajele, aeroportul ulterior, punctul de coborâre și marja de procesare sunt intrări explicite ale operatorului. Cerințele de accesibilitate nesusținute blochează acest flux. Returul propus pornește la checkout plus zece minute, o marjă a aplicației. Numai proprietățile și resursele din planul ales, cerința de ședere, profilul și propunerea sunt aplicate atomic, după reverificarea versiunii, surselor și constrângerilor. Nu sunt create rezervări.

## 5. Constrângeri și sensul unei propuneri fezabile

Backendul verifică indivizibilitatea grupurilor, capacitățile documentate, continuitatea fizică și temporală, compatibilitatea cu accesibilitatea declarată, termenele, bugetele și utilizarea vehiculelor. Căutarea este limitată; nu demonstrează optimalitatea globală.

Fiecare propunere trebuie să ajungă la destinația grupului. O cameră de hotel singură nu înseamnă recuperarea călătoriei. În live, itinerariile pot include până la șase segmente pentru a reprezenta și transferurile fizice ale unei șederi la hotel.

Trecerea peste miezul nopții nu creează singură o obligație de cazare. Așteptările dintre servicii, inclusiv conexiunile din cotațiile de zbor, sunt afișate cu durata efectivă și fusul orar local când este cunoscut. Fluxul hotelier păstrează în grup cerința structurată `hotel_stay_requirement`: intervalul exact, numărul camerelor și durata minimă de odihnă cerută de operator. Un singur segment hotelier sursat trebuie să acopere întregul interval și ocuparea; așteptările sau camerele neconectate nu satisfac cerința. Aceasta este o condiție de planificare, nu dovada odihnei efective sau o politică de odihnă presupusă. Accesibilitatea declarată necesită compatibilitate documentată.

Costurile obligatorii ale hotelului sunt separate de suma de bază a furnizorului. Taxele incluse nu sunt adăugate din nou. Taxele lipsă sau într-o monedă fără conversie admisă lasă totalul pentru buget necunoscut și împiedică folosirea cotației într-un plan complet calculat.

La importul unei cotații hoteliere, serverul poate verifica automat intervalul de check-in și limita de checkout din politica proprietății exacte, în fusul orar derivat din coordonatele sale. Fără o oră finală de check-in publicată, este admisă automat numai ora exactă de început; sosirea târzie nu este presupusă. Politicile incomplete, contradictorii, expirate, cu instrucțiuni speciale sau cu ore locale ambigue exclud proprietatea și păstrează motivul. Operatorul poate declara separat că a convenit orele cu proprietatea; această declarație are proveniență distinctă și nu reprezintă o rezervare. Niciuna dintre căi nu creează un transfer până la hotel.

Bugetele sunt verificate față de costurile documentate ale propunerii, nu față de o plată viitoare garantată. Cotațiile pot expira și necesită revalidare. Politica `ecb_reference_v1` admite estimări ECB pentru compararea și importul prețurilor APL în itinerarii: suma exactă în GBP rămâne principală, iar valoarea EUR folosită la buget este explicit aproximativă, cu sursa, data cursului și momentul evaluării. Dovezile tipizate se păstrează în resursă, segmente, propunere și contextul Decisions. Sunt admise cel mult trei zile calendaristice de vechime în Europe/Berlin, o regulă a aplicației. Un curs vechi, lipsă sau indisponibil nu produce o valoare zero și nu ascunde cotația GBP. Estimarea nu este preț EUR al furnizorului și nu garantează decontarea; este necesară revalidarea înainte de rezervare.

Transferurile manuale folosesc aceeași proprietate hotelieră identificată prin metadate și coordonate sursate. Punctul inițial este o ipoteză confirmată de operator, legată de grup, eligibilă numai ca prim segment; nu rescrie originea grupului și nu inventează o conexiune de la un zbor sosit. Returul se leagă de o revizie exactă a zborului ulterior și de marja declarată de procesare. Google furnizează o predicție rutieră pentru ora viitoare și coordonatele cerute, nu garanții de acces pietonal, încărcare, terminal sau îmbarcare. Capacitatea cotației private se referă la grupul solicitat, nu la numărul vehiculelor din flotă.

Resursele cu valabilitate încheiată sau prospețime necunoscută sunt etichetate explicit și nu primesc numărători obișnuite de disponibilitate. Cotațiile fără expirare explicită, dar cu o oră de sursă admisă, folosesc o fereastră locală de 120 de secunde. Pentru APL, ora și expirarea furnizorului rămân necunoscute: limita este de 120 de secunde de la recepția aplicației; returul respectă și limita mai timpurie a recepției Google. Aceasta nu este o reținere la furnizor. Inspectorul arată orele în UTC. Aceste indicii se actualizează fără interogări de furnizor și nu anulează confirmările de rezervare înregistrate; verificarea backend rămâne autoritară.

## 6. Propunere, preferință și confirmare

- **Propus:** există un itinerar verificat pe datele și versiunea curente; evidențele locale previn alocări contradictorii în aplicație, dar nu rețin stoc la furnizor.
- **Acceptat de pasager:** preferința este înregistrată; rezervarea externă rămâne în așteptare.
- **Refuzat sau expirat:** înregistrările locale asociate sunt eliberate. O cotație veche nu este prezentată drept ofertă activă.
- **Confirmat extern:** operatorul furnizează referințe pentru fiecare segment al unui grup real. Acestea sunt afirmații documentate ale operatorului, nu verificare automată prin API-ul furnizorului.

Operațiile sunt idempotente și autorizate pentru grupul respectiv. Un rezultat incert păstrează cererea originală pentru recuperare. Grupurile deja confirmate extern nu sunt înlocuite automat de o nouă căutare pe incident. Alocările neafectate din afara ariei selectate sunt păstrate.

## 7. Interfață și direcție vizuală

Harta domină spațiul de lucru; lista de resurse, sumarul cazului, cronologia și inspectorul oferă context. Designul păstrează panouri clare, tipografie lizibilă, accente cyan/amber/mint și teme luminoasă/întunecată. Culorile sunt însoțite de text și simboluri.

| Nivel                | Scop                                                                        |
| -------------------- | --------------------------------------------------------------------------- |
| Regiune              | Compararea aeroporturilor, stațiilor, proprietăților și serviciilor sursate |
| Aeroport/proprietate | Localizarea punctelor relevante și a legăturilor lipsă                      |
| Pasager              | Grupul, constrângerile, propunerea și preferința sa                         |

Google Maps 3D a randat geografia reală. Acoperirea detaliată a clădirilor și fidelitatea față de concepte nu sunt garantate. Scena locală Three.js și modelele proprii sunt reprezentări arhitecturale, nu măsurători. În live nu animăm cotațiile, autobuzele sau grupurile ca și cum ar avea poziții GPS observate. Traficul observat își păstrează sursa și vechimea.

### Repere de design păstrate

Aceste imagini au fost generate pentru conceptul inițial. Geometria, inventarul, numerele și etichetele de simulare din imagini sunt ilustrative; nu sunt capturi ale produsului actual sau dovezi de rezervare. Folosim compoziția și ierarhia vizuală, nu valorile fictive.

![Concept: vedere operațională 3D asupra aeroportului](mockups/01-operations-overview.png)

Vedere de ansamblu, selecție și inspector contextual.

![Concept: dependențe și alternative la pierderea unui transfer](mockups/02-bus-breakdown.png)

Relația dintre un serviciu indisponibil, grupurile afectate și alternativele reale.

![Concept: context regional și consecințe asupra călătoriei](mockups/03-flight-diversion.png)

Context regional pentru recuperarea pasagerilor. Produsul nu emite instrucțiuni de deviere și nu este un sistem de control al traficului aerian.

[Prompturile imaginilor](mockups/prompts.json) păstrează proveniența conceptelor istorice.

## 8. Arhitectură și interacțiune AI

| Componentă       | Responsabilitate actuală                                                                 |
| ---------------- | ---------------------------------------------------------------------------------------- |
| React/TypeScript | Spațiul operatorului, formularele de caz, progresul, harta și pagina pasagerului         |
| FastAPI          | Autorizare, contracte validate, integrarea furnizorilor și orchestrare                   |
| Sol Ultrafast    | Interpretare structurată și selecția căutărilor eligibile                                |
| Decisions        | Alegerea dintre propuneri calculate și validate                                          |
| Solver Python    | Fezabilitate, alocări, costuri și păstrarea ariei neafectate                             |
| SQLite           | Stare versionată, registrul căutărilor, idempotency și audit                             |
| SSE              | Actualizări ale stării comune către interfețe                                            |
| GPT-Live         | Transport vocal și delegare; verificarea unei conversații reale complete rămâne separată |

Modelele nu decid singure dacă o capacitate, un preț sau un transfer este valid. Backendul reverifică înainte de aplicare. Nicio decizie live nu este înlocuită cu un răspuns prestabilit când API-ul eșuează.

Comenzile vocale/text pot porni o căutare durabilă pentru un caz existent, identificat explicit prin ID sau printr-un zbor fără ambiguitate în cererea curentă. Sunt admise numai grupurile deja legate de acel caz; rezervările confirmate și alocările din afara selecției sunt protejate. Confirmarea vocală de pornire descrie cererea înregistrată, nu un itinerar final sau o rezervare.

Limita comenzii include apelul de interpretare: implicit opt apeluri în total, dintre care cel mult șapte pentru căutarea durabilă. Formularul cazului are separat limita apelurilor propriei căutări. O interpretare cu rezultat necunoscut nu este retrimisă automat. Formularul cazului și intrarea prin comandă text au probe reale descrise mai jos. Conversația cu microfon rămâne de verificat: o sesiune WebRTC reușită cu sunet sintetic nu validează microfonul, delegarea, redarea și întreruperea într-o conversație reală.

După un plan hotelier complet reușit, profilul exact se salvează pentru grup și caz. O comandă text/vocală explicită poate refolosi configurația cu toate cele trei moduri și cel puțin **11 apeluri totale**, incluzând interpretarea; limita implicită rămâne opt. Fără profil salvat se completează formularul. Reutilizarea reverifică identitatea, constrângerile și orele exacte înainte de noi căutări; nu mută datele și nu transformă cotațiile vechi în disponibilitate curentă. O rulare eșuată, anulată sau cu rezultat necunoscut nu înlocuiește profilul. Implementarea acestei căi nu constituie încă dovada unei recuperări complete prin voce în browser.

Cheile private rămân pe server. Cheia publică Maps pentru browser necesită restricțiile aplicației. Contextul temporar FR24 nu este copiat în exporturile portabile sau backupurile operaționale; retenția și drepturile furnizorilor rămân explicite.

## 9. Verificări reale și limite actuale

La 31 octombrie 2026 au fost observate următoarele rezultate pe instanța principală, în rulări succesive. Prețurile și cotațiile de mai jos sunt dovezi datate, nu disponibilitate curentă:

- Identificarea cursei FR24 EK5 DXB→LHR, crearea cazului și legarea unui grup explicit de planificare cu doi adulți.
- O căutare durabilă a folosit patru apeluri: Responses, două căutări Nuitee paralele și Decisions. Au fost întoarse 20 de cotații și înregistrată o propunere validă pentru doi pasageri, fără excepții; grupul existent din afara selecției a rămas neatins.
- Circuitul căutării a durat aproximativ 12,1 secunde într-o singură rulare. Nu este o promisiune de latență sau o medie/p95.
- Prima verificare APL a păstrat șase cotații reale în GBP per vehicul și a afișat estimări aproximative în EUR din cursul ECB din 30 octombrie 2026, 1 EUR = 0,83380 GBP. Această verificare istorică a afișării nu a creat segmente. Ulterior, importul manual în ambele direcții a fost verificat cu proprietatea exactă, cotația APL și Google Routes datat, fără rezervări sau modificarea grupurilor; returul a fost legat de zborul ulterior sursat.
- Importul hotelier prin interfață a adăugat nouă cotații EUR și nouă proprietăți reale din zona LHR, după verificarea automată a politicilor de ședere, fără confirmare suplimentară a operatorului. O proprietate a fost exclusă cu motiv sursat. Această probă de import a fost distinctă de planul complet verificat ulterior.
- Fluxul automat cu hotel a reușit pe API `95a0606` și interfața `03112f6`: exact zece apeluri, patru resurse noi alese și o propunere completă pentru doi adulți, fără cerințe nerezolvate. Transferurile, hotelul Park Avenue Bayswater Inn Hyde Park și zborul KLM LHR→AMS→OTP au totalizat aproximativ EUR 934,86, inclusiv GBP 118 evaluate la aproximativ EUR 141,52 prin ECB. Lumea a trecut de la versiunea 47 la 48; grupul din afara selecției a rămas neschimbat și nu s-a făcut nicio rezervare. Durata de la înregistrare până la aplicare a fost 9,495 secunde, o singură măsurare. Profilul salvat a fost revizuit în browser fără alte apeluri către furnizori; cotațiile au expirat ulterior normal.
- O comandă text în română a respectat limita de patru apeluri: o interpretare și trei apeluri ale căutării. Zece cotații Nuitee au produs o propunere pentru doi pasageri, fără excepții, păstrând grupul din afara cazului. Căutarea a durat 7,21 secunde fără interpretare, într-o singură observație.
- Pasagerul a acceptat o propunere reală ca preferință, după care cotația a expirat în mod normal. Nu a fost creată o rezervare la furnizor.
- Cotațiile vechi de zbor și hotel au fost afișate cu `Validity expired`, respectiv `Quote stale`, iar limita locală de 120 de secunde a fost vizibilă în UTC.

Planul hotelier automat verifică fluxul din formular până la surse reale, Decisions și aplicarea atomică. Nu demonstrează disponibilitate viitoare, rezervări, acoperire universală sau transport terestru în România. APL este limitat la transferurile UK suportate; pentru CRA/OTP/SBZ există piste documentate de cumpărare pentru consumatori, dar nu o integrare verificată de inventar rutier în aplicație. [Limitele și sursele românești](real-data.md#romanian-ground-transport-coverage-gap) sunt păstrate separat.

Recuperarea completă prin voce rămâne deschisă. Proba istorică `fe47825` a verificat transportul, delegarea și confirmarea vocală de pornire în 33 de secunde, cu patru apeluri backend, însă fără plan aprobat și fără păstrarea exactă a datei. A doua probă, autorizată separat la 21:32 UTC pe API `95a0606` și interfața `31f49a1`, s-a oprit la verificarea ASR din clientul privat după coruperea datei și numelui grupului: închidere confirmată în 17 secunde, zero interpretări backend, zero apeluri către furnizorii de căutare, zero joburi și lumea neschimbată la versiunea 49. Nu a existat confirmare vocală de job sau recuperare în patru segmente; proba nu a fost reluată. Contextul vocal inițial selectat a fost implementat ulterior și verificat offline, fără probă plătită care să dovedească îmbunătățirea recunoașterii. [Raportul detaliat](verification/report.md#saved-hotel-profile--single-authorized-voice-probe) separă aceste rezultate de succesul formularului; microfonul fizic, redarea și întreruperea în browser rămân de verificat.

## 10. Criterii de acceptare ale fluxului live asistat

- Selectarea exactă a zborului și a grupurilor precedă căutările; un caz nou nu șterge operațiunea.
- Fiecare serviciu folosit are proveniență, orar, cost și capacitate suficiente pentru verificare. Necunoscutele nu devin zero sau stoc disponibil.
- Propunerea ajunge la destinație și păstrează grupul; hotelul fără conexiuni fizice nu trece drept recuperare completă.
- Rezultatele întârziate sunt reverificate; cererile repetate nu dublează aplicarea sau expedierile deja înregistrate.
- Anularea și rezultatele necunoscute sunt explicite, fără retry automat al apelurilor taxabile.
- Alternativa exclusă, cerința nerezolvată și așteptarea informativă sunt prezentate separat.
- Acceptarea de pe telefon este preferință, iar confirmarea externă necesită dovezi pentru toate segmentele.
- Fluxul real zbor→caz→cotații→propunere și fluxul limitat cu hotel, două transferuri UK și zbor au verificări distincte reușite. Recuperarea vocală completă necesită propria verificare; probele formularului sau de transport audio nu o substituie.
- Harta, atribuirea surselor și panourile sunt lizibile pe desktop și telefon; rezultatele nu folosesc imagini de concept drept fundal operațional.

## 11. Operare și dezvoltare internă

Instanța principală rămâne cea de la portul 8080. Actualizările păstrează baza persistentă și nu resetează starea utilizatorului. Publicarea pe un domeniu HTTPS este o etapă separată; nu există o instanță publică declarată în această verificare.

Modul intern `simulation` și fixture-urile sintetice sunt păstrate pentru regresii și dezvoltare izolată. Nu sunt surse de inventar pentru live, nu apar ca alternativă la un furnizor eșuat și nu fac parte din fluxul comercial descris aici. Resetarea sintetică este refuzată în live.

Limitele de apeluri controlează o căutare, nu factura totală a contului. Costurile furnizorilor și condițiile conturilor se verifică înainte de operare; nu păstrăm în această specificație tarifele orientative ale conceptului inițial drept prețuri actuale.

Produsul actual oferă recuperare asistată pentru grupurile adulte suportate, cu propuneri reale și verificabile, plus afișarea clară a serviciilor lipsă. Integrarea rezervărilor automate, confirmarea furnizorilor și extinderea acoperirii transportului terestru sunt proiecte distincte. O estimare de rută sau de curs valutar nu le substituie.

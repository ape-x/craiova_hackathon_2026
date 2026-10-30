import { useEffect, useState } from "react";
import {
  ArrowRight,
  Bus,
  Check,
  Clock3,
  Hotel,
  MapPin,
  RefreshCw,
  ShieldCheck,
  Ticket,
  Utensils,
  Users,
} from "lucide-react";
import { api, date, money, time } from "./api";
import type { Passenger } from "./types";
import { BrandMark } from "./components/BrandMark";

export function PassengerPage({
  token,
  embedded = false,
}: {
  token: string;
  embedded?: boolean;
}) {
  const [data, setData] = useState<Passenger | null>(null);
  const [error, setError] = useState("");
  const load = () =>
    api<Passenger>(`/passenger/${encodeURIComponent(token)}`)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [token]);
  const allConfirmed = data?.hotel && data?.transfer;
  return (
    <div className={`passenger-shell ${embedded ? "embedded" : ""}`}>
      <header className="passenger-brand">
        <BrandMark />
        <strong>
          stranded<span>OS</span>
        </strong>
        <span className="sim-small">SIMULARE</span>
      </header>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {!data && !error && (
        <div className="loading">Se încarcă asistența ta…</div>
      )}
      {data && (
        <main className="passenger-main">
          <div className="passenger-intro">
            <span className="eyebrow">
              ASISTENȚĂ PENTRU ZBORUL {data.flight}
            </span>
            <h1>
              O pauză.
              <br />
              Ne ocupăm noi.
            </h1>
            <p>
              {data.group.label}, toate detaliile asistenței tale, într-un
              singur loc.
            </p>
            <div className="passenger-group-chip">
              <Users size={14} />
              {data.group.size}{" "}
              {data.group.size === 1 ? "persoană" : "persoane"}
              <span />
              Grup {data.group.id}
            </div>
          </div>
          <div className={`passenger-status ${allConfirmed ? "complete" : ""}`}>
            {allConfirmed ? <ShieldCheck size={21} /> : <Clock3 size={21} />}
            <div>
              <strong>
                {allConfirmed
                  ? "Serviciile tale sunt confirmate"
                  : "Organizăm asistența pentru tine"}
              </strong>
              <p>
                {allConfirmed
                  ? "Urmează instrucțiunile de mai jos."
                  : "Afișăm fiecare serviciu după confirmarea furnizorului."}
              </p>
            </div>
          </div>
          <section className="passenger-card">
            <div className="passenger-card-title">
              <span className="icon-tile">
                <Hotel size={21} />
              </span>
              <h2>
                Cazarea ta<small>01 / ODIHNĂ</small>
              </h2>
              {data.hotel && (
                <span className="badge confirmed">
                  <Check size={12} />
                  Confirmată
                </span>
              )}
            </div>
            {data.hotel ? (
              <>
                <h3>{data.hotel.name}</h3>
                <p className="location">
                  <MapPin size={15} />
                  {data.hotel.address}
                </p>
                <div className="info-line">
                  <span>
                    {data.hotel.rooms} cameră/camere · {data.group.size}{" "}
                    persoane
                  </span>
                  <span>Până la {date(data.end)}</span>
                </div>
                <p className="small muted">
                  Prezintă documentul de identitate și codul grupului{" "}
                  <strong>{data.group.id}</strong> la recepție.
                </p>
              </>
            ) : (
              <p className="pending-copy">
                În așteptarea confirmării hotelului. Revino la această pagină
                pentru actualizări.
              </p>
            )}
          </section>
          <section className="passenger-card">
            <div className="passenger-card-title">
              <span className="icon-tile">
                <Bus size={21} />
              </span>
              <h2>
                Transferul tău<small>02 / ÎMPREUNĂ, LA HOTEL</small>
              </h2>
              {data.transfer && (
                <span className="badge confirmed">
                  <Check size={12} />
                  Confirmat
                </span>
              )}
            </div>
            {data.transfer ? (
              <>
                <div className="departure">
                  <strong>{time(data.transfer.departure_at)}</strong>
                  <span>
                    {data.transfer.provider}
                    <br />
                    {data.transfer.label}
                  </span>
                </div>
                <div className="meeting">
                  <MapPin size={20} />
                  <div>
                    <strong>Punct de întâlnire</strong>
                    <p>{data.transfer.meeting_point}</p>
                  </div>
                </div>
                <p className="small muted">
                  Rămâneți împreună. Prezentați-vă cu 10 minute înainte de
                  plecare.
                </p>
              </>
            ) : (
              <p className="pending-copy">
                Transferul va apărea după confirmarea hotelului și a
                transportatorului.
              </p>
            )}
          </section>
          <section className="passenger-card voucher">
            <div className="passenger-card-title">
              <span className="icon-tile">
                <Utensils size={21} />
              </span>
              <h2>
                Voucher de masă<small>03 / O MASĂ CALDĂ</small>
              </h2>
              <Ticket size={20} />
            </div>
            {data.voucher ? (
              <>
                <div className="voucher-amount">
                  {money(data.voucher.value)} <span>RON / grup</span>
                </div>
                <p>
                  {data.voucher.per_person} RON / persoană ·{" "}
                  {data.voucher.venue}
                </p>
                <div className="voucher-code">{data.voucher.code}</div>
                <p className="small muted">
                  Valabil până la {date(data.voucher.expires_at)},{" "}
                  {time(data.voucher.expires_at)}. Cod demonstrativ, fără
                  valoare comercială.
                </p>
              </>
            ) : (
              <p className="pending-copy">
                Voucherul se emite când hotelul confirmă serviciile de cazare și
                masă.
              </p>
            )}
          </section>
          <div className="passenger-help">
            <Users size={19} />
            <div>
              <strong>Ai nevoie de ajutor?</strong>
              <p>
                Adresează-te coordonatorului de la punctul de întâlnire B din
                Terminalul Sosiri.
              </p>
            </div>
          </div>
          <button className="btn secondary full" onClick={load}>
            <RefreshCw size={16} />
            Actualizează serviciile
          </button>
          <p className="passenger-disclaimer">
            Mediu demonstrativ · Toate datele și serviciile sunt fictive. Nicio
            rezervare sau notificare reală.
          </p>
          {embedded && (
            <a
              className="text-link"
              href={`/p/${token}`}
              target="_blank"
              rel="noreferrer"
            >
              Deschide pagina pasagerului <ArrowRight size={15} />
            </a>
          )}
        </main>
      )}
    </div>
  );
}

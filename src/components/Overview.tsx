import { useState, type ReactNode } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Accessibility,
  Building2,
  Bus,
  Check,
  CircleCheck,
  Clock3,
  GitCompareArrows,
  History,
  MapPin,
  Plane,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  Users,
  Wallet,
} from "lucide-react";
import { date, money, time } from "../api";
import type { Hotel, Status, Workspace } from "../types";
import { Badge, Budget, Card } from "./UI";

type Props = {
  s: Workspace;
  busy: boolean;
  onGenerate: () => void;
  onApprove: () => void;
  onResponses: () => void;
  onNavigate: (tab: "plan" | "suppliers" | "passengers" | "history") => void;
  onExceptions: () => void;
  onCompare: () => void;
};
const active = (status: Status) => status === "held" || status === "confirmed";

export function Overview({
  s,
  busy,
  onGenerate,
  onApprove,
  onResponses,
  onNavigate,
  onExceptions,
  onCompare,
}: Props) {
  const [resource, setResource] = useState<"hotels" | "transfers" | "network">(
    "hotels",
  );
  const m = s.metrics;
  const hasPlan = s.plan_version > 0;
  const pending =
    s.assignments.some((a) => a.hotel_status === "held") ||
    s.trips.some((t) => t.status === "held");
  const disrupted =
    s.assignments.some((a) =>
      ["refused", "expired"].includes(a.hotel_status),
    ) || s.trips.some((t) => ["refused", "expired"].includes(t.status));
  const step = !hasPlan
    ? 0
    : disrupted || s.approval_required
      ? 1
      : pending
        ? 2
        : 3;
  const title = disrupted
    ? "Un furnizor nu mai poate asigura serviciile"
    : !hasPlan
      ? "Un plan pentru fiecare pasager."
      : s.approval_required
        ? "Recomandarea e gata. Tu decizi."
        : pending
          ? "Buget aprobat. Așteptăm furnizorii."
          : "Confirmările au fost înregistrate";
  const description = disrupted
    ? "Recalculează grupurile afectate. Păstrăm confirmările care pot fi folosite în continuare."
    : !hasPlan
      ? `${m.passengers} pasageri importați. Găsește camerele și transferurile potrivite, fără să separi familiile.`
      : s.approval_required
        ? "Verifică alocările și autorizează costul. Furnizorii confirmă serviciile într-o etapă separată."
        : pending
          ? "Simulează răspunsurile partenerilor: Northline refuză, iar ceilalți furnizori confirmă."
          : `${m.confirmed} pasageri cu servicii confirmate. ${m.exceptions ? `${m.exceptions} au în continuare nevoie de intervenție.` : "Instrucțiunile și voucherele sunt pregătite."}`;
  const label = disrupted
    ? "Recalculează"
    : !hasPlan
      ? "Generează planul"
      : s.approval_required
        ? "Verifică & aprobă"
        : pending
          ? "Simulează răspunsuri"
          : "Vezi pasagerii";
  const next =
    disrupted || !hasPlan
      ? onGenerate
      : s.approval_required
        ? onApprove
        : pending
          ? onResponses
          : () => onNavigate("passengers");
  return (
    <>
      <section className="incident-ribbon" aria-label="Incidentul activ">
        <div className="incident-identity">
          <span className="flight-icon">
            <Plane size={19} />
          </span>
          <strong>{s.incident.flight}</strong>
          <span className="ribbon-route">
            {s.incident.airport.split(" · ")[0]}
            <ArrowRight size={13} />
            {s.incident.destination}
          </span>
          <span className="cancelled">Zbor anulat</span>
        </div>
        <div className="ribbon-details">
          <span>{date(s.incident.start)}</span>
          <span>
            <Clock3 size={13} />
            {time(s.incident.start)}–{time(s.incident.end)}
          </span>
          <span className="live-indicator">
            <i />
            Incident activ
          </span>
        </div>
      </section>
      <section className="stats-grid" aria-label="Situația incidentului">
        <Metric
          title="Pasageri în incident"
          value={m.passengers}
          detail={`${s.groups.length} grupuri · ${s.groups.filter((g) => g.accessible).length} cu accesibilitate`}
          icon={<Users size={17} />}
        />
        <Metric
          title="Servicii confirmate"
          value={m.confirmed}
          suffix={`/ ${m.eligible}`}
          detail="Cazare, transfer și masă"
          icon={<ShieldCheck size={17} />}
          progress={m.eligible ? m.confirmed / m.eligible : 0}
        />
        <Metric
          title="Necesită intervenție"
          value={hasPlan ? m.exceptions : "—"}
          detail={
            hasPlan
              ? m.exceptions
                ? "Vezi pasagerii fără soluție"
                : "Nicio excepție în plan"
              : "Apar după generarea planului"
          }
          icon={<TriangleAlert size={17} />}
          onClick={onExceptions}
          alert={hasPlan && m.exceptions > 0}
        />
        <Metric
          title="Costul planului"
          value={money(m.total_cost)}
          suffix="RON"
          detail={`Buget de ${money(s.incident.budget)} RON`}
          icon={<Wallet size={17} />}
        />
      </section>
      <div className="overview-layout">
        <div className="overview-primary">
          <section className={`focus-card ${disrupted ? "is-urgent" : ""}`}>
            <div className="focus-main">
              <div className="focus-copy">
                <div className="focus-eyebrow">
                  <span className="focus-spark">
                    {disrupted ? (
                      <TriangleAlert size={15} />
                    ) : (
                      <Sparkles size={15} />
                    )}
                  </span>
                  {disrupted ? "ACȚIUNE NECESARĂ" : "URMĂTORUL PAS"}
                </div>
                <h2>{title}</h2>
                <p>{description}</p>
                <div className="focus-actions">
                  <button
                    className="btn primary"
                    disabled={busy || (!hasPlan && !s.groups.length)}
                    onClick={next}
                  >
                    {label}
                    <ArrowRight size={16} />
                  </button>
                  {!hasPlan && (
                    <button
                      className="btn ghost"
                      onClick={() => onNavigate("passengers")}
                    >
                      Vezi pasagerii
                    </button>
                  )}
                  {hasPlan && s.approval_required && !disrupted && (
                    <button
                      className="btn ghost"
                      onClick={() => onNavigate("plan")}
                    >
                      Explorează planul
                    </button>
                  )}
                </div>
              </div>
              <OrbitArtwork
                complete={m.confirmed === m.eligible && hasPlan}
                disrupted={disrupted}
              />
            </div>
            <ol className="workflow" aria-label="Etapele asistenței">
              {[
                "Incident deschis",
                "Plan & buget",
                "Confirmări",
                "Asistență pasageri",
              ].map((name, i) => (
                <li
                  key={name}
                  className={`workflow-step ${i < step ? "done" : ""} ${i === step ? "current" : ""}`}
                  aria-current={i === step ? "step" : undefined}
                >
                  <span>{i < step ? <Check size={12} /> : i + 1}</span>
                  <div>
                    {name}
                    <small>
                      {i < step
                        ? "Finalizat"
                        : i === step
                          ? "În desfășurare"
                          : "Urmează"}
                    </small>
                  </div>
                </li>
              ))}
            </ol>
          </section>
          <Card
            title="Resursele incidentului"
            sub="Partenerii tăi contractuali, într-un singur loc."
            action={
              <button
                className="icon-button"
                aria-label="Vezi furnizorii"
                onClick={() => onNavigate("suppliers")}
              >
                <ArrowUpRight size={18} />
              </button>
            }
          >
            <div
              className="resource-tabs"
              role="tablist"
              aria-label="Tipul resurselor"
            >
              {(
                [
                  {
                    id: "hotels",
                    label: "Cazare",
                    count: s.hotels.length,
                    icon: Building2,
                  },
                  {
                    id: "transfers",
                    label: "Transferuri",
                    count: new Set(s.vehicles.map((v) => v.provider_id)).size,
                    icon: Bus,
                  },
                  { id: "network", label: "Rețea", count: null, icon: MapPin },
                ] as const
              ).map((item) => (
                <button
                  key={item.id}
                  id={`resource-${item.id}`}
                  role="tab"
                  aria-selected={resource === item.id}
                  tabIndex={resource === item.id ? 0 : -1}
                  onKeyDown={(event) => {
                    if (
                      event.key === "ArrowRight" ||
                      event.key === "ArrowLeft"
                    ) {
                      event.preventDefault();
                      const order = ["hotels", "transfers", "network"] as const;
                      const index = order.indexOf(item.id);
                      const next =
                        order[
                          (index + (event.key === "ArrowRight" ? 1 : 2)) % 3
                        ];
                      setResource(next);
                      document.getElementById(`resource-${next}`)?.focus();
                    }
                  }}
                  aria-controls="resource-panel"
                  onClick={() => setResource(item.id)}
                >
                  <item.icon size={15} />
                  {item.label}
                  {item.count !== null && <span>{item.count}</span>}
                </button>
              ))}
            </div>
            <div
              className="resource-panel"
              role="tabpanel"
              tabIndex={0}
              id="resource-panel"
              aria-labelledby={`resource-${resource}`}
              key={resource}
            >
              {resource === "hotels" && <HotelResources s={s} />}
              {resource === "transfers" && (
                <div className="transport-summary">
                  {[...new Set(s.vehicles.map((v) => v.provider_id))].map(
                    (pid) => {
                      const vehicles = s.vehicles.filter(
                        (v) => v.provider_id === pid,
                      );
                      const trips = s.trips.filter(
                        (t) =>
                          vehicles.some((v) => v.id === t.vehicle_id) &&
                          active(t.status),
                      );
                      return (
                        <div className="transport-summary-row" key={pid}>
                          <span className="transport-icon">
                            <Bus size={21} />
                          </span>
                          <div>
                            <strong>{vehicles[0].provider}</strong>
                            <small>
                              {vehicles.length} vehicule ·{" "}
                              {vehicles.reduce((n, v) => n + v.seats, 0)} locuri
                              disponibile în catalog
                            </small>
                          </div>
                          <div className="transport-summary-end">
                            <strong>{trips.length} curse</strong>
                            <small>
                              {
                                trips.filter((t) => t.status === "confirmed")
                                  .length
                              }{" "}
                              confirmate
                            </small>
                          </div>
                          <button
                            className="icon-button"
                            aria-label={`Detalii ${vehicles[0].provider}`}
                            onClick={() => onNavigate("suppliers")}
                          >
                            <ArrowUpRight size={16} />
                          </button>
                        </div>
                      );
                    },
                  )}
                </div>
              )}
              {resource === "network" && <RouteNetwork s={s} />}
            </div>
            <div className="card-foot">
              <span>
                <ShieldCheck size={14} />
                Familiile rămân împreună
              </span>
              <button className="text-link" onClick={() => onNavigate("plan")}>
                Deschide planul
                <ArrowRight size={14} />
              </button>
            </div>
          </Card>
        </div>
        <aside className="overview-secondary" aria-label="Buget și activitate">
          <Card
            title="Situația bugetului"
            action={<span className="currency-label">RON</span>}
          >
            <Budget s={s} />
            <div className="budget-compare">
              <button className="text-link" onClick={onCompare}>
                <GitCompareArrows size={15} />
                Compară cu regula simplă
                <ArrowUpRight size={14} />
              </button>
            </div>
          </Card>
          <Card
            title="Activitate recentă"
            action={<History size={17} className="muted" />}
          >
            <div className="recent-activity">
              {[...s.audit]
                .reverse()
                .slice(0, 3)
                .map((a, i) => (
                  <div className="recent-item" key={a.id}>
                    <span
                      className={`activity-dot ${a.action.includes("Refuz") ? "warning" : ""}`}
                    />
                    <div>
                      <strong>{a.action}</strong>
                      <p>{a.actor.split(" · ")[0]}</p>
                    </div>
                    <time>{time(a.at)}</time>
                    {i < Math.min(s.audit.length, 3) - 1 && (
                      <i className="activity-line" />
                    )}
                  </div>
                ))}
            </div>
            <button
              className="activity-link"
              onClick={() => onNavigate("history")}
            >
              Tot istoricul
              <ArrowRight size={14} />
            </button>
          </Card>
          <div className="operator-note">
            <CircleCheck size={16} />
            <p>
              Recomandarea te ajută să decizi.
              <br />
              <strong>Controlul rămâne la tine.</strong>
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}

function Metric({
  title,
  value,
  suffix,
  detail,
  icon,
  onClick,
  progress,
  alert,
}: {
  title: string;
  value: string | number;
  suffix?: string;
  detail: string;
  icon: ReactNode;
  onClick?: () => void;
  progress?: number;
  alert?: boolean;
}) {
  const content = (
    <>
      <div className="stat-top">
        <span>{title}</span>
        {icon}
      </div>
      <div className={`stat-value ${alert ? "attention" : ""}`}>
        {value}
        <small>{suffix}</small>
      </div>
      <div className="stat-bottom">
        <p>{detail}</p>
        {onClick && <ArrowUpRight size={13} />}
      </div>
      {progress !== undefined && (
        <div className="metric-progress">
          <i style={{ width: `${progress * 100}%` }} />
        </div>
      )}
    </>
  );
  return onClick ? (
    <button className="stat-card clickable" onClick={onClick}>
      {content}
    </button>
  ) : (
    <div className="stat-card">{content}</div>
  );
}

function HotelResources({ s }: { s: Workspace }) {
  const status = (hotel: Hotel): Status => {
    if (hotel.status === "refused") return "refused";
    const rows = s.assignments.filter((a) => a.hotel_id === hotel.id);
    return rows.length
      ? rows.every((a) => a.hotel_status === "confirmed")
        ? "confirmed"
        : rows.some((a) => a.hotel_status === "held")
          ? "held"
          : rows[0].hotel_status
      : "available";
  };
  return (
    <div className="table-scroll">
      <table className="hotel-table">
        <thead>
          <tr>
            <th>Hotel</th>
            <th>Camere</th>
            <th>Pasageri</th>
            <th>Disponibilitate</th>
          </tr>
        </thead>
        <tbody>
          {s.hotels.map((h, i) => {
            const rows = s.assignments.filter(
              (a) => a.hotel_id === h.id && active(a.hotel_status),
            );
            const used = rows.reduce((n, a) => n + a.room_ids.length, 0);
            return (
              <tr key={h.id}>
                <td>
                  <div className="hotel-cell">
                    <span className={`hotel-thumb color-${i}`}>H{i + 1}</span>
                    <div>
                      <strong>{h.name}</strong>
                      <small>
                        {h.area}
                        <span className="inline-separator">·</span>
                        {h.distance_km} km de aeroport
                        {h.rooms.some((r) => r.accessible) && (
                          <Accessibility size={12} />
                        )}
                      </small>
                    </div>
                  </div>
                </td>
                <td>
                  <strong>{used}</strong>
                  <span className="muted"> / {h.rooms.length}</span>
                  <div className="mini-track">
                    <i style={{ width: `${(used / h.rooms.length) * 100}%` }} />
                  </div>
                </td>
                <td>
                  {rows.reduce(
                    (n, a) =>
                      n +
                      (s.groups.find((g) => g.id === a.group_id)?.size || 0),
                    0,
                  )}
                </td>
                <td>
                  <Badge status={status(h)} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OrbitArtwork({
  complete,
  disrupted,
}: {
  complete: boolean;
  disrupted: boolean;
}) {
  return (
    <div
      className={`orbit-artwork ${disrupted ? "disrupted" : ""}`}
      aria-hidden="true"
    >
      <div className="orbit-glow" />
      <div className="orbit-ring outer" />
      <div className="orbit-ring middle" />
      <div className="orbit-ring inner" />
      <span className="orbit-center">
        {complete ? (
          <ShieldCheck size={30} strokeWidth={1.3} />
        ) : (
          <Plane size={29} strokeWidth={1.3} />
        )}
      </span>
      <span className="orbit-node node-hotel">
        <Building2 size={19} strokeWidth={1.5} />
        <i />
      </span>
      <span className="orbit-node node-bus">
        <Bus size={18} strokeWidth={1.5} />
      </span>
      <span className="orbit-node node-people">
        <Users size={17} strokeWidth={1.5} />
      </span>
      <span className="orbit-coordinate c1">OTP</span>
      <span className="orbit-coordinate c2">
        {complete ? "ASISTENȚĂ CONFIRMATĂ" : "ASISTENȚĂ CONECTATĂ"}
      </span>
    </div>
  );
}

function RouteNetwork({ s }: { s: Workspace }) {
  return (
    <div className="route-network">
      <div className="network-origin">
        <span className="flight-icon">
          <Plane size={22} />
        </span>
        <strong>{s.incident.airport.split(" · ")[0]}</strong>
        <small>Punctul de întâlnire B · Terminal Sosiri</small>
      </div>
      <div className="network-destinations">
        {s.hotels.map((h, i) => (
          <div
            className={`network-destination ${h.status === "refused" ? "is-refused" : ""}`}
            key={h.id}
          >
            <span>H{i + 1}</span>
            <strong>{h.name}</strong>
            <small>
              {h.travel_minutes} min · {h.distance_km} km
            </small>
          </div>
        ))}
      </div>
      <p>Rețea schematică · distanțe și disponibilități simulate</p>
    </div>
  );
}

import type { ReactNode } from "react";
import { CheckCircle2, ListChecks, TriangleAlert } from "lucide-react";
import { money } from "../api";
import type { Status, Workspace } from "../types";

const statusLabels: Record<Status, string> = {
  available: "Disponibil",
  held: "Rezervat temporar",
  confirmed: "Confirmat",
  refused: "Refuzat",
  expired: "Expirat",
};

export function Badge({ status }: { status: Status }) {
  return (
    <span className={`badge ${status}`}>
      <span className="status-dot" />
      {statusLabels[status]}
    </span>
  );
}
export function Card({
  title,
  sub,
  action,
  children,
  className = "",
}: {
  title: string;
  sub?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          {sub && <p>{sub}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <ListChecks size={29} />
      <p>{children}</p>
    </div>
  );
}

export function Budget({ s }: { s: Workspace }) {
  const m = s.metrics;
  const over = m.total_cost > s.incident.budget;
  return (
    <div className="budget-body">
      <div className="budget-number">
        {money(m.total_cost)} <span>RON</span>
        <small>din {money(s.incident.budget)} RON alocați</small>
      </div>
      <div className={`progress-track ${over ? "over" : ""}`}>
        <i
          style={{
            width: `${Math.min(100, (m.total_cost / s.incident.budget) * 100)}%`,
          }}
        />
      </div>
      <div className="budget-remaining">
        {over ? <TriangleAlert size={13} /> : <CheckCircle2 size={13} />}{" "}
        {over
          ? `Depășire: ${money(m.total_cost - s.incident.budget)} RON`
          : `Disponibil: ${money(s.incident.budget - m.total_cost)} RON`}
        <span>{Math.round((m.total_cost / s.incident.budget) * 100)}%</span>
      </div>
      <div className="cost-breakdown">
        <div>
          <span>
            <i className="cost-dot hotel" />
            Cazare
          </span>
          <strong>{money(m.hotel_cost)} RON</strong>
        </div>
        <div>
          <span>
            <i className="cost-dot transport" />
            Transport
          </span>
          <strong>{money(m.transport_cost)} RON</strong>
        </div>
        <div>
          <span>
            <i className="cost-dot meal" />
            Vouchere de masă
          </span>
          <strong>{money(m.meal_cost)} RON</strong>
        </div>
      </div>
      <div className="budget-bottom">
        <span>Cost / pasager asistat</span>
        <strong>
          {m.covered ? money(m.total_cost / m.covered) : "—"}{" "}
          {m.covered ? "RON" : ""}
        </strong>
      </div>
    </div>
  );
}

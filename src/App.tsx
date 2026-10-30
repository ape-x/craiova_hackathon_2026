import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Building2,
  Bus,
  Check,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  FileSpreadsheet,
  GitCompareArrows,
  History,
  Hotel as HotelIcon,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  MapPin,
  Plane,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Ticket,
  TriangleAlert,
  Upload,
  Users,
  X,
  Accessibility,
  PanelLeftClose,
  PanelLeftOpen,
  Moon,
  Sun,
  ChevronLeft,
} from "lucide-react";
import { api, date, money, time } from "./api";
import { PassengerPage } from "./Passenger";
import { BrandMark } from "./components/BrandMark";
import { Overview } from "./components/Overview";
import { Badge, Budget, Card, Empty } from "./components/UI";
import { CommandPalette, type Command } from "./components/CommandPalette";
import type { Group, Hotel, Metrics, Status, Workspace } from "./types";

const statusLabels: Record<Status, string> = {
  available: "Disponibil",
  held: "Rezervat temporar",
  confirmed: "Confirmat",
  refused: "Refuzat",
  expired: "Expirat",
};
const active = (status: Status) => status === "held" || status === "confirmed";
type Tab = "overview" | "plan" | "suppliers" | "passengers" | "history";
type Modal =
  | "command"
  | "new"
  | "approve"
  | "compare"
  | "reset"
  | "help"
  | "import"
  | "adjust"
  | "passenger"
  | null;
const nav: { id: Tab; name: string; icon: typeof Activity }[] = [
  { id: "overview", name: "Panou de incident", icon: LayoutDashboard },
  { id: "plan", name: "Plan de alocare", icon: ListChecks },
  { id: "suppliers", name: "Furnizori", icon: Building2 },
  { id: "passengers", name: "Pasageri", icon: Users },
  { id: "history", name: "Istoric acțiuni", icon: History },
];

function preference(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

export function App() {
  const [collapsed, setCollapsed] = useState(
    () => preference("stranded-sidebar", "expanded") === "collapsed",
  );
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    preference("stranded-theme", "light") === "dark" ? "dark" : "light",
  );
  const [passengerPage, setPassengerPage] = useState(1);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute("content", theme === "dark" ? "#202020" : "#fcfcfb");
    try {
      localStorage.setItem("stranded-theme", theme);
    } catch {
      /* Storage can be disabled. */
    }
  }, [theme]);
  useEffect(() => {
    try {
      localStorage.setItem(
        "stranded-sidebar",
        collapsed ? "collapsed" : "expanded",
      );
    } catch {
      /* Storage can be disabled. */
    }
  }, [collapsed]);
  const [s, setS] = useState<Workspace | null>(null);
  const [tab, setTab] = useState<Tab>(
    nav.some((n) => n.id === location.hash.slice(1))
      ? (location.hash.slice(1) as Tab)
      : "overview",
  );
  const [modal, setModal] = useState<Modal>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [onlyExceptions, setOnlyExceptions] = useState(false);
  const [selected, setSelected] = useState<Group | null>(null);
  const [compare, setCompare] = useState<{
    recommended: Metrics;
    baseline: Metrics;
    assumption: string;
  } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setModal((current) =>
          current === "command" ? null : current ? current : "command",
        );
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  useEffect(() => {
    setPassengerPage(1);
  }, [query, onlyExceptions]);
  const refresh = () =>
    api<Workspace>("/workspace")
      .then((data) =>
        setS((previous) =>
          !previous || data.revision >= previous.revision ? data : previous,
        ),
      )
      .catch((e) => setError(e.message));
  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    const listener = () => {
      const hash = location.hash.slice(1);
      if (nav.some((n) => n.id === hash)) setTab(hash as Tab);
    };
    window.addEventListener("hashchange", listener);
    return () => window.removeEventListener("hashchange", listener);
  }, []);
  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(""), 6500);
    return () => clearTimeout(id);
  }, [notice]);
  async function mutate(path: string, body: object = {}, success?: string) {
    if (!s || busy) return false;
    setBusy(true);
    setError("");
    try {
      const data = await api<Workspace>(path, {
        revision: s.revision,
        ...body,
      });
      setS(data);
      if (success) setNotice(success);
      return true;
    } catch (e) {
      setError((e as Error).message);
      await refresh();
      return false;
    } finally {
      setBusy(false);
    }
  }
  const navigate = (id: Tab) => {
    window.scrollTo(0, 0);
    setTab(id);
    location.hash = id;
    setQuery("");
    setOnlyExceptions(false);
    setPassengerPage(1);
  };
  const showCompare = async () => {
    setCompare(null);
    setModal("compare");
    try {
      setCompare(await api("/comparison"));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const generate = () =>
    mutate(
      "/plan/generate",
      {},
      "Recomandarea este pregătită pentru verificare și aprobare.",
    );
  const providerResponse = (kind: string, id: string, response: string) =>
    mutate(
      `/providers/${kind}/${id}/respond`,
      {
        response,
        reason:
          response === "confirmed"
            ? "Furnizorul acceptă solicitarea în simulare."
            : "Furnizorul raportează indisponibilitate în simulare.",
      },
      response === "confirmed"
        ? "Confirmare înregistrată."
        : "Refuz înregistrat. Recalculează planul pentru grupurile afectate.",
    );
  const canRespond =
    !!s && s.approved_version === s.plan_version && s.plan_version > 0;
  const openPassenger = (group: Group) => {
    setSelected(group);
    setModal("passenger");
  };
  const handleImport = async (file?: File) => {
    if (!file) return;
    if (file.size > 1000000) {
      setError("CSV-ul poate avea maximum 1 MB.");
      return;
    }
    if (
      await mutate(
        "/passengers/import",
        { csv_text: await file.text() },
        "Pasagerii au fost importați și validați.",
      )
    )
      setModal(null);
  };
  const hotelStatus = (h: Hotel): Status => {
    if (h.status === "refused") return "refused";
    const rows = s?.assignments.filter((a) => a.hotel_id === h.id);
    return rows?.length
      ? rows.every((a) => a.hotel_status === "confirmed")
        ? "confirmed"
        : rows.some((a) => a.hotel_status === "held")
          ? "held"
          : rows[0].hotel_status
      : "available";
  };
  if (!s)
    return (
      <div className="initial-loading">
        <span className="logo-mark">
          <Plane />
        </span>
        <h1>
          stranded<span>OS</span>
        </h1>
        {error ? (
          <>
            <p role="alert">Backend indisponibil: {error}</p>
            <button className="btn primary" onClick={refresh}>
              Reîncearcă
            </button>
            <code>uvicorn backend.main:app --reload</code>
          </>
        ) : (
          <p>
            <LoaderCircle className="spin" size={17} />
            Se deschide centrul operațional…
          </p>
        )}
      </div>
    );
  const m = s.metrics;
  const pending =
    s.assignments.some((a) => a.hotel_status === "held") ||
    s.trips.some((t) => t.status === "held");
  const disrupted =
    s.assignments.some(
      (a) => a.hotel_status === "refused" || a.hotel_status === "expired",
    ) || s.trips.some((t) => t.status === "refused" || t.status === "expired");
  const hasPlan = s.plan_version > 0;
  const currentTitle = nav.find((n) => n.id === tab)!.name;
  const filteredGroups = s.groups.filter(
    (g) =>
      (!onlyExceptions || s.exceptions.some((e) => e.group_id === g.id)) &&
      `${g.id} ${g.label} ${g.members.map((member) => member.name).join(" ")}`
        .toLocaleLowerCase("ro")
        .includes(query.toLocaleLowerCase("ro")),
  );
  const pageCount = Math.max(1, Math.ceil(filteredGroups.length / 20));
  const currentPage = Math.min(passengerPage, pageCount);
  const pageGroups = filteredGroups.slice(
    (currentPage - 1) * 20,
    currentPage * 20,
  );
  const commands: Command[] = [
    {
      id: "theme",
      label:
        theme === "light"
          ? "Activează tema întunecată"
          : "Activează tema luminoasă",
      detail: "Aspectul spațiului de lucru",
      icon: theme === "light" ? Moon : Sun,
      run: () => {
        setTheme(theme === "light" ? "dark" : "light");
        setModal(null);
      },
    },
    ...nav.map((n) => ({
      id: n.id,
      label: n.name,
      detail: "Spațiu de lucru",
      icon: n.icon,
      run: () => {
        setModal(null);
        navigate(n.id);
      },
    })),
    {
      id: "new",
      label: "Incident nou",
      detail: "Deschide un nou scenariu",
      icon: Plus,
      run: () => setModal("new"),
    },
    {
      id: "compare",
      label: "Compară regulile de alocare",
      detail: "Recomandare și regulă simplă",
      icon: GitCompareArrows,
      run: () => {
        void showCompare();
      },
    },
    ...s.groups.map((g) => ({
      id: g.id,
      label: `${g.id} · ${g.label}`,
      detail: `${g.size} persoane · Registrul pasagerilor`,
      keywords: g.members.map((member) => member.name).join(" "),
      icon: Users,
      run: () => {
        setModal(null);
        navigate("passengers");
        setQuery(g.id);
      },
    })),
  ];

  return (
    <div className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      {busy && (
        <div
          className="global-progress"
          role="progressbar"
          aria-label="Se actualizează incidentul"
        >
          <span />
        </div>
      )}
      <aside className="sidebar">
        <div className="sidebar-top">
          <a
            className="brand"
            href="#overview"
            onClick={() => navigate("overview")}
            aria-label="StrandedOS · Panou de incident"
          >
            <BrandMark />
            <span>
              stranded<strong>OS</strong>
            </span>
          </a>
          <button
            className="icon-button sidebar-toggle"
            aria-label={
              collapsed ? "Extinde bara laterală" : "Restrânge bara laterală"
            }
            onClick={() => setCollapsed(!collapsed)}
            title={
              collapsed ? "Extinde bara laterală" : "Restrânge bara laterală"
            }
          >
            {collapsed ? (
              <PanelLeftOpen size={18} />
            ) : (
              <PanelLeftClose size={18} />
            )}
          </button>
        </div>
        <button
          className="workspace-selector"
          onClick={() => setModal("command")}
          aria-label="Deschide spațiul de lucru"
        >
          <span className="workspace-icon">O</span>
          <span className="workspace-name">
            Otopeni<small>Spațiu de coordonare</small>
          </span>
          <ChevronDown size={13} />
        </button>
        <button
          className="sidebar-search"
          onClick={() => setModal("command")}
          aria-label="Caută în StrandedOS"
        >
          <Search size={17} />
          <span>Caută în spațiu</span>
          <kbd>Ctrl K</kbd>
        </button>
        <div className="nav-label">SPAȚIU DE LUCRU</div>
        <nav aria-label="Navigare principală">
          {nav.map((n) => (
            <a
              href={`#${n.id}`}
              aria-label={n.name}
              title={collapsed ? n.name : undefined}
              aria-current={tab === n.id ? "page" : undefined}
              key={n.id}
              onClick={() => navigate(n.id)}
              className={`nav-item ${tab === n.id ? "selected" : ""}`}
            >
              <n.icon size={18} strokeWidth={1.6} />
              <span>{n.name}</span>
              {n.id === "passengers" && m.exceptions > 0 && hasPlan && (
                <span className="nav-count warning">{m.exceptions}</span>
              )}
            </a>
          ))}
        </nav>
        <div className="sidebar-incident">
          <div className="nav-label">
            INCIDENT ACTIV
            <i />
          </div>
          <a
            href="#overview"
            onClick={() => navigate("overview")}
            title={s.incident.flight}
          >
            <span className="incident-mini-icon">
              <Plane size={16} />
            </span>
            <div>
              <strong>{s.incident.flight}</strong>
              <small>
                {s.incident.airport.split(" · ")[0]} →{" "}
                {s.incident.destination.split(" · ").at(-1)}
              </small>
            </div>
            <span className="incident-mini-dot" />
          </a>
        </div>
        <div className="sidebar-bottom">
          <div className="sidebar-demo-note">
            <span className="live-dot" />
            <span>Mediu de simulare</span>
          </div>
          <button
            className="sidebar-control"
            onClick={() => setTheme(theme === "light" ? "dark" : "light")}
            aria-label={
              theme === "light"
                ? "Activează tema întunecată"
                : "Activează tema luminoasă"
            }
            title="Schimbă tema"
          >
            {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            <span>
              {theme === "light" ? "Temă întunecată" : "Temă luminoasă"}
            </span>
          </button>
          <button
            className="sidebar-control"
            onClick={() => setModal("help")}
            aria-label="Ajutor și ghid demo"
            title="Ajutor și ghid demo"
          >
            <CircleHelp size={17} />
            <span>Ajutor și ghid demo</span>
            <ArrowUpRight size={13} />
          </button>
          <div className="profile">
            <span className="avatar">AI</span>
            <div>
              Alexandra Ionescu<small>Coordonator operațional</small>
            </div>
            <span className="profile-dot" />
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>Operațiuni</span>
            <ChevronRight size={12} />
            <strong>{s.incident.id}</strong>
            <span className="topbar-live">
              <i />
              Activ
            </span>
          </div>
          <div className="topbar-right">
            <button
              className="icon-button mobile-command-button"
              aria-label="Caută în StrandedOS"
              onClick={() => setModal("command")}
            >
              <Search size={17} />
            </button>
            <span className="sim-label">
              <span />
              SIMULARE
            </span>
            <button
              className="icon-button notification-button"
              aria-label="Deschide intervențiile"
              onClick={() => {
                navigate("passengers");
                setOnlyExceptions(true);
              }}
            >
              <Bell size={18} strokeWidth={1.6} />
              {m.exceptions > 0 && hasPlan && <i />}
            </button>
            <span className="topbar-divider" />
            <button
              className="btn secondary topbar-new"
              onClick={() => setModal("new")}
            >
              <Plus size={16} />
              Incident nou
            </button>
          </div>
        </header>
        <main className="main-content">
          <div className="page-heading">
            <div>
              <div className="eyebrow page-eyebrow">
                {tab === "overview"
                  ? "SPAȚIUL TĂU DE COORDONARE"
                  : `INCIDENT ${s.incident.flight}`}
              </div>
              <h1>{currentTitle}</h1>
              <p>
                {tab === "overview"
                  ? "Mai puțină coordonare. Mai multă grijă pentru pasageri."
                  : tab === "plan"
                    ? "Recomandări explicabile, cu fiecare constrângere verificată."
                    : tab === "suppliers"
                      ? "Catalog contractual și răspunsuri simulate, într-un singur loc."
                      : tab === "passengers"
                        ? "Familii împreună. Nevoi vizibile. Asistență urmărită."
                        : "Fiecare decizie are un autor, un moment și un motiv."}
              </p>
            </div>
            <div className="heading-actions">
              <button
                className="btn primary"
                disabled={busy || !s.groups.length}
                onClick={generate}
              >
                {busy ? (
                  <LoaderCircle size={17} className="spin" />
                ) : (
                  <Sparkles size={17} />
                )}{" "}
                {hasPlan ? "Recalculează planul" : "Generează planul"}
              </button>
            </div>
          </div>
          {error && (
            <div className="alert error" role="alert">
              <TriangleAlert size={18} />
              <span>{error}</span>
              <button aria-label="Închide eroarea" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="toast" role="status">
              <CheckCircle2 size={18} />
              {notice}
            </div>
          )}

          <div className="view-content" key={tab}>
            {tab === "overview" && (
              <Overview
                s={s}
                busy={busy}
                onGenerate={generate}
                onApprove={() => setModal("approve")}
                onResponses={() =>
                  mutate(
                    "/demo/responses",
                    {},
                    "Răspunsurile simulate au fost înregistrate.",
                  )
                }
                onNavigate={navigate}
                onExceptions={() => {
                  navigate("passengers");
                  setOnlyExceptions(true);
                }}
                onCompare={showCompare}
              />
            )}

            {tab === "plan" && (
              <>
                <div className="plan-summary">
                  <div>
                    <span className="eyebrow">
                      {hasPlan
                        ? `RECOMANDARE · VERSIUNEA ${s.plan_version}`
                        : "NICIUN PLAN GENERAT"}
                    </span>
                    <h2>
                      {m.covered}{" "}
                      <span>/ {m.eligible} pasageri cu soluție completă</span>
                    </h2>
                    <p>{s.last_change}</p>
                  </div>
                  <div className="plan-summary-end">
                    <strong>
                      {money(m.total_cost)} <small>RON</small>
                    </strong>
                    <button
                      className="btn primary"
                      disabled={
                        busy || !hasPlan || !s.approval_required || disrupted
                      }
                      onClick={() => setModal("approve")}
                    >
                      <CheckCheck size={16} />
                      {s.approval_required ? "Aprobă bugetul" : "Buget aprobat"}
                    </button>
                  </div>
                </div>
                <div className="plan-toolbar">
                  <span>
                    <Clock3 size={15} />
                    Rezervări temporare: 20 minute
                  </span>
                  <button className="btn secondary small" onClick={showCompare}>
                    <GitCompareArrows size={16} />
                    Compară regulile
                  </button>
                </div>
                <div className="plan-grid">
                  {s.hotels.map((h) => {
                    const rows = s.assignments.filter(
                      (a) => a.hotel_id === h.id,
                    );
                    const groupCount = rows
                      .filter((a) => active(a.hotel_status))
                      .reduce(
                        (a, b) =>
                          a +
                          (s.groups.find((g) => g.id === b.group_id)?.size ||
                            0),
                        0,
                      );
                    return (
                      <Card
                        key={h.id}
                        title={h.name}
                        sub={`${h.area} · ${h.distance_km} km de aeroport`}
                        action={<Badge status={hotelStatus(h)} />}
                      >
                        <div className="hotel-plan-info">
                          <span>
                            <Users size={17} />
                            <strong>{groupCount}</strong> pasageri
                          </span>
                          <span>
                            <HotelIcon size={17} />
                            <strong>
                              {rows
                                .filter((a) => active(a.hotel_status))
                                .reduce((n, a) => n + a.room_ids.length, 0)}
                            </strong>{" "}
                            camere
                          </span>
                          <span>
                            <Bus size={17} />
                            <strong>
                              {
                                s.trips.filter(
                                  (t) =>
                                    t.hotel_id === h.id && active(t.status),
                                ).length
                              }
                            </strong>{" "}
                            transferuri
                          </span>
                        </div>
                        {!rows.length ? (
                          <Empty>Fără grupuri alocate acestui hotel.</Empty>
                        ) : (
                          <div className="group-list">
                            {rows.map((a) => {
                              const g = s.groups.find(
                                (g) => g.id === a.group_id,
                              )!;
                              const t = s.trips.find((t) => t.id === a.trip_id);
                              return (
                                <div className="group-row" key={a.group_id}>
                                  <div className="group-main">
                                    <strong>
                                      {g.id} · {g.label}
                                      {g.accessible > 0 && (
                                        <Accessibility size={15} />
                                      )}
                                    </strong>
                                    <span>
                                      {g.size} persoane · {a.room_ids.length}{" "}
                                      cameră/camere · {a.room_ids.join(", ")}
                                    </span>
                                    <small>{a.explanation}</small>
                                    <div className="group-statuses">
                                      <Badge status={a.hotel_status} />
                                      {t && (
                                        <span className="small muted">
                                          Transfer {time(t.departure_at)} ·{" "}
                                          {statusLabels[t.status]}
                                        </span>
                                      )}
                                    </div>
                                  </div>
                                  <button
                                    className="icon-button"
                                    aria-label={`Ajustează ${g.id}`}
                                    onClick={() => {
                                      setSelected(g);
                                      setModal("adjust");
                                    }}
                                  >
                                    <SlidersHorizontal size={17} />
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </Card>
                    );
                  })}
                </div>
                <Exceptions
                  s={s}
                  onAdjust={(g) => {
                    setSelected(g);
                    setModal("adjust");
                  }}
                />
              </>
            )}

            {tab === "suppliers" && (
              <>
                <div className="provider-banner">
                  <div>
                    <span className="icon-tile">
                      <ShieldCheck size={20} />
                    </span>
                    <div>
                      <strong>
                        Aprobarea internă și confirmarea furnizorului sunt etape
                        distincte.
                      </strong>
                      <p>
                        {canRespond
                          ? "Planul este aprobat. Poți simula acceptarea sau refuzul fiecărei solicitări."
                          : "Generează și aprobă planul pentru a activa răspunsurile simulate."}
                      </p>
                    </div>
                  </div>
                  <button
                    className="btn primary"
                    disabled={busy || !canRespond || !pending}
                    onClick={() =>
                      mutate(
                        "/demo/confirm-all",
                        {},
                        "Toate solicitările încă valabile au fost confirmate în simulare.",
                      )
                    }
                  >
                    <CheckCheck size={16} />
                    Confirmă solicitările
                  </button>
                </div>
                <div className="section-heading">
                  <h2>
                    Hoteluri contractuale <span>4</span>
                  </h2>
                  <span className="muted small">
                    Tarife per cameră / noapte · RON
                  </span>
                </div>
                <div className="supplier-grid">
                  {s.hotels.map((h, i) => {
                    const rows = s.assignments.filter(
                      (a) => a.hotel_id === h.id && active(a.hotel_status),
                    );
                    const roomUsed = rows.reduce(
                      (n, a) => n + a.room_ids.length,
                      0,
                    );
                    const held = rows.some((a) => a.hotel_status === "held");
                    return (
                      <section className="card supplier-card" key={h.id}>
                        <div className={`supplier-cover cover-${i}`}>
                          <Building2 size={52} />
                          <span>PARTENER CONTRACTUAL</span>
                        </div>
                        <div className="supplier-body">
                          <div className="supplier-title">
                            <h3>{h.name}</h3>
                            <Badge status={hotelStatus(h)} />
                          </div>
                          <p className="location">
                            <MapPin size={14} />
                            {h.area} · {h.distance_km} km · {h.travel_minutes}{" "}
                            min
                          </p>
                          <div className="availability">
                            <strong>
                              {h.status === "refused"
                                ? 0
                                : h.rooms.length - roomUsed}
                              <small> / {h.rooms.length}</small>
                            </strong>
                            <span>camere disponibile</span>
                            <div className="progress-track">
                              <i
                                style={{
                                  width: `${(roomUsed / h.rooms.length) * 100}%`,
                                }}
                              />
                            </div>
                          </div>
                          <div className="room-types">
                            {[
                              ...new Set(
                                h.rooms.map(
                                  (r) =>
                                    `${r.capacity}-${r.rate}-${r.accessible}`,
                                ),
                              ),
                            ].map((key) => {
                              const [cap, rate, access] = key.split("-");
                              const rooms = h.rooms.filter(
                                (r) =>
                                  r.capacity === Number(cap) &&
                                  r.rate === Number(rate) &&
                                  String(r.accessible) === access,
                              );
                              return (
                                <div key={key}>
                                  <span>
                                    {access === "true" ? (
                                      <Accessibility size={13} />
                                    ) : (
                                      <HotelIcon size={13} />
                                    )}{" "}
                                    {cap} persoane
                                    {access === "true" ? " · accesibil" : ""}
                                    <small> × {rooms.length}</small>
                                  </span>
                                  <strong>{rate} RON</strong>
                                </div>
                              );
                            })}
                          </div>
                          <div className="provider-actions">
                            <button
                              className="btn secondary small"
                              disabled={busy || !canRespond || !held}
                              onClick={() =>
                                providerResponse("hotel", h.id, "refused")
                              }
                            >
                              Refuză
                            </button>
                            <button
                              className="btn primary small"
                              disabled={busy || !canRespond || !held}
                              onClick={() =>
                                providerResponse("hotel", h.id, "confirmed")
                              }
                            >
                              <Check size={15} />
                              Confirmă
                            </button>
                          </div>
                        </div>
                      </section>
                    );
                  })}
                </div>
                <div className="section-heading">
                  <h2>
                    Transportatori <span>3</span>
                  </h2>
                  <button
                    className="text-link"
                    disabled={busy || !pending}
                    onClick={() =>
                      mutate(
                        "/demo/expire",
                        {},
                        "Rezervările temporare au fost expirate în simulare.",
                      )
                    }
                  >
                    <Clock3 size={14} />
                    Simulează expirarea rezervărilor
                  </button>
                </div>
                <Card
                  title="Flotă & solicitări de transfer"
                  sub="Bagaje și locuri pentru scaune rulante verificate separat"
                >
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Vehicul / transportator</th>
                          <th>Capacitate</th>
                          <th>Interval disponibil</th>
                          <th>Destinație / preluare</th>
                          <th>Tarif / cursă</th>
                          <th>Stare & răspuns</th>
                        </tr>
                      </thead>
                      <tbody>
                        {s.vehicles.map((v) => {
                          const t = s.trips.find((t) => t.vehicle_id === v.id);
                          return (
                            <tr key={v.id}>
                              <td>
                                <strong>{v.provider}</strong>
                                <small>
                                  {v.id} · {v.label}
                                </small>
                              </td>
                              <td>
                                {v.seats} locuri · {v.bags} bagaje
                                <small>{v.wheelchairs} locuri accesibile</small>
                              </td>
                              <td>
                                {time(v.available_from)} –{" "}
                                {time(v.available_until)}
                              </td>
                              <td>
                                {t ? (
                                  <>
                                    {
                                      s.hotels.find((h) => h.id === t.hotel_id)
                                        ?.name
                                    }
                                    <small>
                                      {time(t.departure_at)} ·{" "}
                                      {t.group_ids.reduce(
                                        (n, gid) =>
                                          n +
                                          (s.groups.find((g) => g.id === gid)
                                            ?.size || 0),
                                        0,
                                      )}{" "}
                                      pasageri
                                    </small>
                                  </>
                                ) : (
                                  "—"
                                )}
                              </td>
                              <td>
                                <strong>{v.price} RON</strong>
                              </td>
                              <td>
                                <Badge status={t?.status || v.status} />
                                {t?.status === "held" && (
                                  <div className="inline-actions">
                                    <button
                                      disabled={busy || !canRespond}
                                      onClick={() =>
                                        providerResponse(
                                          "transport",
                                          t.id,
                                          "confirmed",
                                        )
                                      }
                                    >
                                      Confirmă
                                    </button>
                                    <button
                                      disabled={busy || !canRespond}
                                      onClick={() =>
                                        providerResponse(
                                          "transport",
                                          t.id,
                                          "refused",
                                        )
                                      }
                                    >
                                      Refuză
                                    </button>
                                  </div>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </>
            )}

            {tab === "passengers" && (
              <>
                <div className="passenger-toolbar">
                  <div className="search-field">
                    <Search size={17} />
                    <input
                      aria-label="Caută pasager sau grup"
                      placeholder="Caută un pasager, o familie sau un grup…"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </div>
                  <div className="filter-buttons">
                    <button
                      className={`btn small ${!onlyExceptions ? "primary" : "secondary"}`}
                      onClick={() => setOnlyExceptions(false)}
                    >
                      Toți · {m.passengers}
                    </button>
                    <button
                      className={`btn small ${onlyExceptions ? "primary" : "secondary"}`}
                      onClick={() => setOnlyExceptions(true)}
                    >
                      <TriangleAlert size={14} />
                      Intervenții · {hasPlan ? m.exceptions : 0}
                    </button>
                  </div>
                  <button
                    className="btn secondary small"
                    onClick={() => setModal("import")}
                    disabled={hasPlan}
                  >
                    <Upload size={16} />
                    Import CSV
                  </button>
                  <button
                    className="btn secondary small"
                    disabled={
                      busy || hasPlan || s.incident.passenger_count !== 186
                    }
                    onClick={() =>
                      mutate(
                        "/passengers/demo",
                        {},
                        "Scenariul cu 186 de pasageri a fost încărcat.",
                      )
                    }
                  >
                    <Users size={16} />
                    Încarcă demo
                  </button>
                </div>
                <Card
                  title={
                    onlyExceptions
                      ? "Lista de intervenții"
                      : "Registrul pasagerilor"
                  }
                  sub="Un rând per grup · membrii familiei au același hotel și transfer"
                >
                  <div className="table-scroll">
                    <table className="passengers-table">
                      <thead>
                        <tr>
                          <th>Grup / pasageri</th>
                          <th>Nevoi</th>
                          <th>Cazare</th>
                          <th>Transfer</th>
                          <th>Asistență</th>
                          <th>Acțiuni</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pageGroups.map((g) => {
                          const a = s.assignments.find(
                            (a) => a.group_id === g.id,
                          );
                          const t = s.trips.find((t) => t.id === a?.trip_id);
                          const complete =
                            a?.hotel_status === "confirmed" &&
                            t?.status === "confirmed";
                          return (
                            <tr key={g.id}>
                              <td>
                                <strong>{g.label}</strong>
                                <small>
                                  {g.id} · {g.size} persoane
                                </small>
                                <details>
                                  <summary>Membrii grupului</summary>
                                  {g.members.map((m) => (
                                    <small key={m.id}>
                                      {m.id} · {m.name}
                                    </small>
                                  ))}
                                </details>
                              </td>
                              <td>
                                {g.room_needs.length} cameră/camere · {g.bags}{" "}
                                bagaje
                                {g.accessible > 0 && (
                                  <small className="accessible">
                                    <Accessibility size={13} />
                                    Accesibilitate: {g.accessible} persoane
                                  </small>
                                )}
                              </td>
                              <td>
                                {a ? (
                                  <>
                                    {
                                      s.hotels.find((h) => h.id === a.hotel_id)
                                        ?.name
                                    }
                                    <small>
                                      {statusLabels[a.hotel_status]}
                                    </small>
                                  </>
                                ) : (
                                  "În așteptare"
                                )}
                              </td>
                              <td>
                                {t ? (
                                  <>
                                    {time(t.departure_at)}
                                    <small>{statusLabels[t.status]}</small>
                                  </>
                                ) : (
                                  "În așteptare"
                                )}
                              </td>
                              <td>
                                {!g.eligible ? (
                                  <span className="badge">Neeligibil</span>
                                ) : complete ? (
                                  <Badge status="confirmed" />
                                ) : s.exceptions.some(
                                    (e) => e.group_id === g.id,
                                  ) ? (
                                  <span className="badge refused">
                                    Intervenție
                                  </span>
                                ) : (
                                  <span className="badge held">
                                    În pregătire
                                  </span>
                                )}
                              </td>
                              <td>
                                <div className="row-actions">
                                  <button
                                    className="icon-button"
                                    aria-label={`Pagina pasagerului ${g.id}`}
                                    onClick={() => openPassenger(g)}
                                  >
                                    <Ticket size={17} />
                                  </button>
                                  <button
                                    className="icon-button"
                                    aria-label={`Ajustează ${g.id}`}
                                    disabled={!hasPlan}
                                    onClick={() => {
                                      setSelected(g);
                                      setModal("adjust");
                                    }}
                                  >
                                    <SlidersHorizontal size={17} />
                                  </button>
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    {!filteredGroups.length && (
                      <Empty>
                        {!s.groups.length
                          ? "Importă un CSV sau încarcă scenariul demonstrativ."
                          : query
                            ? "Niciun pasager nu corespunde căutării."
                            : "Nu există intervenții înregistrate."}
                      </Empty>
                    )}
                  </div>
                  <div className="pagination">
                    <span>
                      {filteredGroups.length
                        ? `${(currentPage - 1) * 20 + 1}–${Math.min(currentPage * 20, filteredGroups.length)} din ${filteredGroups.length} grupuri`
                        : "0 grupuri"}
                    </span>
                    <div>
                      <button
                        className="icon-button"
                        aria-label="Pagina anterioară"
                        disabled={currentPage === 1}
                        onClick={() => setPassengerPage(currentPage - 1)}
                      >
                        <ChevronLeft size={17} />
                      </button>
                      <span>
                        {currentPage} / {pageCount}
                      </span>
                      <button
                        className="icon-button"
                        aria-label="Pagina următoare"
                        disabled={currentPage === pageCount}
                        onClick={() => setPassengerPage(currentPage + 1)}
                      >
                        <ChevronRight size={17} />
                      </button>
                    </div>
                  </div>
                </Card>
                {onlyExceptions && s.exceptions.length > 0 && (
                  <Exceptions
                    s={s}
                    onAdjust={(g) => {
                      setSelected(g);
                      setModal("adjust");
                    }}
                  />
                )}
              </>
            )}

            {tab === "history" && (
              <Card
                title="Jurnalul incidentului"
                sub={`${s.audit.length} evenimente · ore afișate în fusul Europe/Bucharest`}
                action={
                  <span className="subtle-tag">
                    <History size={14} />
                    Trasabilitate
                  </span>
                }
              >
                <div className="timeline">
                  {[...s.audit].reverse().map((a) => (
                    <div className="timeline-item" key={a.id}>
                      <span
                        className={`timeline-icon ${a.action.includes("Refuz") ? "orange" : ""}`}
                      >
                        {a.action.includes("Refuz") ? (
                          <TriangleAlert size={17} />
                        ) : a.action.includes("aprobat") ? (
                          <ShieldCheck size={17} />
                        ) : (
                          <Activity size={17} />
                        )}
                      </span>
                      <div>
                        <div className="timeline-title">
                          <h3>{a.action}</h3>
                          <time>
                            {date(a.at)} · {time(a.at)}
                          </time>
                        </div>
                        <p>{a.reason}</p>
                        <small>{a.actor}</small>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
          <footer className="page-footer">
            <span>
              StrandedOS <i /> Asistența începe cu un plan.
            </span>
            <button onClick={() => setModal("reset")}>
              <RotateCcw size={13} />
              Resetează scenariul
            </button>
            <span>Date fictive · Fără rezervări reale</span>
          </footer>
        </main>
      </div>

      {modal && (
        <ModalFrame
          key={modal}
          title={
            modal === "command"
              ? "Caută în StrandedOS"
              : modal === "new"
                ? "Deschide un incident"
                : modal === "approve"
                  ? "Aprobarea internă a bugetului"
                  : modal === "compare"
                    ? "Aceleași date. Aceleași constrângeri."
                    : modal === "reset"
                      ? "Resetezi scenariul demonstrativ?"
                      : modal === "import"
                        ? "Importă registrul pasagerilor"
                        : modal === "adjust"
                          ? `Ajustare manuală · ${selected?.id}`
                          : modal === "passenger"
                            ? "Pagina mobilă a pasagerului"
                            : "Demo de 90 de secunde"
          }
          onClose={() => !busy && setModal(null)}
          wide={modal === "compare"}
          passenger={modal === "passenger"}
          command={modal === "command"}
        >
          {error && (
            <div className="alert error" role="alert">
              <TriangleAlert size={17} />
              {error}
            </div>
          )}
          {modal === "command" && (
            <CommandPalette
              commands={commands}
              onClose={() => setModal(null)}
            />
          )}
          {modal === "approve" && (
            <ApprovalForm
              s={s}
              busy={busy}
              onSubmit={async (allow) => {
                if (
                  await mutate(
                    "/plan/approve",
                    { allow_over_budget: allow },
                    "Buget aprobat intern. Furnizorii pot răspunde acum.",
                  )
                )
                  setModal(null);
              }}
            />
          )}
          {modal === "new" && (
            <IncidentForm
              s={s}
              busy={busy}
              onSubmit={async (body) => {
                if (
                  await mutate(
                    "/incidents",
                    body,
                    "Incident deschis. Urmează importul și alocarea.",
                  )
                ) {
                  setModal(null);
                  navigate("overview");
                }
              }}
            />
          )}
          {modal === "compare" &&
            (compare ? (
              <Comparison data={compare} />
            ) : (
              <div className="loading">
                <LoaderCircle className="spin" />
                Se calculează ambele scenarii…
              </div>
            ))}
          {modal === "reset" && (
            <>
              <p className="modal-copy">
                Incidentul curent, planurile și istoricul vor fi înlocuite cu
                scenariul fictiv de 186 de pasageri. Linkurile pasagerilor vor
                fi regenerate.
              </p>
              <button
                className="btn primary full"
                disabled={busy}
                onClick={async () => {
                  if (
                    await mutate(
                      "/reset",
                      {},
                      "Scenariul demonstrativ a fost resetat.",
                    )
                  ) {
                    setModal(null);
                    navigate("overview");
                  }
                }}
              >
                <RotateCcw size={16} />
                Resetează demo-ul
              </button>
            </>
          )}
          {modal === "import" && (
            <>
              <p className="modal-copy">
                Un pasager per rând. Același <code>group_id</code> păstrează
                familia împreună. CSV-ul trebuie să conțină exact{" "}
                {s.incident.passenger_count} pasageri.
              </p>
              <div className="import-schema">
                <FileSpreadsheet size={25} />
                <code>
                  passenger_id, name, group_id, room_capacity, accessible, bags,
                  eligible
                </code>
                <p>
                  Opțional: pickup_start, pickup_end (ISO 8601 cu fus orar).
                  Camere: 1–4 persoane; maximum 12 membri într-un grup. Valorile
                  logice: true/false.
                </p>
              </div>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,text/csv"
                hidden
                onChange={(e) => {
                  handleImport(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
              <button
                className="btn primary full"
                disabled={busy}
                onClick={() => fileRef.current?.click()}
              >
                <Upload size={16} />
                Alege fișierul CSV
              </button>
              <a
                className="text-link download-link"
                href="/demo-passengers.csv"
                download
              >
                <Download size={15} />
                Descarcă CSV-ul demonstrativ (186 pasageri)
              </a>
            </>
          )}
          {modal === "adjust" && selected && (
            <AdjustmentForm
              s={s}
              group={selected}
              busy={busy}
              onSubmit={async (hotel_id, reason) => {
                if (
                  await mutate(
                    `/plan/groups/${encodeURIComponent(selected.id)}`,
                    { hotel_id, reason },
                    "Ajustare validată. Planul necesită o nouă aprobare.",
                  )
                )
                  setModal(null);
              }}
            />
          )}
          {modal === "passenger" && selected && (
            <PassengerPage token={selected.token} embedded />
          )}
          {modal === "help" && (
            <>
              <div className="guide-intro">
                <span className="sim-label">
                  <span />
                  SIMULARE
                </span>
                <p>
                  Un zbor anulat, 186 de pasageri, 4 hoteluri și 3
                  transportatori. Nicio cheie API necesară.
                </p>
              </div>
              <ol className="demo-guide">
                <li>
                  <strong>00–15s · Deschide incidentul</strong>
                  <p>
                    Folosește „Incident nou”, păstrează 186 pasageri și încarcă
                    scenariul demo.
                  </p>
                </li>
                <li>
                  <strong>15–30s · Generează recomandarea</strong>
                  <p>
                    Arată camerele, familiile și costul detaliat. Compară
                    regulile de alocare.
                  </p>
                </li>
                <li>
                  <strong>30–45s · Aprobă bugetul</strong>
                  <p>
                    Confirmările furnizorilor sunt încă zero. Sunt două etape
                    distincte.
                  </p>
                </li>
                <li>
                  <strong>45–60s · Simulează răspunsurile</strong>
                  <p>
                    Northline refuză. Ceilalți furnizori confirmă. Apar
                    intervențiile.
                  </p>
                </li>
                <li>
                  <strong>60–75s · Recalculează</strong>
                  <p>
                    Confirmările utilizabile se păstrează. Aprobă noul plan și
                    confirmă solicitările din panoul Furnizori.
                  </p>
                </li>
                <li>
                  <strong>75–90s · Deschide pagina unui pasager</strong>
                  <p>
                    Din Pasageri, apasă pictograma voucherului. Arată hotelul,
                    preluarea și voucherul de masă.
                  </p>
                </li>
              </ol>
            </>
          )}
        </ModalFrame>
      )}
    </div>
  );
}

function Exceptions({
  s,
  onAdjust,
}: {
  s: Workspace;
  onAdjust: (g: Group) => void;
}) {
  if (!s.exceptions.length) return null;
  return (
    <Card
      title="Intervenții necesare"
      sub={`${s.metrics.exceptions} pasageri fără combinație completă de servicii`}
      className="exceptions-card"
    >
      {s.exceptions.map((e) => (
        <div className="exception-row" key={e.group_id}>
          <TriangleAlert size={17} />
          <div>
            <strong>
              {e.group_id} · {e.passengers} persoane
            </strong>
            <p>{e.reason}</p>
          </div>
          <button
            className="btn secondary small"
            onClick={() => onAdjust(s.groups.find((g) => g.id === e.group_id)!)}
          >
            Ajustează
            <ArrowRight size={13} />
          </button>
        </div>
      ))}
    </Card>
  );
}
function ModalFrame({
  title,
  onClose,
  children,
  wide,
  passenger,
  command,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  passenger?: boolean;
  command?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const initialFocus = ref.current?.querySelector<HTMLElement>(
      '[data-autofocus], input:not([type="checkbox"]):not([type="file"])',
    );
    (initialFocus || ref.current)?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const list = Array.from(
          ref.current?.querySelectorAll<HTMLElement>(
            "button:not([disabled]),a[href],input:not([disabled]),select,textarea",
          ) || [],
        ).filter((node) => node.getClientRects().length > 0);
        if (!list?.length) return;
        const first = list[0],
          last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.body.style.overflow = oldOverflow;
      document.removeEventListener("keydown", handler);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`modal ${wide ? "wide" : ""} ${passenger ? "passenger-modal" : ""} ${command ? "command-modal" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        ref={ref}
        tabIndex={-1}
      >
        <div className="modal-heading">
          <h2 id="modal-title">{title}</h2>
          <button
            className="icon-button"
            aria-label="Închide dialogul"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
function ApprovalForm({
  s,
  busy,
  onSubmit,
}: {
  s: Workspace;
  busy: boolean;
  onSubmit: (allow: boolean) => void;
}) {
  const [allow, setAllow] = useState(false);
  const over = s.metrics.total_cost > s.incident.budget;
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(allow);
      }}
    >
      <p className="modal-copy">
        Autorizezi costul planului v{s.plan_version}. Furnizorii vor trebui să
        confirme separat camerele, masa și transferurile.
      </p>
      <Budget s={s} />
      {over && (
        <label className="checkbox-label over-budget">
          <input
            type="checkbox"
            checked={allow}
            onChange={(e) => setAllow(e.target.checked)}
          />
          Aprob explicit depășirea de{" "}
          {money(s.metrics.total_cost - s.incident.budget)} RON.
        </label>
      )}
      <div className="approval-note">
        <ShieldCheck size={18} />
        {s.metrics.covered} pasageri cu recomandare · {s.metrics.exceptions}{" "}
        necesită intervenție. Confirmările utilizabile rămân valabile.
      </div>
      <button className="btn primary full" disabled={busy || (over && !allow)}>
        <CheckCheck size={17} />
        Aprobă {money(s.metrics.total_cost)} RON
      </button>
    </form>
  );
}
function IncidentForm({
  s,
  busy,
  onSubmit,
}: {
  s: Workspace;
  busy: boolean;
  onSubmit: (body: object) => void;
}) {
  const [loadDemo, setLoadDemo] = useState(true);
  const local = (iso: string) => {
    const d = new Date(iso);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  };
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    onSubmit({
      flight: f.get("flight"),
      airport: f.get("airport"),
      destination: f.get("destination"),
      passenger_count: Number(f.get("passenger_count")),
      budget: Number(f.get("budget")),
      start: new Date(String(f.get("start"))).toISOString(),
      end: new Date(String(f.get("end"))).toISOString(),
      load_demo: loadDemo,
    });
  };
  return (
    <form onSubmit={submit} className="form-grid">
      <p className="modal-copy span-2">
        Un singur incident activ în MVP. Deschiderea unui incident va înlocui
        scenariul curent și istoricul său.
      </p>
      <label>
        Număr zbor
        <input
          name="flight"
          defaultValue="RO 384"
          required
          minLength={2}
          maxLength={20}
        />
      </label>
      <label>
        Aeroport
        <input
          name="airport"
          defaultValue="OTP · Henri Coandă"
          required
          maxLength={80}
        />
      </label>
      <label>
        Destinație
        <input
          name="destination"
          defaultValue="Paris · CDG"
          required
          maxLength={80}
        />
      </label>
      <label>
        Număr pasageri
        <input
          name="passenger_count"
          type="number"
          min="1"
          max="1000"
          defaultValue="186"
          required
        />
      </label>
      <label>
        Început asistență
        <input
          name="start"
          type="datetime-local"
          defaultValue={local(s.incident.start)}
          required
        />
      </label>
      <label>
        Sfârșit asistență
        <input
          name="end"
          type="datetime-local"
          defaultValue={local(s.incident.end)}
          required
        />
      </label>
      <label className="span-2">
        Buget disponibil (RON)
        <input
          name="budget"
          type="number"
          min="1"
          max="10000000"
          defaultValue="45000"
          required
        />
      </label>
      <label className="checkbox-label span-2">
        <input
          type="checkbox"
          checked={loadDemo}
          onChange={(e) => setLoadDemo(e.target.checked)}
        />
        Încarcă scenariul fictiv de 186 de pasageri
      </label>
      <p className="small muted span-2">
        Orele sunt introduse în fusul browserului; consola afișează ora
        României. Interval acceptat: 1–72 ore. Catalogul este simulat.
      </p>
      <button className="btn primary span-2" disabled={busy}>
        <Plus size={17} />
        Deschide incidentul
      </button>
    </form>
  );
}
function AdjustmentForm({
  s,
  group,
  busy,
  onSubmit,
}: {
  s: Workspace;
  group: Group;
  busy: boolean;
  onSubmit: (h: string, r: string) => void;
}) {
  const a = s.assignments.find((a) => a.group_id === group.id);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        onSubmit(String(f.get("hotel_id")), String(f.get("reason")));
      }}
      className="adjustment-form"
    >
      <p className="modal-copy">
        Mută întregul grup de {group.size} persoane. Sistemul verifică din nou
        camerele și transferul. Serviciile modificate vor necesita o nouă
        aprobare și confirmare.
      </p>
      <label>
        Hotel dorit
        <select name="hotel_id" defaultValue={a?.hotel_id}>
          {s.hotels.map((h) => (
            <option value={h.id} key={h.id} disabled={h.status === "refused"}>
              {h.name}
              {h.status === "refused" ? " · refuzat" : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        Motivul modificării
        <textarea
          name="reason"
          minLength={3}
          maxLength={300}
          required
          placeholder="De exemplu: apropierea de aeroport solicitată de familie"
        />
      </label>
      <button className="btn primary full" disabled={busy}>
        <SlidersHorizontal size={16} />
        Verifică și aplică mutarea
      </button>
    </form>
  );
}
function Comparison({
  data,
}: {
  data: { recommended: Metrics; baseline: Metrics; assumption: string };
}) {
  const r = data.recommended,
    b = data.baseline,
    equal = r.covered === b.covered;
  return (
    <>
      <div className="comparison-note">
        <GitCompareArrows size={24} />
        <div>
          <strong>Rezultate simulate, fără garanție de optim global</strong>
          <p>{data.assumption}</p>
        </div>
      </div>
      <table className="comparison-table">
        <thead>
          <tr>
            <th>Indicator</th>
            <th>Regula simplă</th>
            <th>Recomandare</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Cost total</td>
            <td>{money(b.total_cost)} RON</td>
            <td>{money(r.total_cost)} RON</td>
          </tr>
          <tr>
            <td>Confirmări simulate*</td>
            <td>{b.covered} pasageri</td>
            <td>{r.covered} pasageri</td>
          </tr>
          <tr>
            <td>Pasageri fără soluție</td>
            <td>{b.exceptions}</td>
            <td>{r.exceptions}</td>
          </tr>
          <tr>
            <td>Camere / transferuri</td>
            <td>
              {b.room_count} / {b.trip_count}
            </td>
            <td>
              {r.room_count} / {r.trip_count}
            </td>
          </tr>
        </tbody>
      </table>
      <p className="small muted">
        * Scenariu în care toți furnizorii acceptă. Confirmările reale din demo
        apar în panoul incidentului doar după răspunsuri.
      </p>
      <div className="comparison-result">
        {equal
          ? `Acoperire egală: diferență de cost ${money(b.total_cost - r.total_cost)} RON în favoarea recomandării.`
          : `Acoperire diferită: recomandarea asistă ${r.covered - b.covered} pasageri în plus. Un cost mai mic pentru mai puțini pasageri nu reprezintă o economie echivalentă.`}
      </div>
    </>
  );
}

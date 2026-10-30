import { useEffect, useId, useRef, useState } from "react";
import {
  ArrowUpRight,
  CornerDownLeft,
  Search,
  type LucideIcon,
} from "lucide-react";

export type Command = {
  id: string;
  label: string;
  detail: string;
  icon: LucideIcon;
  run: () => void;
  keywords?: string;
};

export function CommandPalette({
  commands,
  onClose,
}: {
  commands: Command[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState(0);
  const listId = useId();
  const results = commands
    .filter((c) =>
      `${c.label} ${c.detail} ${c.keywords || ""}`
        .toLocaleLowerCase("ro")
        .includes(query.toLocaleLowerCase("ro")),
    )
    .slice(0, 9);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    setSelection(0);
  }, [query]);
  useEffect(() => {
    itemRefs.current[selection]?.scrollIntoView({ block: "nearest" });
  }, [selection]);
  return (
    <div className="command-palette">
      <div className="command-search">
        <Search size={21} />
        <input
          data-autofocus
          aria-label="Caută o pagină, un pasager sau o acțiune"
          placeholder="Unde vrei să ajungi?"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={
            results[selection] ? `${listId}-${selection}` : undefined
          }
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSelection((i) =>
                Math.max(0, Math.min(i + 1, results.length - 1)),
              );
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setSelection((i) => Math.max(0, i - 1));
            }
            if (e.key === "Enter" && results[selection]) {
              e.preventDefault();
              results[selection].run();
            }
          }}
        />
        <button
          className="command-close"
          aria-label="Închide căutarea"
          onClick={onClose}
        >
          <kbd>esc</kbd>
        </button>
      </div>
      <div className="command-section-label">
        {query ? "REZULTATE" : "PAGINI ȘI ACȚIUNI RAPIDE"}
      </div>
      <div
        className="command-results"
        role="listbox"
        id={listId}
        aria-label="Rezultatele căutării"
      >
        {results.map((c, i) => (
          <button
            key={c.id}
            id={`${listId}-${i}`}
            role="option"
            aria-selected={i === selection}
            className={`command-result ${i === selection ? "active" : ""}`}
            ref={(node) => {
              itemRefs.current[i] = node;
            }}
            onMouseMove={() => setSelection(i)}
            onClick={c.run}
          >
            <span className="command-icon">
              <c.icon size={18} />
            </span>
            <span>
              <strong>{c.label}</strong>
              <small>{c.detail}</small>
            </span>
            <ArrowUpRight size={15} />
          </button>
        ))}
        {!results.length && (
          <div className="command-empty">
            <Search size={26} />
            <p>Niciun rezultat pentru „{query}”.</p>
            <small>Încearcă numele unei familii sau un cod de grup.</small>
          </div>
        )}
      </div>
      <div className="command-footer">
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd>pentru navigare
        </span>
        <span>
          <CornerDownLeft size={12} />
          pentru deschidere
        </span>
      </div>
    </div>
  );
}

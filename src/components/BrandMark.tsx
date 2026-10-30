export function BrandMark({ className = "" }: { className?: string }) {
  return (
    <span className={`brand-symbol ${className}`} aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none">
        <path
          d="M16 3v9m0 8v9M3 16h9m8 0h9M6.8 6.8l6.4 6.4m5.6 5.6 6.4 6.4M6.8 25.2l6.4-6.4m5.6-5.6 6.4-6.4"
          stroke="currentColor"
          strokeWidth="3.4"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

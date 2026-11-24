export type NovaMood = "idle" | "thinking" | "listening" | "speaking" | "offline";

/**
 * Nova, the Skyline mascot: a small cloud with a face. Colors come from the theme tokens
 * (--nova-body / --nova-face / --nova-halo) so it adapts to light and dark mode; motion is
 * CSS-only (see .nova-* rules in globals.css) and disabled under prefers-reduced-motion.
 */
export function NovaAvatar({
  size = 32,
  mood = "idle",
  halo = true,
  className = "",
}: {
  size?: number;
  mood?: NovaMood;
  halo?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`nova nova-${mood} inline-flex shrink-0 ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      <svg viewBox="0 0 38 38" width={size} height={size}>
        {halo && <circle className="nova-halo" cx="19" cy="19" r="19" fill="var(--nova-halo)" />}
        <g className="nova-body">
          <ellipse cx="19" cy="22.5" rx="12" ry="7" fill="var(--nova-body)" />
          <circle className="nova-puff-a" cx="13.5" cy="18.5" r="6" fill="var(--nova-body)" />
          <circle className="nova-puff-b" cx="21.5" cy="15.5" r="7.5" fill="var(--nova-body)" />
          <g className="nova-eyes">
            <circle cx="15.5" cy="20.5" r="1.4" fill="var(--nova-face)" />
            <circle cx="22.5" cy="20.5" r="1.4" fill="var(--nova-face)" />
          </g>
          <path
            className="nova-mouth"
            d="M16.5 24 q2.5 2 5 0"
            stroke="var(--nova-face)"
            strokeWidth="1.3"
            fill="none"
            strokeLinecap="round"
          />
        </g>
      </svg>
    </span>
  );
}

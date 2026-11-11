import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement>;

const base = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
  focusable: false,
} as const;

export const IconHome = (p: P) => (
  <svg {...base} {...p}>
    <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />
  </svg>
);
export const IconPlus = (p: P) => (
  <svg {...base} {...p}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);
export const IconFlow = (p: P) => (
  <svg {...base} {...p}>
    <rect x="3" y="4" width="6" height="5" rx="1.5" />
    <rect x="15" y="4" width="6" height="5" rx="1.5" />
    <rect x="9" y="15" width="6" height="5" rx="1.5" />
    <path d="M9 6.5h6M6 9v3.5h6V15M18 9v3.5h-6" />
  </svg>
);
export const IconPulse = (p: P) => (
  <svg {...base} {...p}>
    <path d="M3 12h4l2-6 4 12 2-6h6" />
  </svg>
);
export const IconSun = (p: P) => (
  <svg {...base} {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </svg>
);
export const IconMoon = (p: P) => (
  <svg {...base} {...p}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" />
  </svg>
);
export const IconMonitor = (p: P) => (
  <svg {...base} {...p}>
    <rect x="3" y="4" width="18" height="12" rx="2" />
    <path d="M8 20h8M12 16v4" />
  </svg>
);
export const IconMenu = (p: P) => (
  <svg {...base} {...p}>
    <path d="M4 7h16M4 12h16M4 17h16" />
  </svg>
);
export const IconClose = (p: P) => (
  <svg {...base} {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
);
export const BrandMark = (p: P) => (
  <svg viewBox="0 0 32 32" aria-hidden focusable={false} {...p}>
    <rect width="32" height="32" rx="7" fill="var(--c-accent)" />
    <path d="M7 24V13l6 4v-4l6 4v-4l6 4v7z" fill="var(--c-on-accent)" />
  </svg>
);

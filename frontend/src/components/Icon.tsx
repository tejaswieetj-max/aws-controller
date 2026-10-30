import type { SVGProps } from 'react';

export type IconName =
  | 'overview'
  | 'estimate'
  | 'requests'
  | 'budgets'
  | 'alerts'
  | 'calibur'
  | 'refresh'
  | 'arrow'
  | 'close'
  | 'check'
  | 'shield'
  | 'lock'
  | 'unlock'
  | 'server'
  | 'spark'
  | 'activity'
  | 'clock'
  | 'external'
  | 'chevron'
  | 'logout'
  | 'search'
  | 'plus'
  | 'info'
  | 'warning'
  | 'eye';

const paths: Record<IconName, string> = {
  overview: 'M4 13h6V4H4v9Zm0 7h6v-4H4v4Zm10 0h6v-9h-6v9Zm0-16v4h6V4h-6Z',
  estimate: 'M12 3v18M5 8h9a3 3 0 0 1 0 6H5m0 0h10',
  requests: 'M4 6h16M4 12h16M4 18h10',
  budgets: 'M12 3a9 9 0 1 0 9 9h-9V3Z',
  alerts: 'M12 3 2.5 20h19L12 3Zm0 5.5v5m0 3v.5',
  calibur: 'M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z',
  refresh: 'M20 11a8 8 0 0 0-14.9-4M4 5v4h4M4 13a8 8 0 0 0 14.9 4M20 19v-4h-4',
  arrow: 'M5 12h14m-6-6 6 6-6 6',
  close: 'M6 6l12 12M18 6 6 18',
  check: 'm5 13 4 4L19 7',
  shield: 'M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Zm-3 9 2 2 4-4',
  lock: 'M6 10h12v10H6V10Zm3 0V7a3 3 0 0 1 6 0v3',
  unlock: 'M6 10h12v10H6V10Zm3 0V7a3 3 0 0 1 5.7-1.3',
  server: 'M4 4h16v6H4V4Zm0 10h16v6H4v-6Zm4-7h.01M8 17h.01',
  spark: 'm12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5H5V6h5',
  chevron: 'm9 6 6 6-6 6',
  logout: 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm10 2-4.5-4.5',
  plus: 'M12 5v14M5 12h14',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-10v6m0-9v.01',
  warning: 'M12 3 2.5 20h19L12 3Zm0 5.5v5m0 3v.5',
  eye: 'M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
};

export function Icon({
  name,
  size = 18,
  ...props
}: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      <path d={paths[name]} />
    </svg>
  );
}

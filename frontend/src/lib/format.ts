export function formatDate(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

export function formatDateTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

export function relativeTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const delta = date.getTime() - Date.now();
  const abs = Math.abs(delta);
  const minutes = Math.round(abs / 60_000);
  if (minutes < 1) return 'just now';
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  if (minutes < 60) return formatter.format(Math.sign(delta) * minutes, 'minute');
  const hours = Math.round(minutes / 60);
  if (hours < 36) return formatter.format(Math.sign(delta) * hours, 'hour');
  return formatter.format(Math.sign(delta) * Math.round(hours / 24), 'day');
}

export function titleCase(value?: string | null): string {
  if (!value) return 'Unknown';
  return value
    .toLowerCase()
    .replace(/_/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase());
}

export function groupList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === 'string') {
    return value
      .replace(/[[\]'"]/g, '')
      .split(/[ ,]+/)
      .map(item => item.trim())
      .filter(Boolean);
  }
  return [];
}

export function truncateId(value: string, head = 14, tail = 4): string {
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : (plural ?? `${singular}s`)}`;
}

const LOCAL_REQUESTS_KEY = 'spendctl_local_requests';

export type LocalRequestRecord = {
  reservation_id: string;
  team?: string;
  status: string;
  created_at?: string;
  reserved_micro: number;
  estimate_micro?: number;
  resource?: { instance_type?: string; ebs_gb?: number };
  purpose?: string;
  region?: string;
  planned_hours?: number;
  price_source?: string;
  margin_policy?: string;
  margin?: string;
  workflow_start_state?: string;
  workflow_execution_status?: string;
  price_snapshot_id?: string;
};

export function loadLocalRequests(): LocalRequestRecord[] {
  try {
    const raw = window.localStorage.getItem(LOCAL_REQUESTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveLocalRequests(records: LocalRequestRecord[]): void {
  try {
    window.localStorage.setItem(LOCAL_REQUESTS_KEY, JSON.stringify(records));
  } catch {
    // localStorage may be unavailable or quota-exceeded; degrade gracefully.
  }
}

export function appendLocalRequest(record: LocalRequestRecord): LocalRequestRecord[] {
  const existing = loadLocalRequests();
  const updated = [record, ...existing];
  saveLocalRequests(updated);
  return updated;
}

export function findLocalRequest(reservationId: string): LocalRequestRecord | undefined {
  return loadLocalRequests().find((row) => row.reservation_id === reservationId);
}

export function clearLocalRequests(): void {
  try {
    window.localStorage.removeItem(LOCAL_REQUESTS_KEY);
  } catch {
    // degrade gracefully
  }
}

import { RealApiRepository, type RealApiRepositoryOptions } from './real-api-repository';
import type { SpendctlRepository } from './repository';
import { SyntheticRepository } from '../providers/SyntheticRepository';
import { MockEngineRepository } from '../providers/MockEngineRepository';

/**
 * Repository selection order:
 * 1. Mock engine (VITE_USE_MOCK_ENGINE=true) — client-side simulation, no AWS calls.
 * 2. Synthetic fixtures (VITE_USE_SYNTHETIC_DATA=true) — browser-only demo data.
 * 3. Real AWS API (default) — server-authoritative Spendctl API.
 */
export function getSpendctlRepository(options: RealApiRepositoryOptions): SpendctlRepository {
  if (import.meta.env.VITE_USE_MOCK_ENGINE === 'true') return new MockEngineRepository();
  if (import.meta.env.VITE_USE_SYNTHETIC_DATA === 'true') return new SyntheticRepository();
  return new RealApiRepository(options);
}

export function isSyntheticDataMode(): boolean {
  return import.meta.env.VITE_USE_SYNTHETIC_DATA === 'true';
}

export function isMockEngineMode(): boolean {
  return import.meta.env.VITE_USE_MOCK_ENGINE === 'true';
}

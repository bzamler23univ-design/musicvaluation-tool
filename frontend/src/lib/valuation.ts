import type { HistoryPoint } from "./types";

export interface ValuationInputs {
  royaltyRatePerStream: number; // $ per stream (gross)
  ownershipPct: number; // 0..1 — share of the rights you own
  royaltyShare: number; // 0..1 — portion of royalties your rights cover (master/publishing split)
  annualDecayRate: number; // 0..1 — fractional decline in annual streams per year
  discountRate: number; // 0..1 — annual discount rate
  terminalMultiple: number; // x final-year owner net
  projectionYears: number; // integer
}

export const DEFAULT_INPUTS: ValuationInputs = {
  royaltyRatePerStream: 0.004,
  ownershipPct: 1.0,
  royaltyShare: 0.5,
  annualDecayRate: 0.2,
  discountRate: 0.1,
  terminalMultiple: 3,
  projectionYears: 10,
};

export interface ProjectionYear {
  year: number;
  streams: number;
  gross: number;
  ownerNet: number;
  discountFactor: number;
  pv: number;
}

export interface ValuationResult {
  historicalStreams: number;
  historicalGross: number;
  ownerNetHistorical: number;
  recentAnnualStreams: number;
  projection: ProjectionYear[];
  projectedGrossTotal: number;
  projectedOwnerNetTotal: number;
  pvProjected: number;
  terminalValue: number;
  pvTerminal: number;
  impliedCatalogValue: number;
}

/** Estimate a forward annual stream run-rate from the most recent window. */
export function recentAnnualStreams(history: HistoryPoint[], windowDays = 90): number {
  if (history.length === 0) return 0;
  const tail = history.slice(-Math.min(windowDays, history.length));
  const avgDaily = tail.reduce((s, p) => s + p.streams, 0) / tail.length;
  return avgDaily * 365;
}

export function valuate(history: HistoryPoint[], input: ValuationInputs): ValuationResult {
  const ownerFactor = input.ownershipPct * input.royaltyShare;
  const historicalStreams = history.reduce((s, p) => s + p.streams, 0);
  const historicalGross = historicalStreams * input.royaltyRatePerStream;
  const ownerNetHistorical = historicalGross * ownerFactor;

  const annual0 = recentAnnualStreams(history);
  const projection: ProjectionYear[] = [];
  let projectedGrossTotal = 0;
  let projectedOwnerNetTotal = 0;
  let pvProjected = 0;

  const years = Math.max(1, Math.round(input.projectionYears));
  for (let y = 1; y <= years; y++) {
    const streams = annual0 * Math.pow(1 - input.annualDecayRate, y);
    const gross = streams * input.royaltyRatePerStream;
    const ownerNet = gross * ownerFactor;
    const discountFactor = 1 / Math.pow(1 + input.discountRate, y);
    const pv = ownerNet * discountFactor;
    projectedGrossTotal += gross;
    projectedOwnerNetTotal += ownerNet;
    pvProjected += pv;
    projection.push({ year: y, streams, gross, ownerNet, discountFactor, pv });
  }

  const last = projection[projection.length - 1];
  const terminalValue = last.ownerNet * input.terminalMultiple;
  const pvTerminal = terminalValue * last.discountFactor;
  const impliedCatalogValue = pvProjected + pvTerminal;

  return {
    historicalStreams,
    historicalGross,
    ownerNetHistorical,
    recentAnnualStreams: annual0,
    projection,
    projectedGrossTotal,
    projectedOwnerNetTotal,
    pvProjected,
    terminalValue,
    pvTerminal,
    impliedCatalogValue,
  };
}

/** Implied catalog value across a grid of discount rate x decay rate. */
export function sensitivity(
  history: HistoryPoint[],
  base: ValuationInputs,
  discountRates: number[],
  decayRates: number[],
): number[][] {
  return discountRates.map((dr) =>
    decayRates.map(
      (decay) =>
        valuate(history, { ...base, discountRate: dr, annualDecayRate: decay })
          .impliedCatalogValue,
    ),
  );
}

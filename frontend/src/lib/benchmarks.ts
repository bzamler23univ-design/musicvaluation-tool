// Real-world benchmarks that back the valuation defaults, with sources.
// Compiled from public industry reporting (2024–2026). These are ranges, not
// guarantees — every deal differs. Shown in the UI next to each assumption.

export interface Source {
  id: string;
  title: string;
  publisher: string;
  url: string;
}

export const SOURCES: Source[] = [
  {
    id: "loudclear",
    title: "Loud & Clear (per-stream economics)",
    publisher: "Spotify",
    url: "https://loudandclear.byspotify.com/",
  },
  {
    id: "members",
    title: "How Much Does Spotify Pay Per Stream in 2025",
    publisher: "Members.media",
    url: "https://members.media/blogs/industry-insights/how-much-does-spotify-pay-per-stream-in-2025",
  },
  {
    id: "billboard_split",
    title: "Music Streaming Royalty Payments Explained",
    publisher: "Billboard",
    url: "https://www.billboard.com/pro/music-streaming-royalty-payments-explained-song-profits/",
  },
  {
    id: "shottower",
    title: "Music Catalog Market to Stay Attractive (Shot Tower Capital)",
    publisher: "Billboard",
    url: "https://www.billboard.com/pro/music-catalog-market-attractive-slower-growth-shot-tower/",
  },
  {
    id: "hipgnosis",
    title: "Hipgnosis Reveals How It Values Songs",
    publisher: "Music Business Worldwide",
    url: "https://www.musicbusinessworldwide.com/hipgnosis-reveals-how-it-values-songs-and-that-its-catalog-is-worth-slightly-more-than-it-forecast/",
  },
  {
    id: "chartlex",
    title: "Music Catalog Valuation Guide",
    publisher: "Chartlex",
    url: "https://www.chartlex.com/blog/business/music-catalog-valuation-guide-2026",
  },
];

export interface Benchmark {
  low: number;
  high: number;
  text: string; // short human-readable range
  detail: string; // the supporting fact
  sourceIds: string[];
}

// Keyed to ValuationInputs fields (plus "marketMultiple" for the implied check).
export const BENCHMARKS: Record<string, Benchmark> = {
  royaltyRatePerStream: {
    low: 0.003,
    high: 0.005,
    text: "$0.003–$0.005 / stream",
    detail:
      "Spotify pays right-holders roughly $0.003–$0.005 per stream on average (gross, before label/distributor cuts).",
    sourceIds: ["loudclear", "members"],
  },
  royaltyShare: {
    low: 0.2,
    high: 0.8,
    text: "~80% master · ~20% publishing",
    detail:
      "Streaming royalties split ~80/20 between the recording (master) side and the publishing side. Use ~0.8 if valuing the master, ~0.2 for publishing.",
    sourceIds: ["billboard_split"],
  },
  annualDecayRate: {
    low: 0.05,
    high: 0.2,
    text: "~5–20% / year",
    detail:
      "Hipgnosis models ~19% decline in each of the first two years after a modern hit, plateauing to single-digit changes thereafter; established catalogs decay far more slowly.",
    sourceIds: ["hipgnosis", "chartlex"],
  },
  discountRate: {
    low: 0.08,
    high: 0.12,
    text: "~8–12%",
    detail:
      "Hipgnosis Songs Fund used an 8.5% discount rate; buyers discount at their cost of capital, which rises with interest rates.",
    sourceIds: ["hipgnosis", "shottower"],
  },
  marketMultiple: {
    low: 10,
    high: 18,
    text: "~10–18× annual net",
    detail:
      "Publishing deals over $20M averaged ~16.1× net publisher share (NPS) in 2024; catalogs typically trade around 10–18× annual net income.",
    sourceIds: ["shottower", "chartlex"],
  },
};

export function sourcesFor(ids: string[]): Source[] {
  return SOURCES.filter((s) => ids.includes(s.id));
}

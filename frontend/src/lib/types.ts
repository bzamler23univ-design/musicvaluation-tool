export interface Summary {
  source: string;
  is_sample_data: boolean;
  total_songs: number;
  total_rows: number;
  date_range: { start: string; end: string };
  num_chart_days: number;
  num_missing_dates: number;
  num_partial_dates: number;
  last_updated: string;
}

export interface TopSong {
  id: string;
  track_name: string;
  artist_names: string;
  cumulative_streams: number;
  peak_rank: number;
  days_charted: number;
}

export interface IndexSong extends TopSong {
  spotify_track_id: string | null;
}

export interface HistoryPoint {
  date: string;
  rank: number;
  streams: number;
  cumulative: number;
}

export interface SongDetail {
  id: string;
  track_name: string;
  artist_names: string;
  spotify_track_id: string | null;
  peak_rank: number;
  days_charted: number;
  cumulative_streams: number;
  first_date: string;
  last_date: string;
  history: HistoryPoint[];
}

export interface DataHealth {
  source: string;
  is_sample_data: boolean;
  last_updated: string;
  date_range: { start: string; end: string };
  num_chart_days: number;
  missing_dates: string[];
  partial_dates: { chart_date: string; rows: number }[];
  scraper_summary: Record<string, unknown>;
}

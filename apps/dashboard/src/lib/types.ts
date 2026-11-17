export interface Source {
  id: string;
  url: string;
}
export interface Agent {
  id: string;
  public_id: string;
  name: string;
  published: boolean;
  instructions: string | null;
  allowed_origins: string[];
  crawl_config: CrawlConfig;
  sources: Source[];
  page_count: number;
  created_at: string;
}
export interface CrawlConfig {
  max_pages: number;
  max_depth: number;
  min_delay_seconds: number;
  max_duration_seconds: number;
  use_sitemap: boolean;
  render_urls: string[];
  recrawl_interval_hours: number | null;
}
export interface CrawlRun {
  id: string;
  source_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stats: Record<string, number>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}
export interface PageRow {
  id: string;
  url: string;
  canonical_url: string | null;
  status: string;
  render_mode: string;
  error: string | null;
  chunk_count: number;
  last_seen_in_sitemap: string | null;
  last_crawled_at: string | null;
  origin_last_modified: string | null;
  indexed_at: string | null;
}
export interface SkippedRow {
  url: string;
  reason: string;
  crawl_run_id: string;
}
export interface Status {
  app_env: string;
  llm: { provider: string; model: string; configured: boolean };
  embedding: { provider: string; model: string; configured: boolean; active_index: string | null };
  rendering_enabled: boolean;
}

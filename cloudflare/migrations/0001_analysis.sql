-- One atomic INSERT SELECT checks both monthly budgets, the anonymous daily limit and duplicate work.
CREATE TABLE analysis_calls (
  request_id TEXT PRIMARY KEY,
  month TEXT NOT NULL,
  day TEXT NOT NULL,
  visitor TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('report', 'ask')),
  cache_key TEXT NOT NULL,
  started INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'ok', 'failed')),
  reserved INTEGER NOT NULL CHECK (reserved > 0),
  charged INTEGER NOT NULL CHECK (charged BETWEEN 0 AND reserved)
);
CREATE INDEX analysis_month ON analysis_calls(month,kind);
CREATE INDEX analysis_visitor ON analysis_calls(day,visitor,kind);
CREATE INDEX analysis_pending ON analysis_calls(cache_key,status,started);
CREATE TABLE analysis_reports (
  cache_key TEXT PRIMARY KEY,
  response TEXT NOT NULL,
  generated_at TEXT NOT NULL
);

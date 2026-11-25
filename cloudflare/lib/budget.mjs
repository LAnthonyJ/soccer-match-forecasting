// Integer microyuan (1 CNY = 1,000,000). Limits apply to a UTC calendar month.
const limits = {report:20_000_000, ask:30_000_000, total:50_000_000};
export const prices = {input:2, output:8}; // Flash peak cache-miss microyuan per token.

export async function reserve(db, {kind, key, visitor, estimate, now, id}) {
  if (!Number.isSafeInteger(estimate) || estimate <= 0 || !['report','ask'].includes(kind)) {
    throw new Error('invalid reservation');
  }
  const day = now.toISOString().slice(0,10), month = day.slice(0,7);
  const stmt = db.prepare(`
    INSERT INTO analysis_calls(request_id,month,day,visitor,kind,cache_key,started,status,reserved,charged)
    SELECT ?,?,?,?,?,?,?,'pending',?,?
    WHERE (SELECT COALESCE(SUM(charged),0) FROM analysis_calls WHERE month=?) + ? <= ?
      AND (SELECT COALESCE(SUM(charged),0) FROM analysis_calls WHERE month=? AND kind=?) + ? <= ?
      AND (? != 'ask' OR (SELECT COUNT(*) FROM analysis_calls WHERE day=? AND visitor=? AND kind='ask') < 5)
      AND (? != 'report' OR (
        NOT EXISTS(SELECT 1 FROM analysis_reports WHERE cache_key=?)
        AND NOT EXISTS(SELECT 1 FROM analysis_calls WHERE cache_key=? AND started>? AND status IN ('pending','failed'))
      ))`);
  const result = await stmt.bind(id,month,day,visitor,kind,key,now.valueOf(),estimate,estimate,
    month,estimate,limits.total,month,kind,estimate,limits[kind],
    kind,day,visitor,kind,key,key,now.valueOf()-120_000).run();
  if (result.meta.changes === 1) return null;
  if (kind==='ask') {
    const count = await db.prepare("SELECT COUNT(*) AS n FROM analysis_calls WHERE day=? AND visitor=? AND kind='ask'").bind(day, visitor).first();
    if (count.n>=5) return 'daily_limit';
  }
  const balance = await db.prepare('SELECT COALESCE(SUM(charged),0) AS total, COALESCE(SUM(CASE WHEN kind=? THEN charged ELSE 0 END),0) AS lane FROM analysis_calls WHERE month=?').bind(kind,month).first();
  return balance.total+estimate>limits.total || balance.lane+estimate>limits[kind] ? 'monthly_budget' : 'report_pending';
}

export async function settle(db, id, amount, status, key, report) {
  const update = db.prepare("UPDATE analysis_calls SET status=?,charged=COALESCE(?,reserved) WHERE request_id=? AND status='pending'")
    .bind(status,amount,id);
  if (report) {
    await db.batch([update, db.prepare('INSERT INTO analysis_reports(cache_key,response,generated_at) VALUES(?,?,?) ON CONFLICT(cache_key) DO NOTHING')
      .bind(key,JSON.stringify(report),report.generated_at)]);
  } else {
    await update.run();
  }
}

export async function cached(db, key) {
  const row = await db.prepare('SELECT response FROM analysis_reports WHERE cache_key=?').bind(key).first();
  return row ? JSON.parse(row.response) : null;
}

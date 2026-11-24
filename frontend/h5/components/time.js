const BEIJING_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function parseUtc(value) {
  if (!value) return null;
  const normalized = String(value).includes("T")
    ? String(value)
    : String(value).replace(" ", "T");
  const withZone = /Z$|[+-]\d{2}:\d{2}$/.test(normalized) ? normalized : `${normalized}Z`;
  const date = new Date(withZone);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatBeijingTime(value, fallback = "Time TBD") {
  const date = parseUtc(value);
  if (!date) return fallback;
  return `Beijing time ${BEIJING_FORMATTER.format(date)}`;
}

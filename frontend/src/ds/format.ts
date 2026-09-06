// Форматы дат и чисел ровно как в docs/design/screens.html:
// колонка таблицы «17 фев», строка kv «18 февраля, 21:40», блок проверки
// «18.02, 21:40», баллы «4,5 из 6». Год не пишется нигде.
const SHORT = [
  "янв",
  "фев",
  "мар",
  "апр",
  "мая",
  "июн",
  "июл",
  "авг",
  "сен",
  "окт",
  "ноя",
  "дек",
];
const GENITIVE = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];
const pad = (n: number) => String(n).padStart(2, "0");
export const DASH = "—";

export function parseDate(value: string | Date | null | undefined) {
  if (!value) return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** «17 фев» — колонки дат в таблицах. */
export function dayShort(value: string | Date | null | undefined) {
  const d = parseDate(value);
  return d ? `${d.getDate()} ${SHORT[d.getMonth()]}` : DASH;
}

/** «18 февраля, 21:40» — строки «ключ — значение» и плашка. */
export function dayLong(value: string | Date | null | undefined) {
  const d = parseDate(value);
  return d
    ? `${d.getDate()} ${GENITIVE[d.getMonth()]}, ${pad(d.getHours())}:${pad(d.getMinutes())}`
    : DASH;
}

/** «18.02, 21:40» — блоки проверок и попыток, список домашек. */
export function dayNum(value: string | Date | null | undefined) {
  const d = parseDate(value);
  return d
    ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}, ${pad(d.getHours())}:${pad(d.getMinutes())}`
    : DASH;
}

/** «10.02» — только день, для сгруппированных событий. */
export function dayDot(value: string | Date | null | undefined) {
  const d = parseDate(value);
  return d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}` : DASH;
}

/** «4,5» — число с запятой, без лишних нулей. */
export function num(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(value))
    return DASH;
  return value.toLocaleString("ru-RU", { maximumFractionDigits: digits });
}

/** «1 б.», «0,5 б.» */
export function points(value: number | null | undefined) {
  return value === null || value === undefined ? DASH : `${num(value)} б.`;
}

/** «4,5 из 6» */
export function outOf(value: number | null | undefined, max: number) {
  return `${num(value)} из ${num(max)}`;
}

export function plural(n: number, one: string, few: string, many: string) {
  const abs = Math.abs(n) % 100;
  const last = abs % 10;
  if (abs > 10 && abs < 20) return many;
  if (last > 1 && last < 5) return few;
  if (last === 1) return one;
  return many;
}

/** «6 дней», «1 день», «2 дня» */
export function days(n: number) {
  return `${n} ${plural(n, "день", "дня", "дней")}`;
}

/** Целых дней от момента до «сейчас». */
export function daysSince(
  value: string | Date | null | undefined,
  now: Date = new Date(),
) {
  const d = parseDate(value);
  if (!d) return null;
  return Math.max(0, Math.floor((now.getTime() - d.getTime()) / 86_400_000));
}

/** Последние четыре знака идентификатора: хвосты различаются, головы часто одинаковые. */
export function short(id: string | null | undefined, length = 4) {
  const value = (id ?? "").replace(/-/g, "");
  return value.slice(-length);
}

/** Короткий номер студента из хвоста id: те же пять hex-знаков, что и на бэкенде. */
export function studentNumber(id: string): string {
  const tail = id.replace(/-/g, "").slice(-5);
  const n = Number.parseInt(tail, 16);
  return Number.isFinite(n) ? String(n) : short(id);
}

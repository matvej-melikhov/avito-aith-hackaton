/** Даты учебного стенда задаются в Europe/Moscow (UTC+03:00). */
export function moscowDate(value: string | null | undefined) {
  if (!value) return "";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Moscow",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}
export function moscowBoundary(value: string, end = false) {
  return new Date(
    `${value}T${end ? "23:59:59.999" : "00:00:00"}+03:00`,
  ).toISOString();
}

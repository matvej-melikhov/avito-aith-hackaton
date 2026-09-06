import { useRef, type ComponentPropsWithoutRef } from "react";
import { Area, Btn, BtnRow } from "./ds";

const MARKS: { label: string; title: string; wrap: [string, string] }[] = [
  { label: "Ж", title: "Жирный", wrap: ["**", "**"] },
  { label: "К", title: "Курсив", wrap: ["_", "_"] },
  { label: "‹›", title: "Код", wrap: ["`", "`"] },
  { label: "H2", title: "Заголовок", wrap: ["## ", ""] },
  { label: "•", title: "Список", wrap: ["- ", ""] },
  { label: "1.", title: "Нумерованный список", wrap: ["1. ", ""] },
  { label: "↗", title: "Ссылка", wrap: ["[", "](https://)"] },
];

export function MarkdownArea({
  value,
  onChange,
  ...rest
}: Omit<ComponentPropsWithoutRef<"textarea">, "value" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  function apply([before, after]: [string, string]) {
    const el = ref.current;
    if (!el) return;
    const start = el.selectionStart;
    const end = el.selectionEnd;
    onChange(
      value.slice(0, start) +
        before +
        value.slice(start, end) +
        after +
        value.slice(end),
    );
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + before.length, end + before.length);
    });
  }
  return (
    <>
      <BtnRow
        className="md-bar"
        role="group"
        aria-label="Форматирование условия"
      >
        {MARKS.map((mark) => (
          <Btn
            key={mark.title}
            size="s"
            variant="quiet"
            title={mark.title}
            aria-label={mark.title}
            onClick={() => apply(mark.wrap)}
          >
            {mark.label}
          </Btn>
        ))}
      </BtnRow>
      <Area
        ref={ref}
        {...rest}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </>
  );
}

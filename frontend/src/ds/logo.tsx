/* Знак и логотип Avito Reviewer. Геометрия один в один из
   docs/design/logo-options-2026-09-05/04-highlight.svg, цвета переведены на
   токены: чёрный становится --ink и переживает тёмную тему.               */
import { useId } from "react";
import { cx } from "./controls";

/** Кавычки: сердцевина знака. */
function QuotePaths() {
  return (
    <>
      <path
        d="M179 160H273V254C273 304 246 334 192 345V298C217 291 229 275 232 250H179Z"
        fill="var(--brand-blue)"
      />
      <path
        d="M294 158H391V254C391 305 363 336 307 347V299C333 292 346 275 349 249H294Z"
        fill="var(--brand-violet)"
      />
    </>
  );
}

const TITLE = "Avito Reviewer";

export function LogoMark({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg
      className={cx("logo logo--mark", className)}
      viewBox="179 158 212 189"
      role="img"
      aria-labelledby={id}
    >
      <title id={id}>{TITLE}</title>
      <QuotePaths />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg
      className={cx("logo logo--full", className)}
      viewBox="179 126 1744 238"
      role="img"
      aria-labelledby={id}
    >
      <title id={id}>{TITLE}</title>
      <QuotePaths />
      {/* Onest — шрифт интерфейса. textLength держит ширину, если он не успел
          загрузиться и подставилась запасная гарнитура. */}
      <text
        x="469"
        y="317"
        fill="var(--ink)"
        fontFamily="var(--font-text)"
        fontSize="190"
        fontWeight="700"
        letterSpacing="-8"
        textLength="464"
        lengthAdjust="spacingAndGlyphs"
      >
        Avito
      </text>
      <rect
        x="984"
        y="126"
        width="939"
        height="238"
        rx="119"
        fill="var(--brand-green)"
      />
      <text
        x="1071"
        y="315"
        fill="#1A1A1A"
        fontFamily="var(--font-text)"
        fontSize="190"
        fontWeight="700"
        letterSpacing="-8"
        textLength="780"
        lengthAdjust="spacingAndGlyphs"
      >
        Reviewer
      </text>
    </svg>
  );
}

/* Знак и логотип Avito Reviewer. Геометрия один в один из
   docs/design/logo-options-2026-09-05/04-highlight.svg, цвета переведены на
   токены: чёрный становится --ink и переживает тёмную тему.               */
import { useId } from "react";
import { cx } from "./controls";

/** Скобки с кавычками: знак без названия. Он же остаётся в узкой панели. */
function MarkPaths() {
  return (
    <g shapeRendering="geometricPrecision">
      <path d="M78 99H172V141H123V357H172V399H78Z" fill="var(--ink)" />
      <path
        d="M179 160H273V254C273 304 246 334 192 345V298C217 291 229 275 232 250H179Z"
        fill="var(--brand-blue)"
      />
      <path
        d="M294 158H391V254C391 305 363 336 307 347V299C333 292 346 275 349 249H294Z"
        fill="var(--brand-violet)"
      />
      <path d="M393 99H487V399H393V357H442V141H393Z" fill="var(--ink)" />
    </g>
  );
}

const TITLE = "Avito Reviewer";

export function LogoMark({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg
      className={cx("logo logo--mark", className)}
      viewBox="78 99 409 300"
      role="img"
      aria-labelledby={id}
    >
      <title id={id}>{TITLE}</title>
      <MarkPaths />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  const id = useId();
  return (
    <svg
      className={cx("logo logo--full", className)}
      viewBox="78 99 1941 300"
      role="img"
      aria-labelledby={id}
    >
      <title id={id}>{TITLE}</title>
      <MarkPaths />
      {/* Onest — шрифт интерфейса. textLength держит ширину, если он не успел
          загрузиться и подставилась запасная гарнитура. */}
      <text
        x="565"
        y="317"
        fill="var(--ink)"
        fontFamily="var(--font-display)"
        fontSize="190"
        fontWeight="700"
        letterSpacing="-8"
        textLength="464"
        lengthAdjust="spacingAndGlyphs"
      >
        Avito
      </text>
      <rect
        x="1080"
        y="126"
        width="939"
        height="238"
        rx="119"
        fill="var(--brand-green)"
      />
      <text
        x="1167"
        y="315"
        fill="#1A1A1A"
        fontFamily="var(--font-display)"
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

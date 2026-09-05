import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { Field, Inp, num } from "../src/ds";

it("keeps the visible field label, hint and validation attached to an existing input id", () => {
  render(
    <Field label="Баллы" hint="Можно дробное число" error="Не больше пяти">
      <Inp id="existing-score" />
    </Field>,
  );
  const input = screen.getByRole("textbox", { name: "Баллы" });
  expect(input).toHaveAttribute("id", "existing-score");
  expect(input).toHaveAccessibleDescription(
    "Можно дробное число Не больше пяти",
  );
  expect(input).toHaveAttribute("aria-invalid", "true");
});

it("does not round accepted quarter-point grades to a different visible score", () => {
  expect(num(2.75 + 2.5)).toBe("5,25");
});

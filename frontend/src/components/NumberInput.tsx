import { useEffect, useState, type InputHTMLAttributes } from "react";

type Base = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type">;

/**
 * Numeric input that keeps the user's raw text while editing.
 * A plain controlled <input type="number"> turns "" into 0 and writes it back,
 * which makes it impossible to delete a leading zero. Here the parent only
 * receives a value when the text parses; empty text becomes `null` (nullable)
 * or leaves the previous value in place (non-nullable) until blur.
 */
export function NumberInput({
  value,
  onChange,
  nullable = false,
  ...rest
}: Base & {
  value: number | null;
  onChange: (v: number | null) => void;
  nullable?: boolean;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  const [focused, setFocused] = useState(false);

  // Sync from the parent when not being edited (e.g. form reset, preset select).
  useEffect(() => {
    if (!focused) setText(value === null ? "" : String(value));
  }, [value, focused]);

  return (
    <input
      {...rest}
      type="number"
      inputMode="decimal"
      value={text}
      onFocus={(e) => {
        setFocused(true);
        rest.onFocus?.(e);
      }}
      onBlur={(e) => {
        setFocused(false);
        const n = Number(text);
        if (text.trim() === "" || Number.isNaN(n)) {
          if (nullable) onChange(null);
          else setText(value === null ? "" : String(value));
        } else {
          setText(String(n));
        }
        rest.onBlur?.(e);
      }}
      onChange={(e) => {
        const raw = e.target.value;
        setText(raw);
        if (raw.trim() === "") {
          if (nullable) onChange(null);
          return;
        }
        const n = Number(raw);
        if (!Number.isNaN(n)) onChange(n);
      }}
    />
  );
}

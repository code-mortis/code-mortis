import { useEffect, useMemo, useRef, useState } from "react";
import {
  Combobox, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList,
} from "./ui/combobox";
import { Popover, PopoverAnchor, PopoverContent } from "./ui/popover";
import { cn } from "../lib/utils";

export function SearchSelect({
  id, value, onChange, options, placeholder = "Choose an option", disabled, required,
  className, "aria-label": ariaLabel, allowCustom = false,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  className?: string;
  "aria-label"?: string;
  allowCustom?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState<string | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const labels = useMemo(() => new Map(options.map((option) => [option.value, option.label])), [options]);
  const label = (item: string) => labels.get(item) ?? item;
  useEffect(() => {
    if (disabled) {
      setOpen(false);
      setSearch(null);
    }
  }, [disabled]);
  function changeOpen(next: boolean) {
    setOpen(next);
    if (!next) setSearch(null);
  }
  function commitCustom() {
    const next = search?.trim();
    if (allowCustom && next && !disabled) onChange(next);
  }
  return (
    <Combobox
      items={options.map((option) => option.value)}
      value={value || null}
      onValueChange={(next, event) => {
        if (event.reason === "input-clear") return;
        onChange(next ?? "");
        changeOpen(false);
      }}
      itemToStringLabel={label}
      filter={(item, query) => `${label(item)} ${item}`.toLowerCase().includes(query.trim().toLowerCase())}
      disabled={disabled}
      required={required}
      autoHighlight
      inline
      open={open && !disabled}
      onOpenChange={changeOpen}
      inputValue={open && search !== null ? search : value ? label(value) : ""}
      onInputValueChange={(next, event) => {
        if (event.reason === "input-change") {
          setSearch(next);
          setOpen(true);
        }
      }}
    >
      {/* Radix owns the popup layer so focus and Escape stay inside the existing dialogs. */}
      <Popover open={open && !disabled} onOpenChange={changeOpen}>
        <PopoverAnchor asChild>
          <div ref={anchor} className={cn("w-full min-w-0", className)}>
            <ComboboxInput id={id} aria-label={ariaLabel} aria-expanded={open && !disabled} disabled={disabled}
              placeholder={placeholder} className="w-full bg-card dark:bg-card"
              onBlur={() => {
                commitCustom();
                if (allowCustom) changeOpen(false);
              }}
              onKeyDown={(event) => {
                if (allowCustom && event.key === "Enter" && !event.currentTarget.getAttribute("aria-activedescendant")) {
                  event.preventDefault();
                  commitCustom();
                  changeOpen(false);
                }
              }}
              maxLength={allowCustom ? 100 : undefined} />
          </div>
        </PopoverAnchor>
        <PopoverContent role="presentation" align="start" sideOffset={6} collisionPadding={16}
          className="w-(--radix-popover-trigger-width) max-w-[calc(100vw-2rem)] p-0"
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => {
            if (anchor.current?.contains(event.target as Node)) event.preventDefault();
            else commitCustom();
          }}>
          <ComboboxEmpty className="block empty:p-0">{allowCustom ? "Use the language you entered." : "No matches found."}</ComboboxEmpty>
          <ComboboxList className="max-h-60">
            {(item: string) => <ComboboxItem key={item} value={item} className="min-h-10 break-words [overflow-wrap:anywhere]">{label(item)}</ComboboxItem>}
          </ComboboxList>
        </PopoverContent>
      </Popover>
    </Combobox>
  );
}

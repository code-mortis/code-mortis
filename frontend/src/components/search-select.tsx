import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Combobox, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList,
} from "./ui/combobox";
import { Popover, PopoverAnchor, PopoverContent } from "./ui/popover";
import { InputGroupAddon } from "./ui/input-group";
import { cn } from "../lib/utils";

export function SearchSelect({
  id, value, onChange, options, placeholder = "Choose an option", disabled, required,
  className, "aria-label": ariaLabel, "aria-describedby": ariaDescribedBy, allowCustom = false,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string; icon?: ReactNode }[];
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
  allowCustom?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState<string | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const choices = useMemo(() => new Map(options.map((option) => [option.value, option])), [options]);
  const label = (item: string) => choices.get(item)?.label ?? item;
  const selectedIcon = open && search !== null ? undefined : choices.get(value)?.icon;
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
      value={value}
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
            <ComboboxInput id={id} aria-label={ariaLabel} aria-describedby={ariaDescribedBy} aria-expanded={open && !disabled} disabled={disabled}
              placeholder={placeholder} className="w-full bg-card dark:bg-card"
              onBlur={() => {
                commitCustom();
                if (allowCustom) changeOpen(false);
              }}
              onKeyDown={(event) => {
                if (open && allowCustom && event.key === "Enter" && !event.currentTarget.getAttribute("aria-activedescendant")) {
                  event.preventDefault();
                  commitCustom();
                  changeOpen(false);
                }
              }}
              maxLength={allowCustom ? 100 : undefined}>
              {selectedIcon && <InputGroupAddon align="inline-start"><span aria-hidden="true" className="flex">{selectedIcon}</span></InputGroupAddon>}
            </ComboboxInput>
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
            {(item: string) => <ComboboxItem key={item} value={item} className="min-h-10 break-words [overflow-wrap:anywhere]">
              {choices.get(item)?.icon}
              <span className="min-w-0">{label(item)}</span>
            </ComboboxItem>}
          </ComboboxList>
        </PopoverContent>
      </Popover>
    </Combobox>
  );
}

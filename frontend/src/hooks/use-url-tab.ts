import { useEffect, useLayoutEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

export function useUrlTab(key: string, values: string[], defaultValue: string) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const selected = params.get(key);
  const value = selected && values.includes(selected) ? selected : defaultValue;
  const requested = useRef(value);
  useLayoutEffect(() => { requested.current = value; }, [value]);
  useEffect(() => {
    if (selected !== null && selected !== value) {
      const next = new URLSearchParams(params);
      next.delete(key);
      setParams(next, { replace: true, state: location.state });
    }
  }, [selected, value, key, params, setParams, location.state]);
  function change(nextValue: string) {
    // Radix may select a tab on both pointer down and focus before the URL renders.
    if (requested.current === nextValue) return;
    requested.current = nextValue;
    const next = new URLSearchParams(params);
    if (nextValue === defaultValue) next.delete(key); else next.set(key, nextValue);
    setParams(next, { state: location.state });
  }
  return [value, change] as const;
}

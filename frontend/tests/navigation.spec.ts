import { expect, test } from "@playwright/test";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { useIsMobile } from "../src/hooks/use-mobile";
import { getDetailPath } from "../src/lib/navigation";

test("detail paths normalize trailing slashes and exclude unrelated routes", () => {
  for (const path of ["/pulls/pr", "/runs/run"]) {
    for (const suffix of ["", "/", "//"]) expect(getDetailPath(path + suffix)).toBe(path);
  }
  for (const path of ["/", "/pulls", "/runs/", "/repositories/22", "/pulls/pr/files"]) {
    expect(getDetailPath(path)).toBeNull();
  }
});

test("mobile detection uses the media query on the first render", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "window");
  try {
    for (const matches of [true, false]) {
      Object.defineProperty(globalThis, "window", { configurable: true, value: {
        innerWidth: matches ? 1440 : 390,
        matchMedia: (query: string) => {
          expect(query).toBe("(max-width: 1023px)");
          return { matches };
        },
      } });
      expect(renderToString(createElement(() => String(useIsMobile())))).toBe(String(matches));
    }
  } finally {
    if (original) Object.defineProperty(globalThis, "window", original);
    else Reflect.deleteProperty(globalThis, "window");
  }
});

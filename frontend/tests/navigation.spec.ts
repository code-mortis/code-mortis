import { expect, test } from "@playwright/test";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { useIsMobile } from "../src/hooks/use-mobile";
import { getDetailPath, getRepositoryReturn } from "../src/lib/navigation";

test("repository return only accepts a local repository path and query", () => {
  for (const repository of ["/repositories/22", "/repositories/22?q=retry&merged_pages=2"]) {
    expect(getRepositoryReturn({ repository })).toBe(repository);
  }
  for (const repository of [undefined, 22, "//example.com", "/repositories/0", "/repositories/22/other", "/repositories/22#other", "/pulls/pr", "https://example.com"]) {
    expect(getRepositoryReturn({ repository })).toBeNull();
  }
  expect(getRepositoryReturn(null)).toBeNull();
});

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

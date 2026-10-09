import { expect, test } from "@playwright/test";
import { getInboxReturn, INBOX_MAX_PAGES, INBOX_QUERY_MAX_LENGTH, normalizeFilters, selected, withoutPages } from "../src/lib/inbox-filters";

test("filters canonicalize author handles and bound shared pagination", () => {
  const params = normalizeFilters(new URLSearchParams("author=%20@@Alice%20&author=ALICE&author=@&active_pages=999999&reviewed_pages=3"));
  expect(params.getAll("author")).toEqual(["alice"]);
  expect(params.get("active_pages")).toBe(String(INBOX_MAX_PAGES));
  expect(normalizeFilters(new URLSearchParams({ q: "a".repeat(300) })).get("q")).toHaveLength(INBOX_QUERY_MAX_LENGTH);
  expect(withoutPages(new URLSearchParams("reviewed_pages=3&active_pages=2&merged_pages=4&closed_pages=5&q=fix")).toString()).toBe("q=fix");
});

test("state selection returns independent values and preserves defaults", () => {
  const all = new URLSearchParams("state=all");
  selected(all, "state").pop();
  expect(selected(all, "state")).toEqual(["open", "merged", "closed"]);
  expect(selected(new URLSearchParams(), "state")).toEqual(["open", "merged", "closed"]);
  expect(selected(new URLSearchParams("author=alice"), "author")).toEqual(["alice"]);
  expect(normalizeFilters(new URLSearchParams("state=open&state=merged&state=closed")).toString()).toBe("");
  expect(normalizeFilters(new URLSearchParams("state=open")).toString()).toBe("state=open");
  expect(normalizeFilters(new URLSearchParams("state=all")).toString()).toBe("");
});

test("inbox return accepts only the inbox path and query", () => {
  for (const state of [null, undefined, 42, "/", {}, { inbox: 3 }, { inbox: "//example.com" }, { inbox: "/pulls/pr" }, { inbox: "https://example.com" }]) {
    expect(getInboxReturn(state)).toBeNull();
  }
  for (const inbox of ["/", "/?author=alice&active_pages=2"]) expect(getInboxReturn({ inbox })).toBe(inbox);
});

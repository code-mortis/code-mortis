import { expect, test } from "@playwright/test";
import { normalizeReviewFilters, withReviewFilters } from "../src/lib/review-filters";

test("review filters preserve default and explicitly unrestricted finding statuses", () => {
  for (const query of ["", "status=unknown"]) {
    expect(normalizeReviewFilters(new URLSearchParams(query), "pull").getAll("status")).toEqual(["open", "resolved"]);
  }
  expect(normalizeReviewFilters(new URLSearchParams("status=all"), "pull").getAll("status")).toEqual(["all"]);
  expect(normalizeReviewFilters(new URLSearchParams("status=resolved&status=resolved&status=unknown"), "pull").getAll("status")).toEqual(["resolved"]);
});

test("review filters bound URL input to the existing API constraints", () => {
  const raw = new URLSearchParams("severity=high&severity=invalid&severity=high&category=%20bug%20&category=bug&category=&outcome=invalid&outcome=withheld");
  raw.set("q", " " + "a".repeat(300));
  raw.append("category", "b".repeat(51));
  const params = normalizeReviewFilters(raw, "pull");
  expect(params.get("q")).toHaveLength(256);
  expect(params.getAll("severity")).toEqual(["high"]);
  expect(params.getAll("category")).toEqual(["bug"]);
  expect(params.has("outcome")).toBe(false);
  const many = new URLSearchParams();
  for (let index = 0; index < 150; index++) many.append("category", `type_${index}`);
  expect(normalizeReviewFilters(many, "run").getAll("category")).toHaveLength(100);
});

test("run and assessment filters normalize independently and preserve tabs and open sheets", () => {
  const raw = new URLSearchParams("tab=assessment&finding=finding&finding_tab=activity&candidate=failed&assessment_tab=evidence&q=retry&severity=high&assessment_q=%20timeout%20&assessment_severity=low&assessment_outcome=withheld&assessment_outcome=invalid");
  const assessment = normalizeReviewFilters(raw, "assessment");
  expect(assessment.get("q")).toBe("timeout");
  expect(assessment.getAll("severity")).toEqual(["low"]);
  expect(assessment.getAll("outcome")).toEqual(["withheld"]);
  const next = withReviewFilters(raw, assessment, "assessment");
  expect(normalizeReviewFilters(next, "run").toString()).toBe("q=retry&severity=high");
  for (const key of ["tab", "finding", "finding_tab", "candidate", "assessment_tab"]) expect(next.get(key)).toBe(raw.get(key));
  expect(withReviewFilters(next, new URLSearchParams(), "run").get("assessment_q")).toBe("timeout");
  expect(withReviewFilters(next, new URLSearchParams(), "assessment").get("q")).toBe("retry");
});

test("resolved filters normalize and clear without changing findings or assessment", () => {
  const raw = new URLSearchParams("tab=resolved&finding=finding&q=retry&severity=high&assessment_q=timeout&resolved_q=%20fixed%20&resolved_severity=low&resolved_severity=invalid&resolved_category=bug&resolved_category=bug");
  const filters = normalizeReviewFilters(raw, "resolved");
  expect(filters.toString()).toBe("q=fixed&severity=low&category=bug");
  const next = withReviewFilters(raw, filters, "resolved");
  expect(normalizeReviewFilters(next, "run").toString()).toBe("q=retry&severity=high");
  expect(normalizeReviewFilters(next, "assessment").toString()).toBe("q=timeout");
  const cleared = withReviewFilters(next, new URLSearchParams(), "resolved");
  expect(cleared.get("q")).toBe("retry");
  expect(cleared.get("assessment_q")).toBe("timeout");
  expect(cleared.get("tab")).toBe("resolved");
  expect(cleared.get("finding")).toBe("finding");
  expect(cleared.has("resolved_q")).toBe(false);
});

test("run finding results normalize without consuming assessment outcomes", () => {
  const raw = new URLSearchParams("outcome=new&outcome=resolved_repeat&outcome=resolved_repeat&outcome=withheld&outcome=unknown&outcome=__proto__&assessment_outcome=withheld&assessment_outcome=resolved_repeat&tab=assessment&finding=one");
  const run = normalizeReviewFilters(raw, "run");
  expect(run.getAll("outcome")).toEqual(["new", "resolved_repeat"]);
  expect(normalizeReviewFilters(raw, "assessment").getAll("outcome")).toEqual(["withheld"]);
  const cleared = withReviewFilters(raw, new URLSearchParams(), "run");
  expect(cleared.has("outcome")).toBe(false);
  expect(cleared.get("assessment_outcome")).toBe("withheld");
  expect(cleared.get("tab")).toBe("assessment");
  expect(cleared.get("finding")).toBe("one");
});


test("review ordering is validated per list and independent across tabs", () => {
  const raw = new URLSearchParams("ordering=-updated_at&assessment_ordering=-severity&resolved_ordering=path");
  expect(normalizeReviewFilters(raw, "pull").get("ordering")).toBe("-updated_at");
  expect(normalizeReviewFilters(raw, "assessment").get("ordering")).toBe("-severity");
  expect(normalizeReviewFilters(raw, "resolved").get("ordering")).toBe("path");
  expect(normalizeReviewFilters(raw, "run").has("ordering")).toBe(false);
  const next = withReviewFilters(raw, new URLSearchParams("ordering=-path"), "assessment");
  expect(next.get("ordering")).toBe("-updated_at");
  expect(next.get("assessment_ordering")).toBe("-path");
  expect(next.get("resolved_ordering")).toBe("path");
  for (const scope of ["pull", "run", "assessment", "resolved"] as const) {
    expect(normalizeReviewFilters(new URLSearchParams(`${scope === "assessment" ? "assessment_" : scope === "resolved" ? "resolved_" : ""}ordering=invalid`), scope).has("ordering")).toBe(false);
  }
});

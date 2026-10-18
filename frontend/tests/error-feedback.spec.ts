import { expect, test } from "@playwright/test";
import { errorFeedback } from "../src/lib/error-feedback";

for (const [raw, title] of [
  ['OCR returned an invalid verification verdict: Security evidence does not match its cited source lines.; response={"findings":[]}', "Evidence could not be verified"],
  ['The native security result failed source validation: Security evidence cites source that was not supplied.', "Evidence could not be verified"],
  ['Required security evidence is unavailable or exceeds its limit.', "Supporting evidence unavailable"],
  ['Required security evidence could not be supplied: guard.py. This path is not in the prepared snapshot at the scanned commit.', "Supporting evidence unavailable"],
  ['Required security evidence could not be supplied: guard.py. The 100-source-file evidence limit was reached; this is separate from time and token budgets.', "Supporting evidence unavailable"],
  ['AI token usage is unknown; response={"internal":"output"}', "AI usage could not be verified"],
  ['Security token budget exhausted.', "AI limit reached"],
  ['OCR returned an invalid verification verdict; response={"invalid":true}', "AI response could not be used"],
  ['OCR returned an invalid structured result; response={"invalid":true}', "AI response could not be used"],
  ['TypeError: fetch failed ECONNREFUSED', "Connection failed"],
  ['HTTP 401: {"error":"invalid_api_key"}', "Authentication required"],
  ['HTTP 403: {"error":"forbidden"}', "Access denied"],
  ['HTTP 429: {"error":"rate_limit_exceeded"}', "Service limit reached"],
  ['sqlalchemy.exc.OperationalError: database connection failed', "Action could not be completed"],
  ['Unexpected output: {"internal":"data"}', "Action could not be completed"],
  ["x".repeat(1000), "Action could not be completed"],
] as const) {
  test(`shared errors explain ${title}: ${raw.slice(0, 95)}`, () => {
    const feedback = errorFeedback(new Error(raw));
    expect(feedback.title).toBe(title);
    expect(feedback.details).toBe(raw);
    expect(feedback.message).not.toMatch(/response=|\{"|sqlalchemy|ECONNREFUSED/);
    expect(feedback.message.length).toBeLessThan(250);
  });
}

test("security evidence errors distinguish unavailable source from actual limits and legacy records", () => {
  const missing = "guard.py. This path is not in the prepared snapshot at the scanned commit.";
  expect(errorFeedback(`Required security evidence could not be supplied: ${missing}`).message).toBe(missing);
  expect(errorFeedback("Required security evidence could not be supplied: guard.py. File exceeds the 4 MB evidence limit").message).toContain("4 MB evidence limit");
  expect(errorFeedback("Required security evidence is unavailable or exceeds its limit.").message).toContain("older record did not save the requested path");
});

test("shared errors preserve actionable messages and separate technical suffixes", () => {
  for (const message of [
    "Enable Issues: Read and write on the existing GitHub App and approve the installation permission update. Security scans and reports remain available.",
    "Your session has expired. Sign in with GitHub again.",
    "The request contains invalid JSON. Check quotes, commas, and braces.",
    "Settings / token budget: Enter a value no greater than 5,000,000.",
  ]) expect(errorFeedback(message)).toEqual({ title: undefined, message, details: "" });
  const raw = 'The scanner could not refresh its database. Technical details: {"exit_code":1}';
  expect(errorFeedback(raw)).toEqual({ title: undefined, message: "The scanner could not refresh its database.", details: raw });
  expect(errorFeedback({})).toEqual({ title: undefined, message: "This action could not be completed. Refresh the page and try again.", details: "" });
});

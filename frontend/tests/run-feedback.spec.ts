import { expect, test } from "@playwright/test";
import { assessmentFeedback } from "../src/lib/run-feedback";

test("assessment feedback extracts one complete JSON object before trailing diagnostics", () => {
  const detail = { field: "evidence[2]", reason: "empty quote", revision: "original", path: 'src/{quoted"path\\}.ts',
    start_line: 7, end_line: 9, context: { quote: "{}", values: [{ nested: true }] } };
  for (const suffix of ["", " Technical details: diagnostic text", ' Extra diagnostics: {"error":"unavailable"}', "; response={}"]) {
    expect(assessmentFeedback("Assessment evidence does not match the supplied source. " + JSON.stringify(detail) + suffix)).toEqual({
      title: "Evidence could not be verified", message: "The AI did not include a source quote.",
      citation: `Citation 3 · Original source: ${detail.path}:7–9`,
    });
  }
  expect(assessmentFeedback('Invalid assessment. {"field":"finding_id","reason":"unavailable"} Trailing text').title)
    .toBe("Finding reference could not be verified");
});

test("assessment feedback tolerates malformed or truncated JSON", () => {
  for (const payload of ['{"field":"evidence[2]"', '{"field":"evidence[2]",}', '{"field":"evidence[2]","reason":"unclosed }']) {
    expect(assessmentFeedback("Assessment evidence does not match the supplied source. " + payload)).toEqual({
      title: "Evidence could not be verified", message: "The AI returned a source citation that could not be validated.", citation: undefined,
    });
  }
});

test("verification explains the saved budget before the missing-result diagnostic", () => {
  const error = 'The fix could not be verified automatically. Technical details: {"warnings": [{"message": '
    + '"stopped in batch #0: used 0 tokens + next-file estimate exceeds budget 200000"}], '
    + '"result_error": "The AI did not return exactly one final assessment.", "diagnostics": ';
  for (const suffix of ['"long diagnostic"}', '"truncated']) {
    expect(assessmentFeedback(error + suffix, "verification")).toEqual({
      title: "Verification token limit reached",
      message: "This check stopped before contacting the AI: its estimated request exceeded the saved limit of 200,000 tokens. Start a new review to use current settings.",
    });
  }
  const used = assessmentFeedback(error.replace("used 0 tokens", "used 5000 tokens"), "verification");
  expect(used.message).toContain("5,000 already used");
  expect(used.message).not.toContain("before contacting");
  expect(assessmentFeedback('Unknown failure: {"diagnostics":"internal output"}', "verification"))
    .toEqual({ title: "Verification could not be completed", message: "No usable verification was saved. Inspect the technical details before starting another review." });
});

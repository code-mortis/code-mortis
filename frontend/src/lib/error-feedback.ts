export function errorFeedback(error: unknown) {
  const raw = (error instanceof Error ? error.message : typeof error === "string" ? error : "").trim();
  if (!raw) return { title: undefined, message: "This action could not be completed. Refresh the page and try again.", details: "" };
  const message = raw.split(/; response=| Technical details:| Result details:/, 1)[0].trim();
  const explain = (title: string, message: string) => ({ title, message, details: raw });

  if (/token (?:consumption|usage) is unknown|token usage.*(?:unavailable|invalid)|did not report valid token usage/i.test(message)) {
    return explain("AI usage could not be verified", "The AI service did not report reliable token usage. Further requests were stopped. Check the connection and technical details before retrying.");
  }
  if (/(?:Security|Verification|Assessment) evidence|source validation|source citation|cited source lines/i.test(message)) {
    return explain("Evidence could not be verified", "The AI returned source evidence that did not match the supplied code. Review the technical details and retry the run. Unverified results were not accepted.");
  }
  if (/token budget.*(?:exhausted|reached)|no token budget remains|exceeds budget|evidence prompt exceeds|context limit exhausted/i.test(message)) {
    return explain("AI limit reached", "The analysis could not continue within its configured limits. Review the token budget and prompt limit before retrying.");
  }
  if (/invalid (?:verification verdict|structured result)|OCR returned invalid JSON|(?:AI|review engine|native).*unsupported.*(?:format|response)|did not return.*final|did not finish checking|OCR verification process failed/i.test(message)) {
    return explain("AI response could not be used", "The AI did not return a complete, valid result. Check the selected connection, model, and technical details before retrying.");
  }
  if (/ECONN\w+|ENOTFOUND|Failed to fetch|fetch failed|NetworkError/i.test(message)) {
    return explain("Connection failed", "A service could not be reached. Check your connection and the service status, then try again.");
  }
  if (/HTTP 401|invalid_api_key|authentication failed/i.test(message)) {
    return explain("Authentication required", "The connected service rejected its credentials. Check or reconnect the affected account before retrying.");
  }
  if (/HTTP 403|permission denied/i.test(message)) {
    return explain("Access denied", "The service denied access to this operation. Check repository and connection permissions before retrying.");
  }
  if (/HTTP 429|rate_limit_exceeded|insufficient_quota/i.test(message)) {
    return explain("Service limit reached", "The connected service reached a usage or request limit. Check the account limits and retry when capacity is available.");
  }
  if (/Traceback \(most recent call last\)|(?:sqlalchemy|psycopg)\.|\[SQL:|^\s*[\[{<]|\b(?:TypeError|RuntimeError|ValueError|KeyError):/.test(message)
    || message.length > 600 || !message) {
    return explain("Action could not be completed", "This action could not be completed. Review the technical details. If it keeps failing, ask the administrator to check the service.");
  }
  if (/[{}]|\n/.test(message)) {
    return explain("Action could not be completed", "This action could not be completed. Review the technical details before trying again.");
  }
  return { title: undefined, message, details: message === raw ? "" : raw };
}

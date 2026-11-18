export function engineDiagnostic(message: string) {
  return message.replace(/\bGateway\/OCR\b/g, "Gateway and engine")
    .replace(/\b(?:Open Code Review|OCR)\b/g, "retired engine")
    .replace(/^retired engine\b/, "The retired engine");
}

export function resultDeliveryFeedback(error: string) {
  if (!/AI result delivery failed|The AI did not submit a complete structured result|task_done did not include|"kind"\s*:\s*"delivery"/.test(error)) return undefined;
  const message = /exactly one complete task_done/.test(error)
    ? "The AI did not submit exactly one complete structured result."
    : /arguments (?:are not valid JSON|must contain JSON text)/.test(error)
      ? "The AI completion call contained invalid JSON arguments."
      : /did not include the required DONE state and result object/.test(error)
        ? "The AI completion call omitted the required DONE state or structured result."
        : "The AI completion call did not match the required result format.";
  const summary = error.split(/; response=| Technical details:/, 1)[0];
  const origin = /The gateway returned incomplete or malformed completion arguments\.$/.test(summary)
    ? "The gateway response already contained incomplete or malformed arguments."
    : /parsed completion differs from the gateway response\.$/.test(summary)
      ? "The engine changed or lost data from the gateway's completion."
      : /recorded identical completion arguments\.$/.test(summary)
        ? "The gateway and the engine recorded identical arguments."
        : /comparison was unavailable; the origin could not be determined\.$/.test(summary)
          ? "The gateway and engine could not be compared, so the origin is unknown." : "";
  return { title: "AI result delivery failed", message: `${message}${origin ? ` ${origin}` : ""} The affected check was not accepted.` };
}

export function errorFeedback(error: unknown) {
  const raw = (error instanceof Error ? error.message : typeof error === "string" ? error : "").trim();
  if (!raw) return { title: undefined, message: "This action could not be completed. Refresh the page and try again.", details: "" };
  const message = raw.split(/; response=| Technical details:| Result details:/, 1)[0].trim();
  const explain = (title: string, message: string) => ({ title, message, details: raw });
  if (/token (?:consumption|usage) is unknown|token usage.*(?:unavailable|invalid)|did not report valid token usage/i.test(message)) {
    return explain("AI usage could not be verified", "The AI service did not report reliable token usage. Further requests were stopped. Check the connection and technical details before retrying.");
  }
  const delivery = resultDeliveryFeedback(raw);
  if (delivery) return { ...delivery, details: raw };
  if (/Inference request exceeds the connection prompt token ceiling/i.test(message)) {
    return explain("AI prompt limit reached", "The evidence for one or more checks exceeded the connection's prompt limit. Those checks remain unverified. Technical details identify the affected work.");
  }
  if (/Required security evidence|Required supporting source cannot be supplied safely|AI requested evidence it already received|Security analysis could not settle its required evidence/i.test(message)) {
    return explain("Supporting evidence unavailable", message.includes("unavailable or exceeds its limit")
      ? "Supporting source could not be supplied. This older record did not save the requested path or distinguish missing source from an evidence limit."
      : engineDiagnostic(message.replace(/^Required security evidence could not be supplied:\s*/i, "")));
  }
  if (/AI citation mismatch|(?:Security|Verification|Assessment) evidence does not match/i.test(message)
    && !/"reason"\s*:\s*"unavailable source"/.test(message)) {
    const path = /^AI citation mismatch in (.+?): /.exec(message)?.[1];
    return explain("AI citation mismatch", `The AI's quote or cited line numbers do not match ${path ?? "the supplied source"}. The source was read; this is not a missing-file error. Unverified results were not accepted.`);
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
  return { title: undefined, message: engineDiagnostic(message), details: message === raw ? "" : raw };
}

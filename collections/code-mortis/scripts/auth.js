if (noodle.request.url.includes("REPLACE_ME") || (noodle.request.body.text() || "").includes("REPLACE_ME")) {
  throw new Error("Set the resource IDs and request values in the selected environment before sending. See README.md.");
}

const token = noodle.env.get("cm_api_token");
if (!token) {
  throw new Error("Create a personal API token in Settings and store it with noodle secret set cm_api_token. See README.md.");
}
if (!token.startsWith("cm_pat_")) {
  throw new Error("Use a personal API token from Settings for cm_api_token. See README.md.");
}

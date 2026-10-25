if (noodle.request.url.includes("REPLACE_ME") || (noodle.request.body.text() || "").includes("REPLACE_ME")) {
  throw new Error("Set the resource IDs and request values in the selected environment before sending. See README.md.");
}

const session = noodle.env.get("cm_session");
if (!session) {
  throw new Error("Store the cm_session cookie from your GitHub sign-in with noodle secret set cm_session. See README.md.");
}

const cookie = `cm_session=${session}`;
noodle.request.headers.set("Cookie", cookie);

if (!["GET", "HEAD", "OPTIONS"].includes(noodle.request.method)) {
  const me = await noodle.sendRequest({
    method: "GET",
    url: `${noodle.env.get("base_url").replace(/\/+$/, "")}/api/v1/auth/me`,
    headers: { Cookie: cookie },
    timeout: 10000,
  });
  const csrf = me.json().csrf;
  if (typeof csrf !== "string" || !csrf) {
    throw new Error("The session did not return a CSRF token. Sign in again and replace cm_session.");
  }
  noodle.request.headers.set("X-CSRF-Token", csrf);
}

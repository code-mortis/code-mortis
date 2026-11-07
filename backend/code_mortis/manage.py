"""Operator commands. init-env uses only Python's standard library."""

import argparse
import base64
import json
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description="Code Mortis operator utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-env", help="Create a new .env without overwriting an existing configuration")
    init.add_argument("--owner", required=True, help="Instance owner's GitHub username")
    init.add_argument("--url", default="http://localhost:8080")
    init.add_argument("--output", default=".env")
    init.add_argument(
        "--webhook-url", default="", help="Public webhook URL, independently of the browser origin"
    )
    upgrade = sub.add_parser("upgrade-env", help="Add missing configuration without replacing secrets")
    upgrade.add_argument("--file", default=".env")
    export = sub.add_parser("export-openapi")
    export.add_argument("--output", default="../frontend/node_modules/.cache/code-mortis-openapi.json")
    sub.add_parser("retire-subscriptions", help="Cancel retired inference durably after stopping old workers and fencing their runner endpoint")
    args = parser.parse_args()
    if args.command == "retire-subscriptions":
        from code_mortis.core.db import transaction
        from code_mortis.core.shared_execution import retire_subscriptions

        with transaction() as db:
            result = retire_subscriptions(db)
        print(json.dumps(result, sort_keys=True))
    elif args.command == "upgrade-env":
        path = Path(args.file)
        if not path.is_file():
            parser.error(f"{args.file} does not exist; use init-env for a new instance")
        content = path.read_text()
        missing = []
        for key in ("CM_RUNNER_TOKEN", "CM_CODE_GRAPH_TOKEN"):
            if re.search(rf"(?m)^{key}=.+$", content):
                continue
            if re.search(rf"(?m)^{key}=", content):
                parser.error(f"{key} is empty; remove the empty line before upgrading")
            missing.append(key)
        if not missing:
            print("Service configuration already exists; no values were changed.")
            return
        path.chmod(0o600)
        with path.open("a") as output:
            output.write(
                ("" if content.endswith("\n") else "\n")
                + "".join(f"{key}={secrets.token_urlsafe(32)}\n" for key in missing)
            )
        print(f"Added {', '.join(missing)}. Existing keys, credentials, URLs and data were preserved.")
    elif args.command == "init-env":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.owner):
            parser.error("Use a valid GitHub username")
        url = urlsplit(args.url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
            or url.username
        ):
            parser.error("Use an HTTP(S) origin without a path, query, or credentials")
        if url.scheme != "https" and url.hostname not in {"localhost", "127.0.0.1"}:
            parser.error("Remote installations require HTTPS")
        if args.webhook_url:
            webhook = urlsplit(args.webhook_url)
            if (
                webhook.scheme != "https"
                or not webhook.hostname
                or webhook.hostname in {"localhost", "127.0.0.1"}
                or webhook.username
                or webhook.query
                or webhook.fragment
            ):
                parser.error("Use a public HTTPS webhook URL without credentials, query or fragment")
        content = {
            "CM_PUBLIC_URL": args.url.rstrip("/"),
            "CM_WEBHOOK_URL": args.webhook_url,
            "CM_OWNER_GITHUB_LOGIN": args.owner,
            "CM_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
            "CM_BOOTSTRAP_TOKEN": secrets.token_urlsafe(32),
            "CM_RUNNER_TOKEN": secrets.token_urlsafe(32),
            "CM_CODE_GRAPH_TOKEN": secrets.token_urlsafe(32),
            "COMPOSE_PROFILES": "",
            "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
            "CM_COOKIE_SECURE": "true" if url.scheme == "https" else "false",
            "CM_ALLOW_PRIVATE_ENDPOINTS": "false",
        }
        try:
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            parser.error(f"{args.output} already exists; it was not changed")
        with os.fdopen(fd, "w") as output:
            output.write("\n".join(f"{key}={value}" for key, value in content.items()) + "\n")
        print(f"Created {args.output} with private permissions. Keep this file with your encrypted backups.")
    else:
        # Schema generation does not use credentials or connect to services.
        os.environ.setdefault("CM_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
        os.environ.setdefault("CM_BOOTSTRAP_TOKEN", "schema-generation-only-token")
        os.environ.setdefault("CM_OWNER_GITHUB_LOGIN", "schema-generator")
        from code_mortis.main import app

        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")
        print(f"Wrote {destination}")


if __name__ == "__main__":
    main()

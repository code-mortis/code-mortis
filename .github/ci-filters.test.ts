import { expect, test } from "bun:test"

const filenames = ["ci.yml", "backend.yml", "image.yml", "native-clients.yml"]
const workflows = await Promise.all(filenames.map(async (name) =>
  Bun.YAML.parse(await Bun.file(new URL("./workflows/" + name, import.meta.url)).text()),
))
const allJobs = ["backend", "export-openapi", "frontend", "image", "native-clients"]
const required = (files: string[], event: "push" | "pull_request") => workflows
  .filter((workflow) => files.some((file) => workflow.on[event].paths.some((pattern: string) => new Bun.Glob(pattern).match(file))))
  .flatMap((workflow) => Object.keys(workflow.jobs)).sort()

test.each([
  ["documentation", ["README.md", "docs/operations.md"], []],
  ["GitHub templates", [".github/ISSUE_TEMPLATE/bug.md", ".github/pull_request_template.md"], []],
  ["frontend", ["frontend/src/reviews.tsx"], ["export-openapi", "frontend", "image"]],
  ["frontend dependencies", ["frontend/bun.lock"], ["export-openapi", "frontend", "image"]],
  ["backend and API code", ["backend/code_mortis/reviews.py"], allJobs],
  ["backend tests", ["backend/tests/test_review_flow.py"], ["backend", "image"]],
  ["backend dependencies", ["backend/pyproject.toml", "backend/uv.lock"], allJobs],
  ["migration", ["backend/migrations/versions/new.py"], ["backend", "image"]],
  ["database configuration", ["backend/alembic.ini"], ["backend", "image"]],
  ["test services", ["compose.test.yaml"], ["backend"]],
  ["Docker build", ["Dockerfile"], ["image"]],
  ["Docker context", [".dockerignore"], ["image", "native-clients"]],
  ["Docker-specific context", ["Dockerfile.dockerignore"], ["image"]],
  ["OCR patch", ["build/ocr/diagnostics.patch"], ["image"]],
  ["image notices", ["THIRD_PARTY_NOTICES.md", "LICENSE", "LICENSES/OCR.txt"], ["image"]],
  ["frontend workflow", [".github/workflows/ci.yml"], ["export-openapi", "frontend"]],
  ["backend workflow", [".github/workflows/backend.yml"], ["backend"]],
  ["image workflow", [".github/workflows/image.yml"], ["image"]],
  ["native workflow", [".github/workflows/native-clients.yml"], ["native-clients"]],
  ["filter tests", [".github/ci-filters.test.ts"], allJobs],
  ["runner image", ["Dockerfile.runner"], ["native-clients"]],
  ["runner-specific context", ["Dockerfile.runner.dockerignore"], ["native-clients"]],
  ["native smoke", ["backend/tests/native_runner_smoke.py"], ["backend", "image", "native-clients"]],
  ["rename out of backend", ["backend/removed.py", "docs/removed.py"], ["backend", "image"]],
  ["mixed changes", ["frontend/src/reviews.tsx", "compose.test.yaml"], ["backend", "export-openapi", "frontend", "image"]],
  ["no changes", [], []],
])("selects jobs for %s", (_, files, jobs) => {
  for (const event of ["push", "pull_request"] as const) {
    expect(required(files as string[], event)).toEqual(jobs)
  }
})

test("keeps exactly five jobs, scoped concurrency, and the OpenAPI dependency", () => {
  expect(workflows.flatMap((workflow) => Object.keys(workflow.jobs)).sort()).toEqual(allJobs)
  expect(new Set(workflows.map((workflow) => workflow.name)).size).toBe(workflows.length)
  for (const workflow of workflows) {
    expect(workflow.on.push.branches).toEqual(["main"])
    expect(workflow.on.pull_request).toEqual(workflow.on.push)
    expect(workflow.on).toHaveProperty("workflow_dispatch")
    expect(workflow.concurrency.group).toContain("github.workflow")
    for (const job of Object.values(workflow.jobs)) expect(job.strategy).toBeUndefined()
  }
  const frontend = workflows.find((workflow) => workflow.jobs.frontend)
  expect(frontend.jobs.frontend.needs).toBe("export-openapi")
  expect(frontend.jobs["export-openapi"]).toBeDefined()
  expect(frontend.jobs.frontend.if).toBeUndefined()
  const upload = frontend.jobs["export-openapi"].steps.find((step) => step.uses?.startsWith("actions/upload-artifact@"))
  const download = frontend.jobs.frontend.steps.find((step) => step.uses?.startsWith("actions/download-artifact@"))
  expect(upload.with.name).toBe(download.with.name)
  expect(frontend.jobs.frontend.steps.some((step) => step.run === "git diff --exit-code -- src/api.generated.ts")).toBe(true)
})

import { expect, test } from "bun:test"

const filenames = ["ci.yml", "backend.yml", "image.yml"]
const workflows = await Promise.all(filenames.map(async (name) =>
  Bun.YAML.parse(await Bun.file(new URL("./workflows/" + name, import.meta.url)).text()),
))
const allJobs = ["backend", "export-openapi", "frontend", "image"]
const required = (files: string[], event: "push" | "pull_request") => workflows
  .filter((workflow) => files.some((file) => workflow.on[event].paths.some((pattern: string) => new Bun.Glob(pattern).match(file))))
  .flatMap((workflow) => Object.keys(workflow.jobs).filter((job) => job !== "backend-tests")).sort()

test.each([
  ["documentation", ["README.md", "docs/operations.md"], []],
  ["GitHub templates", [".github/ISSUE_TEMPLATE/bug.md", ".github/pull_request_template.md"], []],
  ["frontend", ["frontend/src/reviews.tsx"], ["export-openapi", "frontend", "image"]],
  ["frontend dependencies", ["frontend/bun.lock"], ["export-openapi", "frontend", "image"]],
  ["review worker", ["backend/code_mortis/reviews/service.py"], ["backend", "image"]],
  ["job worker", ["backend/code_mortis/jobs.py"], ["backend", "image"]],
  ["assessment worker", ["backend/code_mortis/reviews/assessment.py"], ["backend", "image"]],
  ["background context", ["backend/code_mortis/reviews/background.py"], ["backend", "image"]],
  ["analytics implementation", ["backend/code_mortis/reviews/analytics.py"], ["backend", "image"]],
  ["security scanning", ["backend/code_mortis/security_scans/service.py"], ["backend", "image"]],
  ["API routes", ["backend/code_mortis/api/routers/pulls.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["new API router", ["backend/code_mortis/api/routers/new.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["API composition", ["backend/code_mortis/api/router.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["shared API permissions", ["backend/code_mortis/api/dependencies.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["shared API serializers", ["backend/code_mortis/api/presenters.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["GitHub OAuth helpers", ["backend/code_mortis/api/oauth.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["security routes", ["backend/code_mortis/api/routers/security.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["MCP authorization routes", ["backend/code_mortis/mcp/auth.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["MCP registration", ["backend/code_mortis/mcp/server.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["subscription routes", ["backend/code_mortis/api/routers/subscriptions.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["route registration", ["backend/code_mortis/main.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["OpenAPI command", ["backend/code_mortis/manage.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["analytics schema", ["backend/code_mortis/schemas/analytics.py"], ["backend", "export-openapi", "frontend", "image"]],
  ["shared schema", ["backend/code_mortis/schemas/common.py"], allJobs],
  ["shared security schema", ["backend/code_mortis/schemas/security.py"], allJobs],
  ["shared ordering types", ["backend/code_mortis/core/ordering.py"], allJobs],
  ["FastAPI database dependency", ["backend/code_mortis/core/db.py"], allJobs],
  ["package initialization", ["backend/code_mortis/__init__.py"], allJobs],
  ["core package initialization", ["backend/code_mortis/core/__init__.py"], allJobs],
  ["integration package initialization", ["backend/code_mortis/integrations/__init__.py"], allJobs],
  ["review package initialization", ["backend/code_mortis/reviews/__init__.py"], allJobs],
  ["scan package initialization", ["backend/code_mortis/security_scans/__init__.py"], allJobs],
  ["schema package initialization", ["backend/code_mortis/schemas/__init__.py"], allJobs],
  ["Mortis review", ["backend/code_mortis/reviews/mortis.py"], ["backend", "image"]],
  ["inference gateway", ["backend/code_mortis/integrations/llm.py"], ["backend", "image"]],
  ["inference contracts", ["backend/code_mortis/schemas/inference.py"], allJobs],
  ["security analysis", ["backend/code_mortis/security_scans/engine.py"], ["backend", "image"]],
  ["review diagnostics", ["backend/code_mortis/reviews/diagnostics.py"], ["backend", "image"]],
  ["backend tests", ["backend/tests/test_review_flow.py"], ["backend", "image"]],
  ["backend dependencies", ["backend/pyproject.toml", "backend/uv.lock"], allJobs],
  ["migration", ["backend/migrations/versions/new.py"], ["backend", "image"]],
  ["database configuration", ["backend/alembic.ini"], ["backend", "image"]],
  ["test services", ["docker/compose.test.yaml"], ["backend"]],
  ["API smoke services", ["docker/compose.api-smoke.yaml"], ["image"]],
  ["API smoke environment", ["collections/code-mortis-ci/.environments/ci.env"], ["image"]],
  ["API smoke request", ["collections/code-mortis-ci/ready.yml"], ["image"]],
  ["interactive Noodle collection", ["collections/code-mortis/public/ready.yml"], []],
  ["Docker build", ["docker/Dockerfile"], ["image"]],
  ["Docker context", [".dockerignore"], ["image"]],
  ["Docker-specific context", ["docker/Dockerfile.dockerignore"], ["image"]],
  ["image notices", ["THIRD_PARTY_NOTICES.md", "LICENSE", "LICENSES/Apache-2.0.txt"], ["image"]],
  ["frontend workflow", [".github/workflows/ci.yml"], ["export-openapi", "frontend"]],
  ["backend workflow", [".github/workflows/backend.yml"], ["backend"]],
  ["image workflow", [".github/workflows/image.yml"], ["image"]],
  ["filter tests", [".github/ci-filters.test.ts"], allJobs],
  ["Mortis image smoke", ["backend/tests/mortis_container_smoke.py"], ["backend", "image"]],
  ["rename out of backend", ["backend/removed.py", "docs/removed.py"], ["backend", "image"]],
  ["mixed changes", ["frontend/src/reviews.tsx", "docker/compose.test.yaml"], ["backend", "export-openapi", "frontend", "image"]],
  ["no changes", [], []],
])("selects jobs for %s", (_, files, jobs) => {
  for (const event of ["push", "pull_request"] as const) {
    expect(required(files as string[], event)).toEqual(jobs)
  }
})

test("keeps required checks, scoped concurrency, and the OpenAPI dependency", () => {
  expect(workflows.flatMap((workflow) => Object.keys(workflow.jobs)).sort()).toEqual([...allJobs, "backend-tests"].sort())
  expect(new Set(workflows.map((workflow) => workflow.name)).size).toBe(workflows.length)
  for (const workflow of workflows) {
    expect(workflow.on.push.branches).toEqual(["main"])
    expect(workflow.on.pull_request).toEqual(workflow.on.push)
    expect(workflow.on).toHaveProperty("workflow_dispatch")
    expect(workflow.concurrency.group).toContain("github.workflow")
    for (const [name, job] of Object.entries(workflow.jobs)) {
      if (name !== "backend-tests") expect(job.strategy).toBeUndefined()
    }
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

test("requires every isolated backend shard to pass", () => {
  const { jobs } = workflows.find((workflow) => workflow.jobs["backend-tests"])
  const shards = jobs["backend-tests"]
  expect(shards.strategy).toEqual({ "fail-fast": false, matrix: { shard: [1, 2, 3, 4] } })
  expect(shards.env).toEqual({
    PYTEST_SHARD_INDEX: "${{ strategy.job-index }}",
    PYTEST_SHARD_COUNT: "${{ strategy.job-total }}",
  })
  expect(shards.services.postgres).toBeDefined()
  expect(shards.services.redis).toBeDefined()
  expect(jobs.backend.needs).toBe("backend-tests")
  expect(jobs.backend.if).toBe("always()")
  expect(jobs.backend.steps[0].env.RESULT).toBe("${{ needs.backend-tests.result }}")
  expect(jobs.backend.steps[0].run).toBe('test "$RESULT" = success')
})

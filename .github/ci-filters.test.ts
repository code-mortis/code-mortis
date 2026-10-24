import { expect, test } from "bun:test"

const workflow = Bun.YAML.parse(await Bun.file(new URL("./workflows/ci.yml", import.meta.url)).text())
const changes = workflow.jobs["export-openapi"].steps.find((step) => step.id === "changes")
const filters = Bun.YAML.parse(changes.with.filters)
const required = (files: string[]) => Object.fromEntries(
  Object.entries(filters).map(([job, patterns]) => [
    job, files.some((file) => (patterns as string[]).some((pattern) => new Bun.Glob(pattern).match(file))),
  ]),
)

test.each([
  ["documentation", ["README.md", "docs/operations.md"], false, false],
  ["GitHub templates", [".github/ISSUE_TEMPLATE/bug.md", ".github/pull_request_template.md"], false, false],
  ["frontend", ["frontend/src/reviews.tsx"], false, true],
  ["frontend dependencies", ["frontend/bun.lock"], false, true],
  ["backend", ["backend/code_mortis/reviews.py"], true, true],
  ["backend tests", ["backend/tests/test_review_flow.py"], true, true],
  ["backend dependencies", ["backend/pyproject.toml", "backend/uv.lock"], true, true],
  ["migration", ["backend/migrations/versions/new.py"], true, true],
  ["database configuration", ["backend/alembic.ini"], true, true],
  ["test services", ["compose.test.yaml"], true, false],
  ["Docker build", ["Dockerfile"], false, true],
  ["Docker context", [".dockerignore"], false, true],
  ["Docker-specific context", ["Dockerfile.dockerignore"], false, true],
  ["OCR patch", ["build/ocr/diagnostics.patch"], false, true],
  ["image notices", ["THIRD_PARTY_NOTICES.md", "LICENSE", "LICENSES/OCR.txt"], false, true],
  ["workflow", [".github/workflows/ci.yml"], true, true],
  ["filter tests", [".github/ci-filters.test.ts"], true, true],
  ["runner image", ["Dockerfile.runner"], false, false],
  ["rename out of backend", ["backend/removed.py", "docs/removed.py"], true, true],
  ["no changes", [], false, false],
])("selects jobs for %s", (_, files, backend, image) => {
  expect(required(files as string[])).toEqual({ backend, image })
})

test("keeps five single jobs and runs checks unless filtering explicitly excludes them", () => {
  expect(Object.keys(workflow.jobs).sort()).toEqual(["backend", "export-openapi", "frontend", "image", "native-clients"])
  expect(changes.if).toBe("github.event_name != 'workflow_dispatch'")
  expect(changes.with.token).toBe("")
  expect(changes.with.base).toBe("${{ github.event_name == 'push' && github.ref || '' }}")
  for (const job of ["backend", "image"]) {
    expect(workflow.jobs[job].strategy).toBeUndefined()
    expect(workflow.jobs[job].needs).toBe("export-openapi")
    expect(workflow.jobs[job].if).toBe("${{ !cancelled() && needs.export-openapi.outputs." + job + " != 'false' }}")
  }
})

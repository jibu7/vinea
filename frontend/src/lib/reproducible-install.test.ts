import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The frontend's dependencies are installed from the lockfile, everywhere they are installed.
 *
 * `npm install` is allowed to *change* resolution: when it decides `package-lock.json` and
 * `package.json` disagree it rewrites the lockfile where it is running and installs what it
 * just resolved. In a Docker layer that rewrite is invisible — it lands in the image and
 * nowhere else — so the container could run a dependency tree that is neither the one
 * committed nor the one CI tested, with a green build and no signal at all. The only way to
 * learn about it is a bug that reproduces in the container and nowhere else, which is the
 * most expensive way there is.
 *
 * `npm ci` is the reproducible form: exactly the lockfile, and a loud failure when the
 * lockfile is missing or out of step with `package.json`.
 *
 * This is a convention until something checks it, and one `npm install` added back in a new
 * job is enough to lose it — so the check is here rather than in a review note. It is string
 * work over `frontend/Dockerfile` and `.github/workflows/*.yml`: no npm, no registry, no
 * container. It deliberately does **not** read `README.md` or `AGENTS.md`, where `npm install`
 * is the right instruction for a developer adding a dependency.
 */
const FRONTEND = process.cwd();
const DOCKERFILE = join(FRONTEND, "Dockerfile");
const WORKFLOW_DIR = join(FRONTEND, "..", ".github", "workflows");

/** Every npm subcommand that resolves dependencies. `ci` is in the list on purpose: the
 * assertions below match all of them and then ask which ones turned up, so a command that
 * installs nothing the lockfile names cannot slip through as "no match". */
const NPM_INSTALLER = /\bnpm\s+(ci|install|i|add)\b/g;

/** Shell comments are not commands. Both files below explain `npm install` in prose, and a
 * scanner that could not tell the explanation from the command would fail on its own
 * documentation. */
function withoutComments(shell: string): string {
  return shell
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("#"))
    .join("\n");
}

/** The shell of every `run:` step in a workflow, inline (`run: npm ci`) and block-scalar
 * (`run: |`) forms alike.
 *
 * The block form is not optional to support: the e2e job's longest steps are written that
 * way, and a reader that returned `"|"` for them would report green over whatever they do.
 * A block scalar's body is every following line indented past the `run:` key, which is what
 * the inner loop walks. */
function runSteps(workflow: string): string[] {
  const lines = workflow.split("\n");
  const steps: string[] = [];
  for (let i = 0; i < lines.length; i += 1) {
    const keyColumn = lines[i].search(/(?<=^[\s-]*)run:/);
    if (keyColumn < 0) continue;
    const value = lines[i].slice(keyColumn + "run:".length).trim();
    if (!/^[>|][-+]?\d*$/.test(value)) {
      steps.push(value);
      continue;
    }
    const body: string[] = [];
    for (let j = i + 1; j < lines.length; j += 1) {
      if (lines[j].trim() === "") {
        body.push("");
        continue;
      }
      if (lines[j].length - lines[j].trimStart().length <= keyColumn) break;
      body.push(lines[j]);
    }
    steps.push(body.join("\n"));
  }
  return steps;
}

function dockerfile(): string {
  return readFileSync(DOCKERFILE, "utf8");
}

function workflows(): { name: string; source: string }[] {
  return readdirSync(WORKFLOW_DIR)
    .filter((name) => name.endsWith(".yml") || name.endsWith(".yaml"))
    .map((name) => ({ name, source: readFileSync(join(WORKFLOW_DIR, name), "utf8") }));
}

/** The installer subcommands a piece of shell invokes, e.g. `["ci"]`. */
function installers(shell: string): string[] {
  return [...withoutComments(shell).matchAll(NPM_INSTALLER)].map(([, subcommand]) => subcommand);
}

describe("the frontend image and CI install the lockfile, not a fresh resolution", () => {
  it("finds the files it claims to check", () => {
    // Anti-vacuity: a renamed Dockerfile or a moved workflow directory must fail here rather
    // than quietly check nothing. Both assertions below are "no bad matches", which is what
    // an empty read looks like too.
    expect(dockerfile()).toContain("FROM node:");
    expect(workflows().map((file) => file.name)).toContain("ci.yml");
    expect(workflows().flatMap((file) => runSteps(file.source)).length).toBeGreaterThan(10);
  });

  it("builds the frontend image with npm ci", () => {
    const commands = dockerfile()
      .split("\n")
      .filter((line) => /^RUN\s/.test(line));
    expect(commands, "no RUN line in frontend/Dockerfile").not.toEqual([]);
    const used = commands.flatMap(installers);
    expect(used, "frontend/Dockerfile installs nothing").not.toEqual([]);
    expect(used.filter((subcommand) => subcommand !== "ci")).toEqual([]);
  });

  it("copies the lockfile by name, so a missing one fails the build", () => {
    // `COPY package-lock.json* ./` is the other half of the hole: the glob makes a missing
    // lockfile a no-op, and `npm ci` would then be reproducing nothing. Named exactly, the
    // build stops at the COPY instead.
    const copies = dockerfile()
      .split("\n")
      .filter((line) => /^COPY\b/.test(line) && line.includes("package-lock.json"));
    expect(copies, "frontend/Dockerfile never copies the lockfile").toHaveLength(1);
    expect(copies[0], "the lockfile is copied through a glob").not.toMatch(/package-lock\.json\*/);
  });

  it("installs the lockfile in every workflow step that installs at all", () => {
    // The e2e job is the one that matters most: two trees meet there, the frontend image's
    // and the runner's, and the runner's is where Playwright comes from. Its browser cache is
    // keyed on the lockfile's hash, so an install free to resolve a different
    // `@playwright/test` turns a cache hit into a test failure rather than an install failure.
    const offenders = workflows().flatMap((file) =>
      runSteps(file.source)
        .filter((shell) => installers(shell).some((subcommand) => subcommand !== "ci"))
        .map((shell) => `${file.name}: ${shell.trim()}`),
    );
    expect(offenders, "workflow steps that resolve instead of installing the lockfile").toEqual([]);
  });

  it("recognises the forms it is meant to reject", () => {
    // Anti-vacuity for the scanner itself. The first three are the ways this comes back; the
    // last three must not be flagged — `npx playwright install` downloads a browser, `npm run`
    // is not an install, and prose about `npm install` is documentation, not a command.
    expect(installers("npm install --no-audit --no-fund")).toEqual(["install"]);
    expect(installers("cd frontend && npm i")).toEqual(["i"]);
    expect(installers("npm add tsx")).toEqual(["add"]);
    expect(installers("npx playwright install --with-deps chromium")).toEqual([]);
    expect(installers("npm run build")).toEqual([]);
    expect(installers("# npm install is allowed to change resolution")).toEqual([]);
  });

  it("reads a block-scalar run: step rather than its indicator", () => {
    // The failure `ci-e2e-groups.test.ts` was written over, one file along: a YAML reader that
    // meets `|` and returns `"|"` reports green over every multi-line step in the file.
    const workflow = [
      "jobs:",
      "  build:",
      "    steps:",
      "      - run: |",
      "          npm install --no-audit --no-fund",
      "          npm run build",
      "        working-directory: frontend",
      "      - run: npm ci",
    ].join("\n");
    const steps = runSteps(workflow);
    expect(steps).toHaveLength(2);
    expect(installers(steps[0])).toEqual(["install"]);
    expect(installers(steps[1])).toEqual(["ci"]);
  });
});

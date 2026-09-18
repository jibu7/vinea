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
 * learn about it is a bug that reproduces in the container and nowhere else, which is the most
 * expensive way there is.
 *
 * `npm ci` is the reproducible form: exactly the lockfile, and a loud failure when the
 * lockfile is missing or out of step with `package.json`.
 *
 * This is a convention until something checks it, and one `npm install` added back in a new
 * job is enough to lose it — so the check is here rather than in a review note. It is string
 * work over the four places that install: no npm, no registry, no container. It deliberately
 * does **not** read `README.md` or `AGENTS.md`, where `npm install` is the right instruction
 * for a developer adding a dependency.
 *
 * Every reader below **fails on a form it cannot parse** rather than returning nothing, which
 * is the lesson `ci-e2e-groups.test.ts` records one file along: a checker that silently cannot
 * see one of the things it checks is worse than no checker, because it also reports green.
 */
const FRONTEND = process.cwd();
const REPO = join(FRONTEND, "..");
const DOCKERFILE = join(FRONTEND, "Dockerfile");
const WORKFLOW_DIR = join(REPO, ".github", "workflows");
const ACTION_DIR = join(REPO, ".github", "actions");
const MAKEFILE = join(REPO, "Makefile");

/** npm's install subcommands, aliases and all — the alias list is npm's own, typos included
 * (`isntall` is a real alias). Anything here resolves dependencies. */
const INSTALLS = new Set([
  "ci",
  "clean-install",
  "ic",
  "install-clean",
  "isntall-clean",
  "install",
  "i",
  "in",
  "ins",
  "inst",
  "insta",
  "instal",
  "isnt",
  "isnta",
  "isntal",
  "isntall",
  "add",
  "install-test",
  "it",
]);

/** The ones `ci` is the right answer for. */
const REPRODUCIBLE = new Set(["ci", "clean-install", "ic", "install-clean", "isntall-clean"]);

/** npm subcommands this repo uses that install nothing. Deliberately short: an npm invocation
 * whose subcommand is in neither set fails the parse rather than passing quietly, so the cost
 * of a subcommand nobody listed is one line added here, not a silent hole. */
const NOT_INSTALLS = new Set(["run", "run-script", "exec", "x", "test", "start", "version"]);

/** Shell comments are not commands. Several of these files explain `npm install` in prose, and
 * a scanner that could not tell the explanation from the command would fail on its own
 * documentation. */
function withoutComments(shell: string): string {
  return shell
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("#"))
    .join("\n");
}

/** The npm subcommand each npm invocation in a piece of shell runs, e.g. `["ci"]`.
 *
 * A token walk and not `/npm\s+(ci|install)\b/`, because npm takes its flags **before** the
 * subcommand as happily as after: `npm --prefix frontend install` is an install, and a regex
 * reading the word right after `npm` sees `--prefix` and reports nothing at all. That form is
 * not hypothetical here — `copilot-setup-steps.yml` has no `working-directory`, so it is one
 * edit away from being the natural thing to write.
 *
 * Scanning forward for the first token in either table skips flags and their values without
 * needing npm's flag list. A token in neither table is an invocation this reader does not
 * understand, and it throws. */
function installers(shell: string): string[] {
  const found: string[] = [];
  for (const segment of withoutComments(shell).split(/&&|\|\||[;|\n]/)) {
    const tokens = segment.split(/\s+/).filter(Boolean);
    const npm = tokens.findIndex((token) => token === "npm" || token.endsWith("/npm"));
    if (npm < 0) continue;
    const subcommand = tokens
      .slice(npm + 1)
      .find((token) => INSTALLS.has(token) || NOT_INSTALLS.has(token));
    if (subcommand === undefined) {
      throw new Error(
        `npm invocation this test cannot classify — add its subcommand to INSTALLS or ` +
          `NOT_INSTALLS rather than leaving it unread: ${segment.trim()}`,
      );
    }
    if (INSTALLS.has(subcommand)) found.push(subcommand);
  }
  return found;
}

/** The shell of every `run:` step in a workflow or composite action, inline (`run: npm ci`)
 * and block-scalar (`run: |`) forms alike.
 *
 * The block form is not optional to support: the e2e job's longest steps are written that way,
 * and a reader returning `"|"` for them would report green over whatever they do. A block
 * scalar's body is every following line indented past the `run:` key, which is what the inner
 * loop walks. */
function runSteps(yaml: string): string[] {
  const lines = yaml.split("\n");
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

/** Workflows **and** composite actions. A `uses:` step runs someone's `run:` steps, so a
 * scan that stopped at `.github/workflows` would be one directory away from missing an
 * install entirely. */
function yamlSites(): { name: string; source: string }[] {
  const isYaml = (name: string) => name.endsWith(".yml") || name.endsWith(".yaml");
  const workflows = readdirSync(WORKFLOW_DIR)
    .filter(isYaml)
    .map((name) => ({ name: `workflows/${name}`, source: readFileSync(join(WORKFLOW_DIR, name), "utf8") }));
  const actions = readdirSync(ACTION_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) =>
      readdirSync(join(ACTION_DIR, entry.name))
        .filter(isYaml)
        .map((name) => ({
          name: `actions/${entry.name}/${name}`,
          source: readFileSync(join(ACTION_DIR, entry.name, name), "utf8"),
        })),
    );
  return [...workflows, ...actions];
}

/** Every command the repo runs anywhere, as `label -> shell`. The Makefile is read whole and
 * split by the invocation walker: `test_property_markers.py` reads `ci.yml` and the `Makefile`
 * together for the same reason — a rule broken in either has the same effect, and only one of
 * them is CI. */
function installSites(): { label: string; shell: string }[] {
  const docker = dockerfile()
    .split("\n")
    .filter((line) => /^RUN\s/.test(line))
    .map((line) => ({ label: "frontend/Dockerfile", shell: line.replace(/^RUN\s+/, "") }));
  const yaml = yamlSites().flatMap((file) =>
    runSteps(file.source).map((shell) => ({ label: file.name, shell })),
  );
  return [...docker, ...yaml, { label: "Makefile", shell: readFileSync(MAKEFILE, "utf8") }];
}

describe("the frontend image and CI install the lockfile, not a fresh resolution", () => {
  it("finds the files it claims to check", () => {
    // Anti-vacuity: a renamed Dockerfile or a moved directory must fail here, not check
    // nothing. The assertions below are all "no bad matches", which is what an empty read
    // looks like too.
    expect(dockerfile()).toContain("FROM node:");
    expect(yamlSites().map((file) => file.name)).toContain("workflows/ci.yml");
    expect(yamlSites().map((file) => file.name)).toContain(
      "actions/drop-vendor-apt-sources/action.yml",
    );
    expect(installSites().length).toBeGreaterThan(15);
    // And it must actually be reading installs, not just lines.
    expect(installSites().flatMap((site) => installers(site.shell)).length).toBeGreaterThanOrEqual(
      4,
    );
  });

  it("installs the lockfile everywhere it installs at all", () => {
    // The e2e job is the one that matters most: two dependency trees meet there, the frontend
    // image's and the runner's, and the runner's is where Playwright comes from. Its browser
    // cache is keyed on the lockfile's hash, so an install free to resolve a different
    // `@playwright/test` turns a cache hit into a test failure, not an install failure.
    const offenders = installSites().flatMap((site) =>
      installers(site.shell)
        .filter((subcommand) => !REPRODUCIBLE.has(subcommand))
        .map(() => `${site.label}: ${site.shell.trim()}`),
    );
    expect(offenders, "installs that resolve instead of reading the lockfile").toEqual([]);
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

  it("recognises the forms it is meant to reject", () => {
    // Anti-vacuity for the scanner. The flags-first forms are the ones a regex on the word
    // after `npm` cannot see, and they are why this walks tokens.
    expect(installers("npm install --no-audit --no-fund")).toEqual(["install"]);
    expect(installers("npm --prefix frontend install")).toEqual(["install"]);
    expect(installers("npm --prefix=frontend i")).toEqual(["i"]);
    expect(installers("cd frontend && npm add tsx")).toEqual(["add"]);
    expect(installers("npm ci --no-audit && npm run build")).toEqual(["ci"]);
  });

  it("does not mistake a neighbouring command for an install", () => {
    // `npx playwright install` downloads a browser and `npm run build` is not an install;
    // flagging either would make this test something people route around.
    expect(installers("npx playwright install --with-deps chromium")).toEqual([]);
    expect(installers("npm run build")).toEqual([]);
    expect(installers("# npm install is allowed to change resolution")).toEqual([]);
    expect(installers("sudo apt-get install -y --no-install-recommends poppler-utils")).toEqual([]);
  });

  it("refuses to classify an npm invocation it does not understand", () => {
    // The failure mode this file exists to avoid, turned into a rule: an unreadable command
    // stops the suite instead of counting as "no install found".
    expect(() => installers("npm frobnicate --hard")).toThrow(/cannot classify/);
  });

  it("reads a block-scalar run: step rather than its indicator", () => {
    // The bug `ci-e2e-groups.test.ts` was written over, one file along: a YAML reader that
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

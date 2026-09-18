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
 * work over the six files that install or run: no npm, no registry, no container. It
 * deliberately does **not** read `README.md`, where `npm install` is the right instruction for
 * a developer adding a dependency.
 *
 * Every reader below **fails on a form it cannot parse** rather than returning nothing, which
 * is the lesson `ci-e2e-groups.test.ts` records one file along: a checker that silently cannot
 * see one of the things it checks is worse than no checker, because it also reports green.
 * Three blind spots were found by re-reading this file after it shipped and two more in
 * review, every one of them a reader returning `[]` for a real install.
 *
 * ## Two decisions this file makes, so that nobody has to make them under pressure
 *
 * **`npm install -g <tool>` counts.** A global install is exactly as unpinned as a local one —
 * no lockfile, whatever resolves on the day — so it fails here like any other. The day one is
 * genuinely needed, the answer is an exemption **with a reason string**, in the rule-14 shape
 * that `test_api_has_a_caller.py` uses, so that every addition costs a line of review. It is
 * not a weaker walker: an allow list names what was decided, a loosened regex hides it.
 *
 * **The boundary is these six files, not the commands they invoke.** `$(NPM) install` behind a
 * Make variable, `run: bash scripts/setup.sh`, a `command:` that execs a script — all read as
 * nothing here, and that is the honest limit of a string scanner. Anything that installs
 * should install in one of these files, where it can be seen.
 */
const FRONTEND = process.cwd();
const REPO = join(FRONTEND, "..");
const DOCKERFILE = join(FRONTEND, "Dockerfile");
const WORKFLOW_DIR = join(REPO, ".github", "workflows");
const ACTION_DIR = join(REPO, ".github", "actions");
const MAKEFILE = join(REPO, "Makefile");
const COMPOSE = ["docker-compose.yml", "docker-compose.e2e.yml"];

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

/** npm subcommands this repo uses that touch neither `package-lock.json` nor `node_modules`.
 * Deliberately short: an npm invocation whose subcommand is in neither table fails the parse
 * rather than passing quietly, so the cost of a subcommand nobody listed is one line added
 * here, under the rule the throw states — not a silent hole. */
const NOT_INSTALLS = new Set(["run", "run-script", "exec", "x", "test", "start", "version"]);

/** Comments are not commands. Several of these files explain `npm install` in prose, and a
 * scanner that could not tell the explanation from the command would fail on its own
 * documentation. */
function withoutComments(shell: string): string {
  return shell
    .split("\n")
    .filter((line) => !line.trimStart().startsWith("#"))
    .join("\n");
}

/** Fold `\`-continued lines into the command they belong to.
 *
 * Without this the Dockerfile reader has the defect the YAML reader was fixed for: filtering
 * `/^RUN\s/` line by line reads `RUN apk add git \` and drops the ` && npm install` on the
 * next line entirely. Silent, and one flag away from being the natural edit. */
function joinContinuations(text: string): string[] {
  const joined: string[] = [];
  let pending = "";
  for (const raw of text.split("\n")) {
    const line = pending ? `${pending} ${raw.trim()}` : raw;
    if (/\\\s*$/.test(line)) {
      pending = line.replace(/\\\s*$/, "").trimEnd();
      continue;
    }
    pending = "";
    joined.push(line);
  }
  if (pending) joined.push(pending);
  return joined;
}

/** The npm subcommand each npm invocation in a piece of shell runs, e.g. `["ci"]`.
 *
 * A token walk and not `/npm\s+(ci|install)\b/`, because npm takes its flags **before** the
 * subcommand as happily as after: `npm --prefix frontend install` is an install, and a regex
 * reading the word right after `npm` sees `--prefix` and reports nothing at all.
 *
 * Quotes are stripped per token because a compose `command:` wraps its shell —
 * `sh -c "npm install && npm run dev"` tokenises to `"npm`, which is not `npm`. That is the
 * exact line a stale `node_modules` volume tempts somebody to write. */
function installers(shell: string): string[] {
  const found: string[] = [];
  for (const segment of withoutComments(shell).split(/&&|\|\||[;|\n]/)) {
    const tokens = segment
      .split(/\s+/)
      .map((token) => token.replace(/^['"]+|['"]+$/g, ""))
      .filter(Boolean);
    const npm = tokens.findIndex((token) => token === "npm" || token.endsWith("/npm"));
    if (npm < 0) continue;
    const subcommand = tokens
      .slice(npm + 1)
      .find((token) => INSTALLS.has(token) || NOT_INSTALLS.has(token));
    if (subcommand === undefined) {
      throw new Error(
        `npm invocation this test cannot classify: ${segment.trim()}\n` +
          `The rule is what it can WRITE, not what it is called: if it can touch ` +
          `package-lock.json or node_modules it belongs in INSTALLS. \`npm update\`, ` +
          `\`dedupe\`, \`prune\`, \`uninstall\` and \`link\` all can, and all read like they ` +
          `do not — filing one of those under NOT_INSTALLS reopens this hole through the ` +
          `mechanism built to close it.`,
      );
    }
    if (INSTALLS.has(subcommand)) found.push(subcommand);
  }
  return found;
}

/** A flow-mapping scalar: everything up to the first unquoted `,` or `}`. */
function flowScalar(rest: string): string {
  let quote = "";
  let out = "";
  for (const character of rest) {
    if (quote) {
      if (character === quote) quote = "";
      else out += character;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === "," || character === "}") break;
    out += character;
  }
  return out.trim();
}

/** The shell of every `key:` in a YAML file — `run:` in a workflow, `command:` in compose.
 *
 * Four forms, because all four are written in this repo and a reader that knows one of them
 * reports green over the rest:
 *
 * - inline scalar — `run: npm ci`
 * - block scalar — `run: |`, whose body is every following line indented past the key. The e2e
 *   job's longest steps are written this way; a reader returning `"|"` for them sees nothing.
 * - flow mapping — `- { run: npm ci, shell: bash }`. This repo writes flow mappings constantly
 *   (`with: { node-version: 22 }`), so the form is a normal edit away. A `run:` whose value is
 *   itself a mapping is `defaults: { run: { working-directory: … } }`: configuration, not a
 *   command, and skipped.
 * - sequence — `command: ["npm", "run", "dev"]` and its block form, joined back into shell.
 *
 * Anything else throws.
 */
function shellValues(yaml: string, key: string): string[] {
  const lines = yaml.split("\n");
  const blockKey = new RegExp(`(?<=^[\\s-]*)${key}:`);
  const flowKey = new RegExp(`[{,]\\s*${key}:\\s*`, "g");
  const values: string[] = [];

  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i].trimStart().startsWith("#")) continue;

    const keyColumn = lines[i].search(blockKey);
    if (keyColumn < 0) {
      for (let match = flowKey.exec(lines[i]); match; match = flowKey.exec(lines[i])) {
        const rest = lines[i].slice(match.index + match[0].length);
        if (rest.startsWith("{")) continue; // a mapping, e.g. `defaults: { run: { … } }`
        values.push(rest.startsWith("[") ? sequence(rest) : flowScalar(rest));
      }
      continue;
    }

    const value = lines[i].slice(keyColumn + key.length + 1).trim();
    if (value.startsWith("{")) continue;
    if (value.startsWith("[")) {
      values.push(sequence(value));
      continue;
    }
    if (value !== "" && !/^[>|][-+]?\d*$/.test(value)) {
      values.push(value.replace(/^['"]|['"]$/g, ""));
      continue;
    }

    // A block scalar, or a block sequence: either way the body is the indented run below.
    const body: string[] = [];
    for (let j = i + 1; j < lines.length; j += 1) {
      if (lines[j].trim() === "") {
        body.push("");
        continue;
      }
      if (lines[j].length - lines[j].trimStart().length <= keyColumn) break;
      body.push(lines[j]);
    }
    if (value === "") {
      const items = body.filter((line) => line.trim() !== "");
      if (items.length === 0 || !items.every((line) => line.trimStart().startsWith("- "))) {
        throw new Error(
          `\`${key}:\` here is a form this test cannot read — it is neither a scalar, a block ` +
            `scalar, nor a sequence. Teach the reader the form rather than leaving it unread: ` +
            `${lines[i].trim()}`,
        );
      }
      values.push(items.map((line) => line.trim().slice(2).replace(/^['"]|['"]$/g, "")).join(" "));
      continue;
    }
    values.push(body.join("\n"));
  }
  return values;
}

/** `["npm", "run", "dev"]` back into `npm run dev`. */
function sequence(flow: string): string {
  const inner = flow.slice(1, flow.lastIndexOf("]") < 0 ? undefined : flow.lastIndexOf("]"));
  return inner
    .split(",")
    .map((item) => item.trim().replace(/^['"]|['"]$/g, ""))
    .filter(Boolean)
    .join(" ");
}

function dockerfile(): string {
  return readFileSync(DOCKERFILE, "utf8");
}

/** Dockerfile instructions, continuations already folded in. */
function dockerInstructions(instruction: string): string[] {
  return joinContinuations(withoutComments(dockerfile()))
    .filter((line) => new RegExp(`^${instruction}\\s`).test(line))
    .map((line) => line.replace(new RegExp(`^${instruction}\\s+`), ""));
}

/** Workflows **and** composite actions. A `uses:` step runs someone else's `run:` steps, so a
 * scan that stopped at `.github/workflows` would be one directory away from missing an install
 * entirely. */
function ciSites(): { name: string; source: string }[] {
  const isYaml = (name: string) => name.endsWith(".yml") || name.endsWith(".yaml");
  const workflows = readdirSync(WORKFLOW_DIR)
    .filter(isYaml)
    .map((name) => ({
      name: `workflows/${name}`,
      source: readFileSync(join(WORKFLOW_DIR, name), "utf8"),
    }));
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

/** Every command this repo runs from a file, as `label -> shell`.
 *
 * Six files. The Makefile is read whole and split by the invocation walker:
 * `test_property_markers.py` reads `ci.yml` and the `Makefile` together for the same reason —
 * a rule broken in either has the same effect, and only one of them is CI. Compose is here
 * because the frontend service bind-mounts `./frontend:/app` over an anonymous `node_modules`
 * volume, which is exactly the arrangement where a stale tree gets "fixed" with
 * `command: sh -c "npm install && npm run dev"` — and the e2e stack runs from these two files.
 */
function installSites(): { label: string; shell: string }[] {
  const docker = dockerInstructions("RUN").map((shell) => ({ label: "frontend/Dockerfile", shell }));
  const ci = ciSites().flatMap((file) =>
    shellValues(file.source, "run").map((shell) => ({ label: file.name, shell })),
  );
  const compose = COMPOSE.flatMap((name) => {
    const source = readFileSync(join(REPO, name), "utf8");
    return ["command", "entrypoint"].flatMap((key) =>
      shellValues(source, key).map((shell) => ({ label: `${name} (${key})`, shell })),
    );
  });
  return [
    ...docker,
    ...ci,
    ...compose,
    { label: "Makefile", shell: readFileSync(MAKEFILE, "utf8") },
  ];
}

describe("the frontend image and CI install the lockfile, not a fresh resolution", () => {
  it("finds the files it claims to check", () => {
    // Anti-vacuity: a renamed Dockerfile or a moved directory must fail here, not check
    // nothing. The assertions below are all "no bad matches", which is what an empty read
    // looks like too.
    expect(dockerfile()).toContain("FROM node:");
    const labels = installSites().map((site) => site.label);
    expect(labels).toContain("workflows/ci.yml");
    expect(labels).toContain("actions/drop-vendor-apt-sources/action.yml");
    expect(labels).toContain("docker-compose.yml (command)");
    expect(labels).toContain("docker-compose.e2e.yml (command)");
    expect(labels).toContain("Makefile");
    expect(installSites().length).toBeGreaterThan(20);
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
    const copies = dockerInstructions("COPY").filter((line) => line.includes("package-lock.json"));
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
    // A quoted compose command, which is how this would arrive in docker-compose.yml.
    expect(installers('sh -c "npm install && npm run dev"')).toEqual(["install"]);
    // The `-g` decision, pinned: a global install is as unpinned as a local one.
    expect(installers("npm install -g tsx")).toEqual(["install"]);
  });

  it("does not mistake a neighbouring command for an install", () => {
    // `npx playwright install` downloads a browser and `npm run build` is not an install;
    // flagging either would make this test something people route around.
    expect(installers("npx playwright install --with-deps chromium")).toEqual([]);
    expect(installers("npm run build")).toEqual([]);
    expect(installers("# npm install is allowed to change resolution")).toEqual([]);
    expect(installers("sudo apt-get install -y --no-install-recommends poppler-utils")).toEqual([]);
    expect(installers("uv run uvicorn app.main:app --host 0.0.0.0")).toEqual([]);
  });

  it("refuses to classify an npm invocation it does not understand", () => {
    // The failure mode this file exists to avoid, turned into a rule: an unreadable command
    // stops the suite instead of counting as "no install found". The message has to send the
    // next reader the right way — `npm update` is not an install by name and rewrites the
    // lockfile by behaviour, so the rule stated is what it can write.
    expect(() => installers("npm frobnicate --hard")).toThrow(/cannot classify/);
    expect(() => installers("npm update")).toThrow(/what it can WRITE/);
  });

  it("reads a RUN whose command is continued across lines", () => {
    // Blind spot 4, and the same class as the three before it: `/^RUN\s/` line by line stops
    // at the backslash and never sees the install on the next line.
    const joined = joinContinuations("RUN apk add --no-cache git \\\n && npm install --no-audit");
    expect(joined).toEqual(["RUN apk add --no-cache git && npm install --no-audit"]);
    expect(installers(joined[0])).toEqual(["install"]);
  });

  it("reads every YAML form of a command, not just the one it met first", () => {
    // Block scalar — the bug `ci-e2e-groups.test.ts` was written over, one file along: a
    // reader that meets `|` and returns `"|"` reports green over every multi-line step.
    const block = [
      "      - run: |",
      "          npm install --no-audit",
      "          npm run build",
      "        working-directory: frontend",
      "      - run: npm ci",
    ].join("\n");
    expect(shellValues(block, "run")).toHaveLength(2);
    expect(shellValues(block, "run").flatMap(installers)).toEqual(["install", "ci"]);

    // Flow mapping — blind spot 5's neighbour, and this repo writes them constantly.
    expect(installers(shellValues("      - { run: npm install }", "run")[0])).toEqual(["install"]);
    expect(shellValues("      - { run: npm ci, shell: bash }", "run")).toEqual(["npm ci"]);

    // …but a `run:` whose value is a mapping is configuration, not a command.
    expect(shellValues("    defaults: { run: { working-directory: backend } }", "run")).toEqual([]);

    // Sequence, flow and block, which is how compose writes a command as argv.
    expect(shellValues('    command: ["npm", "install"]', "command")).toEqual(["npm install"]);
    expect(shellValues("    command:\n      - npm\n      - install", "command")).toEqual([
      "npm install",
    ]);
  });

  it("refuses a YAML command form it cannot read", () => {
    // Same rule as the npm walker, one level up: an unreadable value stops the suite rather
    // than counting as no command at all.
    expect(() => shellValues("    command:\n      key: value", "command")).toThrow(/cannot read/);
  });
});

#!/usr/bin/env node
/**
 * Remove the files that should not be in the release ZIP from the plugin's Code folder (the folder that
 * holds plugin.json, found by ./manifest.mjs): the patterns in scripts/release-exclude.txt, plus the
 * leftovers plugin_tools always leaves out (__pycache__/, venv/, .git/, *.pyc, *.log).
 *
 * Usage:
 *   node scripts/prune-release.mjs --list     show what would be removed - changes nothing
 *   node scripts/prune-release.mjs --delete   remove it. The release workflow does this on a copy of the
 *                                             Code folder (--dir), never on the checkout itself. Outside
 *                                             GitHub Actions it is refused unless --force is added too, so a
 *                                             working copy is not emptied by mistake.
 *   --dir <folder>                            work on this folder instead of the Code folder (e.g. a copy of it)
 *
 * A repository with no plugin.json is released as a standalone ZIP of the whole repository, so the
 * repository root is pruned instead, and the release workflow's own files (.github/ and the scripts it
 * adds) are always left out too.
 *
 * The standalone/ folder (see ./release-standalone.mjs) is never part of the plugin ZIP: when plugin.json is
 * at the repository root, so the Code folder is the whole repository, it is always left out too.
 *
 * Pattern rules (a subset of .gitignore) are described in scripts/release-exclude.txt.
 */
import { existsSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { isStandalone, manifestPath, repoRoot, STANDALONE_DIR } from "./manifest.mjs";
import { compile, findMatches, readPatterns, removeMatches } from "./release-patterns.mjs";

const EXCLUDE_FILE = join(repoRoot, "scripts", "release-exclude.txt");
const standalone = isStandalone();
const ALWAYS = ["__pycache__/", "venv/", ".git/", "*.pyc", "*.log",
	// A standalone ZIP is the whole repository: leave out the release workflow itself
	...(standalone ? [".github/", "scripts/manifest.mjs", "scripts/prune-release.mjs",
		"scripts/release-*.mjs", "scripts/release-*.txt", "scripts/release.sh"] : [])];

const argv = process.argv.slice(2);
const args = new Set(argv);
const remove = args.has("--delete");
const dirIndex = argv.indexOf("--dir");
if ((!remove && !args.has("--list")) || (dirIndex >= 0 && !argv[dirIndex + 1])) {
	console.error("Usage: node scripts/prune-release.mjs --list | --delete [--force] [--dir <folder>]");
	process.exit(2);
}
if (remove && process.env.GITHUB_ACTIONS !== "true" && !args.has("--force")) {
	console.error("Refusing to delete files outside GitHub Actions - use --list to preview, or add --force.");
	process.exit(2);
}

const own = readPatterns(EXCLUDE_FILE).map(compile);
const rules = [...ALWAYS.map(compile), ...own];
// plugin.json at the repository root: the Code folder is the whole repository, so the standalone/ folder
// at its top would otherwise be in the plugin ZIP
if (!standalone && dirname(manifestPath()) === ".") {
	rules.unshift({ pattern: `${STANDALONE_DIR}/`, dirOnly: true, regex: new RegExp(`^${STANDALONE_DIR}$`), matched: 0 });
}
const codeDir = dirIndex >= 0 ? resolve(argv[dirIndex + 1])
	: standalone ? repoRoot : dirname(join(repoRoot, manifestPath()));
if (!standalone && !existsSync(join(codeDir, "plugin.json"))) {
	console.error(`${codeDir} has no plugin.json - is it the plugin's Code folder?`);
	process.exit(2);
}
const found = findMatches(codeDir, rules);

const where = dirIndex >= 0 ? codeDir : relative(repoRoot, codeDir) || ".";
console.log(`${standalone ? "Standalone (no plugin.json), zipping" : "Code folder:"} ${where}  (exclude list: ${existsSync(EXCLUDE_FILE) ? relative(repoRoot, EXCLUDE_FILE) : "none"})`);
for (const { rel } of found) {
	console.log(`${remove ? "removed" : "would remove"}: ${rel}`);
}
if (remove) removeMatches(found);
if (!found.length) console.log("Nothing to leave out.");
// A pattern that matched nothing is probably a typo (or a file that has since been removed)
for (const rule of own.filter((r) => !r.matched)) {
	console.log(`::warning::scripts/release-exclude.txt: "${rule.pattern}" matched nothing in ${where}`);
}

#!/usr/bin/env node
/**
 * Build the standalone ZIP of a plugin that can also run without DWC.
 *
 * Trigger: the repository has a plugin.json and a standalone/ folder at its top (see hasStandaloneBuild in
 * ./manifest.mjs). The release workflow then builds this ZIP as well as the plugin ZIP and attaches both to
 * the Release. It can be run locally in the same way to try the ZIP out.
 *
 * standalone/standalone-<id>-<version>.zip is made from:
 *   1. the plugin's Code folder (the folder holding plugin.json), without what the plugin ZIP leaves out
 *      (scripts/release-exclude.txt, plus __pycache__/, venv/, .git/, *.pyc and *.log) and node_modules/;
 *   2. without what standalone/exclude.txt lists as well (same pattern rules, relative to the Code folder),
 *      e.g. plugin.json and the plugin's sd/ files;
 *   3. plus everything in standalone/ (e.g. an install script and a service file), added at the top of the
 *      ZIP and replacing a file of the same name - except exclude.txt, .gitignore and ZIP files.
 * Folders left empty are dropped. The ZIP is written to standalone/ (older standalone-*.zip files there are
 * removed first) - it is a build output, so add standalone/*.zip to .gitignore.
 *
 * Usage:
 *   node scripts/release-standalone.mjs           build the ZIP
 *   node scripts/release-standalone.mjs --list    show what would go in it - changes nothing
 */
import { execFileSync } from "node:child_process";
import { cpSync, mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative } from "node:path";
import { hasStandaloneBuild, manifestPath, readManifest, repoRoot, STANDALONE_DIR } from "./manifest.mjs";
import { compile, findMatches, readPatterns, removeMatches } from "./release-patterns.mjs";

const ALWAYS = ["__pycache__/", "venv/", ".git/", "*.pyc", "*.log"];
// In standalone/ but not copied into the ZIP
const NOT_COPIED = new Set(["exclude.txt", ".gitignore"]);

const list = process.argv.includes("--list");
if (process.argv.slice(2).some((a) => a !== "--list")) {
	console.error("Usage: node scripts/release-standalone.mjs [--list]");
	process.exit(2);
}
if (!hasStandaloneBuild()) {
	console.log(`No ${STANDALONE_DIR}/ folder next to a plugin.json - there is no standalone ZIP to build.`);
	process.exit(0);
}

const manifest = readManifest();
const standaloneDir = join(repoRoot, STANDALONE_DIR);
const codeDir = dirname(join(repoRoot, manifestPath()));
const zipName = `standalone-${manifest.id}-${manifest.version}.zip`;

const stage = mkdtempSync(join(tmpdir(), "standalone-"));
let exitCode;
try {
	exitCode = build();
} finally {
	rmSync(stage, { recursive: true, force: true });
}
process.exit(exitCode);

function build() {
	// 1-2. A copy of the Code folder, without what the plugin ZIP and standalone/exclude.txt leave out
	cpSync(codeDir, stage, {
		recursive: true,
		// Never copy the standalone/ folder itself (when plugin.json is at the repository root) or what is always
		// left out anyway
		filter: (src) => src !== standaloneDir && ![".git", "venv", "__pycache__", "node_modules"].includes(src.split(/[\\/]/).pop())
			|| src === codeDir,
	});
	const own = readPatterns(join(repoRoot, "scripts", "release-exclude.txt")).map(compile);
	const extra = readPatterns(join(standaloneDir, "exclude.txt")).map(compile);
	removeMatches(findMatches(stage, [...ALWAYS.map(compile), ...own]));
	const left = findMatches(stage, extra);
	removeMatches(left);
	for (const { rel } of left) console.log(`left out (${STANDALONE_DIR}/exclude.txt): ${rel}`);
	for (const rule of extra.filter((r) => !r.matched)) {
		console.log(`::warning::${STANDALONE_DIR}/exclude.txt: "${rule.pattern}" matched nothing in ${relative(repoRoot, codeDir) || "."}`);
	}

	// 3. The standalone files on top
	for (const entry of readdirSync(standaloneDir)) {
		if (NOT_COPIED.has(entry) || /\.zip$/i.test(entry)) continue;
		cpSync(join(standaloneDir, entry), join(stage, entry), { recursive: true, force: true });
	}
	dropEmptyFolders(stage);

	const files = listFiles(stage);
	if (!files.length) {
		console.error("::error::Nothing to put in the standalone ZIP: every file was left out.");
		return 1;
	}
	if (list) {
		console.log(`${zipName} would contain:`);
		for (const file of files) console.log(`  ${file}`);
		return 0;
	}

	for (const old of readdirSync(standaloneDir).filter((n) => /^standalone-.*\.zip$/i.test(n))) {
		rmSync(join(standaloneDir, old));
	}
	const zipPath = join(standaloneDir, zipName);
	makeZip(stage, zipPath);
	console.log(`Built ${relative(repoRoot, zipPath)} (${files.length} files)`);
	return 0;
}

function dropEmptyFolders(dir) {
	for (const entry of readdirSync(dir, { withFileTypes: true })) {
		if (entry.isDirectory()) dropEmptyFolders(join(dir, entry.name));
	}
	if (dir !== stage && readdirSync(dir).length === 0) rmSync(dir, { recursive: true });
}

function listFiles(dir) {
	const out = [];
	(function walk(current) {
		for (const entry of readdirSync(current, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
			const full = join(current, entry.name);
			if (entry.isDirectory()) walk(full);
			else out.push(relative(dir, full).split("\\").join("/"));
		}
	})(dir);
	return out;
}

// zip keeps file permissions (e.g. an executable install script). Where there is no zip (e.g. Git Bash on
// Windows), Python's zipfile does the same.
function makeZip(dir, zipPath) {
	try {
		execFileSync("zip", ["-r", "-X", "-q", zipPath, "."], { cwd: dir, stdio: ["ignore", "inherit", "inherit"] });
		return;
	} catch (error) {
		if (error.code !== "ENOENT") throw error;
	}
	const script = "import os,sys,zipfile\n"
		+ "src,out=sys.argv[1],sys.argv[2]\n"
		+ "with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:\n"
		+ " for root,dirs,names in os.walk(src):\n"
		+ "  dirs.sort()\n"
		+ "  for n in sorted(names):\n"
		+ "   p=os.path.join(root,n); z.write(p,os.path.relpath(p,src))\n";
	for (const python of ["python3", "python", "py"]) {
		try {
			execFileSync(python, ["-c", script, dir, zipPath], { stdio: ["ignore", "inherit", "inherit"] });
			return;
		} catch (error) {
			if (error.code !== "ENOENT") throw error;
		}
	}
	throw new Error("Neither zip nor Python was found to make the ZIP.");
}


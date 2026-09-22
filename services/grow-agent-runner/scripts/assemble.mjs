import {
    cpSync,
    copyFileSync,
    mkdirSync,
    readFileSync,
    readdirSync,
    writeFileSync,
    statSync,
} from "node:fs";
import {resolve, join, dirname, relative} from "node:path";
import {fileURLToPath} from "node:url";
import {execFileSync} from "node:child_process";
import {createHash} from "node:crypto";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
if (process.versions.node !== "24.18.0" || process.platform !== "linux" || process.arch !== "x64")
    throw new Error("Assembly requires Node 24.18.0 on Linux x64");
const output = resolve(root, "release", `linux-x64-${Date.now()}`);
mkdirSync(output, {recursive: true});
for (const name of ["package.json", "package-lock.json"])
    copyFileSync(join(root, name), join(output, name));
execFileSync("npm", ["ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"], {
    cwd: output,
    stdio: "inherit",
});
for (const name of ["dist", "protocol", "notices", "systemd", "image", "native", "scripts"])
    cpSync(join(root, name), join(output, name), {recursive: true});
for (const name of [
    "README.md",
    "CONTAINMENT.md",
    "RUNTIME.md",
    "API-CONTRACTS.md",
    ".node-version",
])
    copyFileSync(join(root, name), join(output, name));
execFileSync(process.execPath, [join(output, "scripts/patch-native.mjs"), output], {
    stdio: "inherit",
});
mkdirSync(join(output, "bin"));
copyFileSync(process.execPath, join(output, "bin/node"));
copyFileSync(
    resolve(dirname(process.execPath), "../LICENSE"),
    join(output, "notices/Node-LICENSE"),
);
const sha = (p) => createHash("sha256").update(readFileSync(p)).digest("hex");
const files = [];
function walk(dir) {
    for (const entry of readdirSync(dir, {withFileTypes: true})) {
        const p = join(dir, entry.name);
        if (entry.isDirectory()) walk(p);
        else if (entry.isFile())
            files.push({path: relative(output, p), sha256: sha(p), bytes: statSync(p).size});
    }
}
walk(output);
const lock = JSON.parse(readFileSync(join(output, "package-lock.json"), "utf8"));
const packages = [];
for (const [path, entry] of Object.entries(lock.packages)) {
    if (!path || entry.dev) continue;
    try {
        const installed = JSON.parse(readFileSync(join(output, path, "package.json"), "utf8"));
        packages.push({
            path,
            name: installed.name,
            version: installed.version,
            license: installed.license,
            integrity: entry.integrity,
            notices: files
                .filter(
                    (f) =>
                        f.path.startsWith(`${path}/`) &&
                        /license|copying|notice|unlicense/i.test(f.path.split("/").at(-1)),
                )
                .map((f) => f.path),
        });
    } catch (e) {
        if (e.code !== "ENOENT") throw e;
    }
}
writeFileSync(
    join(output, "sbom.cdx.json"),
    execFileSync("npm", ["sbom", "--sbom-format=cyclonedx", "--omit=dev", "--package-lock-only"], {
        cwd: output,
        maxBuffer: 8 * 1024 * 1024,
    }),
);
writeFileSync(
    join(output, "release-manifest.json"),
    JSON.stringify(
        {
            node: process.versions.node,
            platform: "linux-x64",
            lock_sha256: sha(join(output, "package-lock.json")),
            packages,
            files,
            certified_modes: [],
            release_gates: [
                "Review embedded and linked native component SBOM",
                "Task 7 final runtime image and containment evidence",
                "Task 8 Git harness",
                "Task 11 acceptance",
                "Task 12 pilot",
            ],
        },
        null,
        2,
    ),
);
console.log(output);

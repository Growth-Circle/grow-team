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
const npmProductionSbom = () => {
    const packagePath = join(output, "package.json");
    const original = readFileSync(packagePath);
    const production = JSON.parse(original);
    delete production.devDependencies;
    writeFileSync(packagePath, `${JSON.stringify(production, null, 2)}\n`);
    try {
        return execFileSync("npm", ["sbom", "--offline", "--sbom-format=cyclonedx", "--omit=dev"], {
            cwd: output,
            maxBuffer: 8 * 1024 * 1024,
        });
    } finally {
        writeFileSync(packagePath, original);
    }
};
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
const codexNoticePaths = ["notices/codex-0.154.0/LICENSE", "notices/codex-0.154.0/NOTICE"];
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
                .map((f) => f.path)
                .concat(
                    ["node_modules/@openai/codex", "node_modules/@openai/codex-linux-x64"].includes(
                        path,
                    )
                        ? codexNoticePaths
                        : [],
                )
                .sort(),
        });
    } catch (e) {
        if (e.code !== "ENOENT") throw e;
    }
}
const sbomPath = join(output, "sbom.cdx.json");
writeFileSync(sbomPath, npmProductionSbom());
const sbom = JSON.parse(readFileSync(sbomPath, "utf8"));
if (!Array.isArray(sbom.components)) throw new Error("Installed production SBOM has no components");
const pcreNoticePath = join(output, "notices/PCRE2-10.45-LICENCE.md");
const pcreNoticeSha256 = sha(pcreNoticePath);
const pcreProvenance = JSON.parse(
    readFileSync(join(output, "notices/PCRE2-10.45-provenance.json"), "utf8"),
);
if (pcreProvenance.notice_sha256 !== pcreNoticeSha256)
    throw new Error("PCRE2 notice hash mismatch");
writeFileSync(
    join(output, "release-manifest.json"),
    JSON.stringify(
        {
            node: process.versions.node,
            platform: "linux-x64",
            lock_sha256: sha(join(output, "package-lock.json")),
            packages,
            npm_sbom: {
                path: "sbom.cdx.json",
                scope: "installed_linux_production",
                sha256: sha(sbomPath),
                component_count: sbom.components.length,
            },
            native_notices: [
                {
                    component: "PCRE2",
                    version: "10.45",
                    runtime_consumer: "rg 15.2.0",
                    path: "notices/PCRE2-10.45-LICENCE.md",
                    sha256: pcreNoticeSha256,
                    provenance_path: "notices/PCRE2-10.45-provenance.json",
                    source_url: pcreProvenance.source_url,
                    source_commit: pcreProvenance.source_commit,
                    scope: "Named runtime notice input only; not an exhaustive native component inventory.",
                },
            ],
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

import {constants, openSync, fstatSync, readFileSync, closeSync} from "node:fs";
import {PrivateStore} from "../dist/config.js";
import {digest, parse} from "../dist/protocol.js";
import {runtimePackageIdentity, validateCodingEvidence} from "../dist/certification.js";
function readRecord(path) {
    const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
        const stat = fstatSync(fd);
        if (
            !stat.isFile() ||
            stat.uid !== process.getuid() ||
            (stat.mode & 0o077) !== 0 ||
            stat.size > 1024 * 1024
        )
            throw new Error("Evidence inputs must be bounded owner-only files");
        return JSON.parse(readFileSync(fd, "utf8"));
    } finally {
        closeSync(fd);
    }
}
if (process.argv.length !== 5)
    throw new Error(
        "Usage: node scripts/install-coding-evidence.mjs STATE PROBE_DESCRIPTOR EVIDENCE",
    );
const store = new PrivateStore(process.argv[2]);
const descriptor = parse("probe_descriptor", readRecord(process.argv[3]));
const evidence = readRecord(process.argv[4]);
const config = store.read("runtime.json"),
    connection = store.read("connection.json");
if (
    config?.owner_approved !== true ||
    connection?.state !== "connected" ||
    connection.runner_id !== descriptor.runner_id
)
    throw new Error("Current owner-approved runner configuration is required");
const hash = digest(evidence);
validateCodingEvidence(descriptor, evidence, hash, runtimePackageIdentity(config.model_image));
store.write("coding-certification.json", evidence);
store.write("runtime.json", {...config, coding_evidence_sha256: hash});
console.log(
    JSON.stringify({
        installed: true,
        configuration_digest: descriptor.configuration_digest,
        evidence_sha256: hash,
    }),
);

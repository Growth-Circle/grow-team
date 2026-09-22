import {readFileSync, readdirSync} from "node:fs";
import {createHash} from "node:crypto";
import {dirname, join} from "node:path";
import {fileURLToPath} from "node:url";
import {PrivateStore} from "./config.js";
import {canonical, digest, effectiveConfiguration, type Data} from "./protocol.js";
import {toolCatalog} from "./runtime.js";
const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
const hash = (value: unknown): boolean => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const tree = (value: unknown): boolean => typeof value === "string" && /^[0-9a-f]{40}$/.test(value);
export function runtimePackageIdentity(modelImage: string): Data {
    const dir = dirname(fileURLToPath(import.meta.url));
    return {
        model_image: modelImage,
        node: process.versions.node,
        modules_digest: digest(
            readdirSync(dir)
                .filter((p) => p.endsWith(".js"))
                .sort()
                .map((path) => ({path, sha256: sha(readFileSync(join(dir, path)))})),
        ),
        protocol_sha256: sha(readFileSync(join(dir, "../protocol/protocol-v1.schema.json"))),
        lock_sha256: sha(readFileSync(join(dir, "../package-lock.json"))),
        native_patch_sha256: sha(readFileSync(join(dir, "../native/patch-manifest.json"))),
    };
}
export function validateCodingEvidence(
    d: Data,
    evidence: Data,
    approvedHash: string,
    identity: Data,
    now = Date.now(),
): void {
    const fail = () => {
        throw new Error("Coding certification is incomplete, stale, or mismatched");
    };
    if (
        !hash(approvedHash) ||
        digest(evidence) !== approvedHash ||
        evidence.version !== 1 ||
        !d.workspace_binding ||
        !d.provider
    )
        fail();
    if (
        !["synthetic", "selected_chat", "selected_repository"].every((scope) =>
            d.provider.data_scope.includes(scope),
        )
    )
        fail();
    const config = effectiveConfiguration(d);
    if (
        digest(config) !== d.configuration_digest ||
        evidence.configuration_digest !== d.configuration_digest ||
        canonical(evidence.configuration) !== canonical(config) ||
        canonical(evidence.package) !== canonical(identity)
    )
        fail();
    const issued = Date.parse(evidence.issued_at),
        expires = Date.parse(evidence.expires_at);
    if (
        !Number.isFinite(issued) ||
        !Number.isFinite(expires) ||
        issued > now ||
        expires <= now ||
        expires - issued > 30 * 86400000
    )
        fail();
    const tools = toolCatalog({
        ...d,
        job_kind: "code",
        repository: {id: d.workspace_binding.repository_id},
    });
    if (
        canonical(evidence.tools) !== canonical(tools) ||
        !tools.some((t) => t.name === "grow_edit") ||
        !tools.some((t) => t.name === "grow_read")
    )
        fail();
    const cases = evidence.cases;
    if (
        !cases ||
        cases.provider?.real_provider !== true ||
        !hash(cases.provider.request_ids_sha256) ||
        cases.read?.passed !== true ||
        !hash(cases.read.output_sha256) ||
        cases.edit?.passed !== true ||
        !tree(cases.edit.before_tree) ||
        !tree(cases.edit.after_tree) ||
        cases.edit.before_tree === cases.edit.after_tree ||
        !hash(cases.edit.diff_sha256)
    )
        fail();
    const checks = cases.checks;
    if (
        !checks ||
        !Array.isArray(checks.definitions) ||
        checks.definitions.length === 0 ||
        digest(checks.definitions) !== d.workspace_binding.checks_digest ||
        !Array.isArray(checks.results) ||
        checks.results.length !== checks.definitions.length
    )
        fail();
    const ids = new Set();
    for (const check of checks.definitions) {
        const result = checks.results.find((r: Data) => r.check_id === check.id);
        if (
            ids.has(check.id) ||
            !result ||
            result.exit_code !== 0 ||
            result.tree_hash !== cases.edit.after_tree ||
            !hash(result.output_sha256) ||
            result.timed_out !== false
        )
            fail();
        ids.add(check.id);
    }
    const publication = cases.publication;
    if (publication?.passed !== true || publication.tree_hash !== cases.edit.after_tree) fail();
    const remote = d.policy.actions.some((action: string) =>
        ["git.push", "git.draft_pr"].includes(action),
    );
    if (remote) {
        if (
            publication.kind !== "remote" ||
            publication.conflict_rejected !== true ||
            publication.replay_idempotent !== true ||
            !hash(publication.remote_receipt_sha256)
        )
            fail();
    } else if (
        publication.kind !== "patch" ||
        publication.diff_artifact_sha256 !== cases.edit.diff_sha256 ||
        !/^[0-9a-f]{8}-[0-9a-f-]{27}$/.test(publication.artifact_id) ||
        !hash(publication.delivery_receipt_sha256)
    )
        fail();
    if (
        cases.containment?.model_stopped !== true ||
        cases.containment.tool_stopped !== true ||
        cases.containment.cancellation_passed !== true ||
        cases.containment.recovery_passed !== true ||
        !hash(cases.containment.evidence_sha256)
    )
        fail();
}
export function codingReadiness(store: PrivateStore, d: Data, config: Data): boolean {
    if (!config.coding_evidence_sha256) return false;
    try {
        const evidence = store.read("coding-certification.json");
        if (!evidence) return false;
        validateCodingEvidence(
            d,
            evidence,
            config.coding_evidence_sha256,
            runtimePackageIdentity(config.model_image),
        );
        return true;
    } catch {
        return false;
    }
}

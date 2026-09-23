import {test} from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {parse, validateDescriptor, digest, effectiveConfiguration} from "../dist/protocol.js";
const fixtures = JSON.parse(
    readFileSync(
        new URL("../../../zerver/tests/fixtures/agents/protocol-v1.json", import.meta.url),
        "utf8",
    ),
);
for (const c of fixtures.valid)
    test(`valid ${c.name}`, () => assert.deepEqual(parse(c.schema, c.payload), c.payload));
for (const c of fixtures.invalid)
    test(`invalid ${c.name}`, () => assert.throws(() => parse(c.schema, c.payload)));
test("both descriptor digests bind unchanged tested configuration", () => {
    const d = structuredClone(fixtures.valid[0].payload);
    d.configuration_digest = digest(d.tested_configuration);
    d.descriptor_digest = digest(
        Object.fromEntries(Object.entries(d).filter(([k]) => k !== "descriptor_digest")),
    );
    validateDescriptor(d, d.runner_id);
    d.request += "tampered";
    assert.throws(() => validateDescriptor(d, d.runner_id));
});
test("a manage descriptor accepts the narrowed answer-shaped policy", () => {
    const template = fixtures.valid.find(
        (c: any) => c.schema === "attempt_descriptor" && c.name === "narrowed_answer_descriptor",
    ).payload;
    const manage = {...structuredClone(template), job_kind: "manage"};
    assert.deepEqual(parse("attempt_descriptor", manage), manage);
});
test("a manage descriptor with a repository is rejected", () => {
    const template = fixtures.valid.find(
        (c: any) => c.schema === "attempt_descriptor" && c.name === "narrowed_answer_descriptor",
    ).payload;
    const codeRepository = fixtures.valid.find(
        (c: any) => c.schema === "attempt_descriptor" && c.name === "code_descriptor",
    ).payload.repository;
    const manage = {
        ...structuredClone(template),
        job_kind: "manage",
        repository: structuredClone(codeRepository),
    };
    assert.throws(() => parse("attempt_descriptor", manage), /Manage job scope/);
});
test("all canonical hashes match the Python protocol oracle", () => {
    const vectors = JSON.parse(
        readFileSync(new URL("../protocol/conformance-digests.json", import.meta.url), "utf8"),
    );
    for (const vector of vectors) {
        const c = fixtures.valid.find((c: any) => c.name === vector.name);
        assert.equal(digest(parse(c.schema, c.payload)), vector.payload_digest, c.name);
        if (vector.descriptor_digest) {
            const d = structuredClone(c.payload);
            d.descriptor_digest = vector.descriptor_digest;
            assert.equal(d.configuration_digest, vector.configuration_digest);
            validateDescriptor(d, d.runner_id, c.schema === "probe_descriptor");
        }
    }
});
test("an instructed descriptor digest matches the Python oracle", () => {
    const vectors = JSON.parse(
        readFileSync(new URL("../protocol/conformance-digests.json", import.meta.url), "utf8"),
    );
    const vector = vectors.find((v: any) => v.name === "instructed_answer_descriptor");
    const c = fixtures.valid.find((c: any) => c.name === "instructed_answer_descriptor");
    const parsed = parse(c.schema, c.payload);
    assert.equal(digest(parsed), vector.payload_digest);
    assert.equal(digest(effectiveConfiguration(parsed)), vector.configuration_digest);
    validateDescriptor(parsed, parsed.runner_id);
});
test("a profile revision mismatch is rejected", () => {
    const c = fixtures.valid.find((c: any) => c.name === "instructed_answer_descriptor");
    const stale = structuredClone(c.payload);
    stale.instructions.profile.revision += 1;
    assert.throws(() => parse("attempt_descriptor", stale), /Stale instructions revision/);
});

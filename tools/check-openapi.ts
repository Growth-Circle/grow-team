#!/usr/bin/env node

import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import path from "node:path";
import {parseArgs} from "node:util";

import SwaggerParser from "@apidevtools/swagger-parser";
import * as Diff from "diff";
import * as ExampleValidator from "openapi-examples-validator";
import {format as prettierFormat} from "prettier";
import {
    CST,
    Composer,
    LineCounter,
    Parser,
    Scalar,
    YAMLMap,
    YAMLSeq,
    parse as parseYAML,
    visit,
} from "yaml";

const usage = "Usage: check-openapi.ts [--fix] <file>...";
const {
    values: {fix, help},
    positionals: files,
} = parseArgs({options: {fix: {type: "boolean"}, help: {type: "boolean"}}, allowPositionals: true});

if (help) {
    console.log(usage);
    process.exit(0);
}

async function checkFile(file: string): Promise<void> {
    const yaml = await fs.promises.readFile(file, "utf8");
    const lineCounter = new LineCounter();
    const tokens = [...new Parser(lineCounter.addNewLine).parse(yaml)];
    const docs = [...new Composer().compose(tokens)];
    if (docs.length !== 1) {
        return;
    }
    const [doc] = docs;
    assert.ok(doc !== undefined);
    if (doc.errors.length > 0) {
        for (const error of doc.errors) {
            console.error("%s: %s", file, error.message);
        }
        process.exitCode = 1;
        return;
    }

    const root = doc.contents;
    if (!(root instanceof YAMLMap)) {
        return;
    }
    // A per-area fragment (zerver/openapi/features/*.yaml) documents
    // paths and components that get merged into the real spec at load
    // time; it has no "openapi" root key of its own and is not a
    // complete document, so full standalone validation below does not
    // apply to it. It still gets the structural and formatting checks.
    const isCompleteDocument = root.has("openapi");

    let ok = true;
    const reformats = new Map<
        number,
        {value: string; context: Parameters<typeof CST.setScalarValue>[2]}
    >();
    const promises: Promise<void>[] = [];

    visit(doc, {
        Map(_key, node) {
            if (node.has("$ref") && node.items.length !== 1) {
                assert.ok(node.range);
                const {line, col} = lineCounter.linePos(node.range[0]);
                console.error("%s:%d:%d: Siblings of $ref have no effect", file, line, col);
                ok = false;
            }

            const combinator = ["allOf", "anyOf", "oneOf"].find((combinator) =>
                node.has(combinator),
            );
            if (node.has("nullable") && combinator !== undefined) {
                assert.ok(node.range);
                const {line, col} = lineCounter.linePos(node.range[0]);
                console.error(
                    `%s:%d:%d: nullable has no effect as a sibling of ${combinator}`,
                    file,
                    line,
                    col,
                );
                ok = false;
            }
        },

        Pair(_key, node) {
            if (
                node.key instanceof Scalar &&
                node.key.value === "allOf" &&
                node.value instanceof YAMLSeq &&
                node.value.items.filter(
                    (subschema) => !(subschema instanceof YAMLMap && subschema.has("$ref")),
                ).length > 1
            ) {
                assert.ok(node.value.range);
                const {line, col} = lineCounter.linePos(node.value.range[0]);
                console.error("%s:%d:%d: Too many inline allOf subschemas", file, line, col);
                ok = false;
            }

            if (
                node.key instanceof Scalar &&
                node.key.value === "description" &&
                node.value instanceof Scalar &&
                typeof node.value.value === "string"
            ) {
                const value = node.value;
                const description = node.value.value;
                promises.push(
                    (async () => {
                        let formatted = await prettierFormat(description, {
                            parser: "markdown",
                        });
                        if (
                            value.type !== Scalar.BLOCK_FOLDED &&
                            value.type !== Scalar.BLOCK_LITERAL
                        ) {
                            formatted = formatted.replace(/\n$/, "");
                        }
                        if (formatted !== description) {
                            assert.ok(value.range);
                            if (fix) {
                                reformats.set(value.range[0], {
                                    value: formatted,
                                    context: {afterKey: true},
                                });
                            } else {
                                ok = false;
                                const {line, col} = lineCounter.linePos(value.range[0]);
                                console.error(
                                    "%s:%d:%d: Format description with Prettier:",
                                    file,
                                    line,
                                    col,
                                );
                                let diff = "";
                                for (const part of Diff.diffLines(description, formatted)) {
                                    const prefix = part.added
                                        ? "\u001B[32m+"
                                        : part.removed
                                          ? "\u001B[31m-"
                                          : "\u001B[34m ";
                                    diff += prefix;
                                    diff += part.value
                                        .replace(/\n$/, "")
                                        .replaceAll("\n", "\n" + prefix);
                                    diff += "\n";
                                }
                                diff += "\u001B[0m";
                                console.error(diff);
                            }
                        }
                    })(),
                );
            }
        },
    });
    await Promise.all(promises);

    if (!ok) {
        process.exitCode = 1;
    }
    if (reformats.size > 0) {
        console.log("%s: Fixing problems", file);
        for (const token of tokens) {
            if (token.type === "document") {
                CST.visit(token, ({value}) => {
                    let reformat;
                    if (
                        CST.isScalar(value) &&
                        (reformat = reformats.get(value.offset)) !== undefined
                    ) {
                        CST.setScalarValue(value, reformat.value, reformat.context);
                    }
                });
            }
        }
        await fs.promises.writeFile(file, tokens.map((token) => CST.stringify(token)).join(""));
    }

    if (!isCompleteDocument) {
        return;
    }

    try {
        await SwaggerParser.validate(file);
    } catch (error) {
        if (!(error instanceof SyntaxError)) {
            throw error;
        }
        console.error("%s: %s", file, error.message);
        process.exitCode = 1;
    }
    const res = await ExampleValidator.validateFile(file);
    if (!res.valid) {
        for (const error of res.errors) {
            console.error(error);
        }
        process.exitCode = 1;
    }
}

// zerver/openapi/features/*.yaml fragments document paths and components
// that only exist once merged into zerver/openapi/zulip.yaml (see
// merge_openapi_fragment in zerver/openapi/openapi.py, which this
// mirrors); checkFile() above validates each file on its own, but a
// fragment is never a complete document by itself, so its paths and
// examples otherwise never go through SwaggerParser or the example
// validator at all. Build the same merged spec Zulip serves at runtime
// and validate that too, whenever this run touches zulip.yaml or a
// fragment.
type JsonObject = Record<string, unknown>;

function isJsonObject(value: unknown): value is JsonObject {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asObject(value: unknown, context: string): JsonObject {
    assert.ok(isJsonObject(value), `Expected an object in ${context}`);
    return value;
}

function ensureObject(container: JsonObject, key: string): JsonObject {
    container[key] ??= {};
    return asObject(container[key], key);
}

// Array.isArray()'s built-in type predicate narrows to any[], which then
// makes spreading the result into another array's push() unsafe by this
// codebase's lint rules; narrow to unknown[] instead.
function isArray(value: unknown): value is unknown[] {
    return Array.isArray(value);
}

const FRAGMENT_TOP_LEVEL_KEYS = new Set(["paths", "components", "events"]);
const FRAGMENT_COMPONENT_KEYS = new Set(["schemas", "securitySchemes"]);

function mergeFragment(spec: JsonObject, fragmentPath: string, fragment: JsonObject): void {
    const unknownKeys = Object.keys(fragment).filter((key) => !FRAGMENT_TOP_LEVEL_KEYS.has(key));
    assert.ok(
        unknownKeys.length === 0,
        `Unsupported top-level key(s) ${unknownKeys.join(", ")} in ${fragmentPath}`,
    );
    const fragmentComponents =
        fragment["components"] === undefined ? {} : asObject(fragment["components"], fragmentPath);
    const unknownComponentKeys = Object.keys(fragmentComponents).filter(
        (key) => !FRAGMENT_COMPONENT_KEYS.has(key),
    );
    assert.ok(
        unknownComponentKeys.length === 0,
        `Unsupported components key(s) ${unknownComponentKeys.join(", ")} in ${fragmentPath}`,
    );

    const paths = ensureObject(spec, "paths");
    const fragmentPaths =
        fragment["paths"] === undefined ? {} : asObject(fragment["paths"], fragmentPath);
    for (const [p, item] of Object.entries(fragmentPaths)) {
        assert.ok(!(p in paths), `Duplicate OpenAPI path ${p} found in ${fragmentPath}`);
        paths[p] = item;
    }

    const components = ensureObject(spec, "components");
    const schemas = ensureObject(components, "schemas");
    const fragmentSchemas =
        fragmentComponents["schemas"] === undefined
            ? {}
            : asObject(fragmentComponents["schemas"], fragmentPath);
    for (const [name, schema] of Object.entries(fragmentSchemas)) {
        assert.ok(!(name in schemas), `Duplicate OpenAPI schema ${name} found in ${fragmentPath}`);
        schemas[name] = schema;
    }

    const securitySchemes = ensureObject(components, "securitySchemes");
    const fragmentSecuritySchemes =
        fragmentComponents["securitySchemes"] === undefined
            ? {}
            : asObject(fragmentComponents["securitySchemes"], fragmentPath);
    for (const [name, scheme] of Object.entries(fragmentSecuritySchemes)) {
        assert.ok(
            !(name in securitySchemes),
            `Duplicate OpenAPI security scheme ${name} found in ${fragmentPath}`,
        );
        securitySchemes[name] = scheme;
    }

    if (fragment["events"] !== undefined) {
        assert.ok(isArray(fragment["events"]), `events must be an array in ${fragmentPath}`);
        const eventsPath = asObject(paths["/events"], fragmentPath);
        const getOperation = asObject(eventsPath["get"], fragmentPath);
        const responses = asObject(getOperation["responses"], fragmentPath);
        const response200 = asObject(responses["200"], fragmentPath);
        const content = asObject(response200["content"], fragmentPath);
        const appJson = asObject(content["application/json"], fragmentPath);
        const schema = asObject(appJson["schema"], fragmentPath);
        assert.ok(isArray(schema["allOf"]), `/events schema missing allOf in ${fragmentPath}`);
        const secondBranch = asObject(schema["allOf"][1], fragmentPath);
        const properties = asObject(secondBranch["properties"], fragmentPath);
        const eventsProperty = asObject(properties["events"], fragmentPath);
        const items = asObject(eventsProperty["items"], fragmentPath);
        assert.ok(isArray(items["oneOf"]), `/events items missing oneOf in ${fragmentPath}`);
        items["oneOf"].push(...fragment["events"]);
    }
}

async function checkMergedSpec(zulipYamlPath: string): Promise<void> {
    const specValue: unknown = parseYAML(await fs.promises.readFile(zulipYamlPath, "utf8"));
    const spec = asObject(specValue, zulipYamlPath);
    const featuresDir = path.join(path.dirname(zulipYamlPath), "features");
    const fragmentNames = (await fs.promises.readdir(featuresDir))
        .filter((name) => name.endsWith(".yaml"))
        .toSorted();
    for (const name of fragmentNames) {
        const fragmentPath = path.join(featuresDir, name);
        const fragmentValue: unknown = parseYAML(await fs.promises.readFile(fragmentPath, "utf8"));
        const fragment = fragmentValue === null ? {} : asObject(fragmentValue, fragmentPath);
        mergeFragment(spec, fragmentPath, fragment);
    }

    const tmpDir = await fs.promises.mkdtemp(path.join(os.tmpdir(), "check-openapi-merged-"));
    // Named .yaml (not .json) so both SwaggerParser and the example
    // validator use their YAML reader; JSON is valid YAML, so the
    // serialized merged spec parses the same way either way.
    const tmpPath = path.join(tmpDir, "zulip-merged.yaml");
    try {
        await fs.promises.writeFile(tmpPath, JSON.stringify(spec));
        try {
            await SwaggerParser.validate(tmpPath);
        } catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            console.error("%s (merged spec): %s", zulipYamlPath, message);
            process.exitCode = 1;
        }
        const res = await ExampleValidator.validateFile(tmpPath);
        if (!res.valid) {
            for (const error of res.errors) {
                console.error("%s (merged spec): %s", zulipYamlPath, error);
            }
            process.exitCode = 1;
        }
    } finally {
        await fs.promises.rm(tmpDir, {recursive: true, force: true});
    }
}

for (const file of files) {
    await checkFile(file);
}

const REPO_ZULIP_YAML = path.join(
    path.dirname(new URL(import.meta.url).pathname),
    "..",
    "zerver",
    "openapi",
    "zulip.yaml",
);
const touchesMergedSpec = files.some(
    (file) =>
        path.basename(file) === "zulip.yaml" || path.basename(path.dirname(file)) === "features",
);
if (touchesMergedSpec) {
    await checkMergedSpec(REPO_ZULIP_YAML);
}

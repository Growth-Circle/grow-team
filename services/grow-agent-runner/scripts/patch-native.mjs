import {readFileSync, writeFileSync, copyFileSync} from "node:fs";
import {resolve, join} from "node:path";
import {createHash} from "node:crypto";
const root = resolve(process.argv[2] || new URL("..", import.meta.url).pathname);
const target = join(root, "node_modules/@agentclientprotocol/codex-acp/dist/index.js");
const source = readFileSync(target, "utf8");
const hash = (s) => createHash("sha256").update(s).digest("hex");
const expected = "f45a64dc3a994556ebdb688dc8d59b86945a9b2f940a3e3e545739dd265a7cc5";
if (hash(source) !== expected) throw Error("Pinned adapter patch source drift");
let output = source;
function patch(before, after) {
    if (output.split(before).length !== 2) throw Error("Native patch target drift");
    output = output.replace(before, after);
}
patch(
    "  async sendRequest(request) {",
    "  async sendRequest(request) { request = growConstrain(request);",
);
patch(
    '    await this.connection.sendRequest("thread/settings/update", params);',
    '    throw new Error("Native settings changes denied");',
);
patch("  onTurnCompleted(userPromptText) {", "  onTurnCompleted(userPromptText) { return;");
patch(
    "    this.connection.onRequest(CommandExecutionApprovalRequest, async (params) => {",
    '    this.connection.onRequest(new import_node2.RequestType("item/tool/call"), growTool);\n    this.connection.onRequest(CommandExecutionApprovalRequest, async (params) => {',
);
output = output.replace(
    "#!/usr/bin/env node\n",
    '#!/usr/bin/env node\nimport {constrain as growConstrain, tool as growTool} from "./grow-policy.mjs";\n',
);
writeFileSync(join(root, "native/patched-adapter.mjs"), output);
copyFileSync(join(root, "native/policy.mjs"), join(root, "native/grow-policy.mjs"));
writeFileSync(
    join(root, "native/patch-manifest.json"),
    JSON.stringify(
        {
            adapter: "1.12.0",
            codex: "0.154.0",
            upstream_sha256: expected,
            patched_sha256: hash(output),
            policy_sha256: hash(readFileSync(join(root, "native/policy.mjs"))),
            patch_script_sha256: hash(readFileSync(new URL(import.meta.url))),
        },
        null,
        2,
    ) + "\n",
);

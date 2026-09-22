import {mkdtempSync} from "node:fs";
import {join} from "node:path";
import {randomUUID} from "node:crypto";
import {PrivateStore} from "./config.js";
export function brokerSocket(root: string, scope: string): string {
    // Linux Unix paths have a small fixed limit. The private parent is never mounted.
    const directory = mkdtempSync("/tmp/grow-broker-");
    const socket = join(directory, "broker.sock");
    new PrivateStore(root).write(`broker-${randomUUID()}.json`, {scope, directory, socket});
    return socket;
}

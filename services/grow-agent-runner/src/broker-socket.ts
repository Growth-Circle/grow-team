import {mkdtempSync, lstatSync, realpathSync, readFileSync} from "node:fs";
import {join} from "node:path";
import {randomUUID} from "node:crypto";
import {PrivateStore} from "./config.js";
export function brokerSystemOwner(uidMap: string, overflowUid: string, ownerUid: number): number {
    const rows = uidMap
        .trim()
        .split("\n")
        .map((row) => row.trim().split(/\s+/));
    const overflow = overflowUid.trim();
    if (
        rows.length !== 1 ||
        rows[0]!.length !== 3 ||
        !rows[0]!.every((value) => /^\d+$/.test(value)) ||
        !/^\d+$/.test(overflow)
    )
        throw new Error("Unsupported broker user namespace");
    const [inside, outside, count] = rows[0]!.map(Number);
    const overflowId = Number(overflow);
    if (!Number.isSafeInteger(overflowId) || overflowId <= 0 || overflowId >= 4294967295)
        throw new Error("Unsupported broker user namespace");
    if (inside === 0 && outside === 0 && count === 4294967295) return 0;
    // PrivateTmp maps only the service owner. System owners remain unmapped.
    if (
        Number.isSafeInteger(ownerUid) &&
        ownerUid > 0 &&
        ownerUid < 4294967295 &&
        inside === ownerUid &&
        outside === ownerUid &&
        count === 1 &&
        overflowId !== ownerUid
    )
        return overflowId;
    throw new Error("Unsupported broker user namespace");
}
export function validateBrokerDirectory(directory: string): void {
    const stat = lstatSync(directory);
    if (
        !stat.isDirectory() ||
        stat.isSymbolicLink() ||
        stat.uid !== process.getuid?.() ||
        (stat.mode & 0o777) !== 0o700 ||
        realpathSync(directory) !== directory
    )
        throw new Error("Broker runtime directory must be owner-private without symlinks");
}
export function brokerSocket(root: string, scope: string): string {
    // The Docker daemon shares this runtime path even when the runner uses PrivateTmp.
    const systemOwner = brokerSystemOwner(
        readFileSync("/proc/self/uid_map", "utf8"),
        readFileSync("/proc/sys/kernel/overflowuid", "utf8"),
        process.getuid!(),
    );
    // Validate the fixed immutable ancestor chain, including its filesystem root.
    for (const parent of ["/", "/run", "/run/user"]) {
        const stat = lstatSync(parent);
        if (
            !stat.isDirectory() ||
            stat.isSymbolicLink() ||
            stat.uid !== systemOwner ||
            realpathSync(parent) !== parent ||
            (stat.mode & 0o022) !== 0
        )
            throw new Error("Unsafe runtime directory parent");
    }
    const parent = `/run/user/${process.getuid!()}`;
    validateBrokerDirectory(parent);
    const directory = mkdtempSync(join(parent, "grow-broker-"));
    validateBrokerDirectory(directory);
    const socket = join(directory, "broker.sock");
    if (Buffer.byteLength(socket) >= 104)
        throw new Error("Broker socket path exceeds platform limit");
    new PrivateStore(root).write(`broker-${randomUUID()}.json`, {scope, directory, socket});
    return socket;
}

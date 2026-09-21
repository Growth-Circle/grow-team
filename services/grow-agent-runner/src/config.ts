import {
    constants,
    mkdirSync,
    lstatSync,
    openSync,
    closeSync,
    writeFileSync,
    readFileSync,
    renameSync,
    fsyncSync,
    fstatSync,
    realpathSync,
} from "node:fs";
import {join, resolve} from "node:path";
import {randomUUID} from "node:crypto";
import type {Data} from "./protocol.js";
export class PrivateStore {
    readonly root: string;
    constructor(root: string) {
        this.root = resolve(root);
        mkdirSync(this.root, {recursive: true, mode: 0o700});
        const s = lstatSync(this.root);
        if (
            !s.isDirectory() ||
            s.isSymbolicLink() ||
            s.uid !== process.getuid?.() ||
            (s.mode & 0o077) !== 0
        )
            throw new Error("State directory must be owner-only");
    }
    path(name: string): string {
        if (!/^[a-z0-9._-]+$/.test(name)) throw new Error("Invalid state filename");
        return join(this.root, name);
    }
    check(name: string): boolean {
        try {
            const s = lstatSync(this.path(name));
            if (
                !s.isFile() ||
                s.isSymbolicLink() ||
                s.uid !== process.getuid?.() ||
                (s.mode & 0o077) !== 0
            )
                throw new Error("Unsafe state file");
            return true;
        } catch (e) {
            if ((e as NodeJS.ErrnoException).code === "ENOENT") return false;
            throw e;
        }
    }
    read<T = Data>(name: string): T | null {
        if (!this.check(name)) return null;
        const fd = openSync(this.path(name), constants.O_RDONLY | constants.O_NOFOLLOW);
        try {
            return JSON.parse(readFileSync(fd, "utf8")) as T;
        } finally {
            closeSync(fd);
        }
    }
    write(name: string, value: unknown): void {
        this.check(name);
        const temp = this.path(`${name}.${randomUUID()}.tmp`);
        const fd = openSync(
            temp,
            constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW,
            0o600,
        );
        try {
            writeFileSync(fd, JSON.stringify(value));
            fsyncSync(fd);
        } finally {
            closeSync(fd);
        }
        renameSync(temp, this.path(name));
        const parent = openSync(this.root, constants.O_RDONLY);
        try {
            fsyncSync(parent);
        } finally {
            closeSync(parent);
        }
    }
    createFile(name: string): string {
        if (!this.check(name)) {
            const fd = openSync(
                this.path(name),
                constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW,
                0o600,
            );
            closeSync(fd);
        }
        return this.path(name);
    }
}
export function controlOrigin(value: string): string {
    const url = new URL(value);
    if (
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        url.pathname !== "/" ||
        (url.protocol !== "https:" &&
            !(
                url.protocol === "http:" &&
                ["127.0.0.1", "[::1]", "localhost"].includes(url.hostname)
            ))
    )
        throw new Error("Unsafe control-plane origin");
    return url.origin;
}
export function childEnvironment(values: Record<string, string>): Record<string, string> {
    // No inherited process environment enters an adapter. Only owner-approved values can be added.
    const allowed = new Set(["LANG", "LC_ALL", "TERM", "TZ"]);
    for (const key of Object.keys(values))
        if (!allowed.has(key)) throw new Error("Reserved or unapproved environment key");
    return {...values};
}
export function localSecret(path: string): string {
    const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
        const s = fstatSync(fd);
        if (!s.isFile() || s.uid !== process.getuid?.() || (s.mode & 0o077) !== 0 || s.size > 65536)
            throw new Error("Unsafe local secret reference");
        return readFileSync(fd, "utf8").trimEnd();
    } finally {
        closeSync(fd);
    }
}
export function workspacePath(path: string): string {
    const real = realpathSync(path);
    if (!lstatSync(real).isDirectory()) throw new Error("Workspace must be a directory");
    return real;
}

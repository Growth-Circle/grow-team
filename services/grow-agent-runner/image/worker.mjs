import fs from "node:fs";
import path from "node:path";
process.umask(7);
const safe = (p) => {
    if (
        typeof p !== "string" ||
        !p ||
        p.startsWith("/") ||
        p.split("/").some((x) => !x || x === "." || x === ".." || x === ".git") ||
        /[\\\0]/.test(p)
    )
        throw Error("Unsafe path");
    let at = "/workspace";
    for (const part of p.split("/")) {
        at = path.join(at, part);
        if (fs.existsSync(at) && fs.lstatSync(at).isSymbolicLink()) throw Error("Symlink denied");
    }
    return at;
};
const [action, encoded] = process.argv.slice(2);
if (action === "seed") {
    fs.cpSync("/seed", "/workspace", {
        recursive: true,
        dereference: false,
        preserveTimestamps: false,
    });
} else {
    const args = JSON.parse(Buffer.from(encoded, "base64").toString());
    if (action === "read") {
        const fd = fs.openSync(safe(args.path), fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
        try {
            const s = fs.fstatSync(fd);
            if (!s.isFile() || s.size > 51200) throw Error("Read limit");
            process.stdout.write(fs.readFileSync(fd));
        } finally {
            fs.closeSync(fd);
        }
    } else if (action === "edit") {
        const p = safe(args.path);
        fs.mkdirSync(path.dirname(p), {recursive: true, mode: 0o2770});
        const fd = fs.openSync(
            p,
            fs.constants.O_WRONLY |
                fs.constants.O_CREAT |
                fs.constants.O_TRUNC |
                fs.constants.O_NOFOLLOW,
            0o660,
        );
        try {
            fs.writeFileSync(fd, Buffer.from(args.content, "base64"));
        } finally {
            fs.closeSync(fd);
        }
    } else if (action === "search") {
        let count = 0,
            bytes = 0;
        const walk = (dir, prefix) => {
            for (const e of fs.readdirSync(dir, {withFileTypes: true})) {
                if (++count > 20000) throw Error("Search file limit");
                if (e.isSymbolicLink()) continue;
                const p = path.join(dir, e.name);
                if (e.isDirectory()) walk(p, prefix + e.name + "/");
                else if (e.isFile()) {
                    const s = fs.statSync(p);
                    if (s.size > 51200) continue;
                    for (const [i, line] of fs.readFileSync(p, "utf8").split("\n").entries())
                        if (line.includes(args.query)) {
                            const out =
                                JSON.stringify({
                                    path: prefix + e.name,
                                    line: i + 1,
                                    text: line.slice(0, 2000),
                                }) + "\n";
                            bytes += Buffer.byteLength(out);
                            if (bytes > 50000) return;
                            process.stdout.write(out);
                        }
                }
            }
        };
        walk(args.path === "." ? "/workspace" : safe(args.path), "");
    } else throw Error("Unknown file tool");
}

// Buffer complete output before release. This also removes secrets split across chunks.
export class SecretFilter {
    private secrets = new Set<string>();
    add(secret: string): void {
        if (!secret || secret.length > 65536) throw new Error("Invalid secret");
        this.secrets.add(secret);
    }
    text(value: string): string {
        for (const secret of [...this.secrets].sort((a, b) => b.length - a.length))
            value = value.split(secret).join("[REDACTED]");
        return value;
    }
    bytes(value: Buffer): Buffer {
        return Buffer.from(this.text(value.toString("utf8")));
    }
    assertArguments(value: unknown): void {
        if (typeof value === "string" && this.text(value) !== value)
            throw new Error("Secret-bearing tool arguments denied");
        if (Array.isArray(value)) for (const item of value) this.assertArguments(item);
        else if (value && typeof value === "object")
            for (const [key, item] of Object.entries(value)) {
                this.assertArguments(key);
                this.assertArguments(item);
            }
    }
}

// The channels overlay is rendered when it first opens, so a name typed
// elsewhere (the room modal) cannot be put in its search box beforehand.
let pending_name: string | undefined;

export function set(name: string): void {
    pending_name = name;
}

export function take(): string | undefined {
    const name = pending_name;
    pending_name = undefined;
    return name;
}

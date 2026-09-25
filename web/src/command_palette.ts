// Placeholder for the command palette (Cmd/Ctrl+K, `/`, `q`, `w`).
// The full palette replaces this file and keeps these exports. Until
// then nothing opens, so is_open() is always false and the Esc key
// keeps its usual meaning.

export type PaletteOpenOptions = {
    group?: "rooms";
};

export type PaletteProvider = (query: string) => Promise<unknown[]>;

export function open(_opts?: PaletteOpenOptions): void {
    // Nothing to open.
}

export function close(): void {
    // Nothing to close.
}

export function toggle(): void {
    // Nothing to toggle.
}

export function is_open(): boolean {
    return false;
}

export function register_provider(_provider: PaletteProvider): void {
    // The full palette asks its providers for results; this
    // placeholder has no results to show.
}

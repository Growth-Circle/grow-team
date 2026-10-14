import * as z from "zod/mini";

export const task_note_schema = z.object({
    id: z.number(),
    task_id: z.number(),
    author_id: z.number(),
    content: z.string(),
    // Rendered by the server with the same Markdown processor as messages.
    rendered_content: z.string(),
    date_created: z.number(),
});

export const task_notes_response_schema = z.object({
    notes: z.array(task_note_schema),
});

export const add_note_response_schema = z.object({
    note: task_note_schema,
    // People who were mentioned but cannot read the card, so they
    // were not notified.
    ignored_mentions: z.array(z.number()),
});

export type TaskNote = z.infer<typeof task_note_schema>;

// A note can arrive twice for the person who wrote it: in the response
// to their request and in the event for the card. Matching on the id
// keeps one copy.
export function add_note(notes: TaskNote[], note: TaskNote): TaskNote[] {
    return [...notes.filter((existing) => existing.id !== note.id), note].toSorted(
        (a, b) => a.id - b.id,
    );
}

export function remove_note(notes: TaskNote[], note_id: number): TaskNote[] {
    return notes.filter((note) => note.id !== note_id);
}

// Mirrors the delete rule of the notes endpoint; the server stays the
// authority.
export function can_delete_note(
    note: TaskNote,
    current_user: {user_id: number; is_admin: boolean},
): boolean {
    return current_user.is_admin || note.author_id === current_user.user_id;
}

import {test} from "node:test";
import assert from "node:assert/strict";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {Journal} from "../dist/journal.js";
import {Transport, TransportError} from "../dist/transport.js";
import {Coordinator, runService, type Supervisor} from "../dist/supervisor.js";

for (const canExecute of [false, true]) {
    test(`idle presence continues when canExecute is ${canExecute}`, async () => {
        const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-idle-test-")));
        const calls: string[] = [],
            heartbeats: unknown[] = [];
        const transport = new Transport("http://localhost", journal, () => "access");
        transport.request = async (route, data) => {
            calls.push(route);
            if (route === "/runner/leases") return {leases: []};
            if (route === "/runner/heartbeat") {
                heartbeats.push(data);
                return {leases: []};
            }
            if (route === "/runner/claims") return {attempt: null};
            throw Error(route);
        };
        transport.poll = async (callback) => {
            for (let i = 0; i < 3; i++) await callback();
        };
        const supervisor: Supervisor = {
            inspect: async () => {
                calls.push("inspect");
                return [];
            },
            stop: async () => ({confirmed: true}),
            canExecute: () => canExecute,
            start: async () => {
                throw Error("Unexpected launch");
            },
            applyInput: async () => {
                throw Error("Unexpected input");
            },
            probe: async () => {
                throw Error("Unexpected probe");
            },
        };
        const coordinator = new Coordinator(journal, transport, supervisor, "runner", {
            assertRuntime: () => {
                throw Error("Unexpected readiness check");
            },
        });
        await runService(
            coordinator,
            {
                access: async () => {
                    calls.push("credentials");
                },
            },
            transport,
            new AbortController().signal,
        );
        assert.deepEqual(
            heartbeats,
            Array.from({length: 3}, () => ({schema_version: 1, leases: []})),
        );
        assert(calls.indexOf("inspect") < calls.indexOf("credentials"));
        assert(calls.indexOf("credentials") < calls.indexOf("/runner/heartbeat"));
        assert(calls.indexOf("/runner/leases") < calls.indexOf("/runner/heartbeat"));
        assert.equal(calls.filter((x) => x === "/runner/claims").length, canExecute ? 3 : 0);
        journal.close();
    });
}

test("idle heartbeat failure prevents claims and permits the next bounded poll", async () => {
    const journal = new Journal(mkdtempSync(join(tmpdir(), "grow-idle-test-")));
    const transport = new Transport("http://localhost", journal, () => "access");
    let heartbeats = 0,
        claims = 0;
    transport.request = async (route) => {
        if (route === "/runner/leases") return {leases: []};
        if (route === "/runner/heartbeat") {
            if (++heartbeats === 1) throw new TransportError("transient");
            return {leases: []};
        }
        if (route === "/runner/claims") {
            claims++;
            return {attempt: null};
        }
        throw Error(route);
    };
    const coordinator = new Coordinator(
        journal,
        transport,
        {inspect: async () => [], canExecute: () => true} as Supervisor,
        "runner",
        {assertRuntime: () => {}},
    );
    await assert.rejects(() => coordinator.tick(), TransportError);
    assert.equal(claims, 0);
    await coordinator.tick();
    assert.equal(heartbeats, 2);
    assert.equal(claims, 1);
    journal.close();
});

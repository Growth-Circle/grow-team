import os
import tempfile
import time

from zerver.lib.test_classes import ZulipTestCase
from zerver.openapi.openapi import OpenAPISpec

BASE_SPEC = """\
openapi: 3.0.1
info:
  title: Fragment test spec
  version: "1.0.0"
paths:
  /events:
    get:
      operationId: get-events
      responses:
        "200":
          description: Success.
          content:
            application/json:
              schema:
                allOf:
                  - type: object
                  - type: object
                    properties:
                      events:
                        type: array
                        items:
                          oneOf:
                            - type: object
                              properties:
                                type:
                                  type: string
                                  enum:
                                    - heartbeat
                              additionalProperties: false
                              example:
                                {"type": "heartbeat", "id": 0}
"""


def fragment_yaml(*, path: str = "", schema: str = "", events: str = "") -> str:
    body = f"paths:\n{path}" if path else "paths: {}\n"
    if schema:
        body += f"components:\n  schemas:\n{schema}"
    if events:
        body += f"events:\n{events}"
    return body


class OpenAPIFragmentTest(ZulipTestCase):
    """Tests for the per-area OpenAPI fragment loader (Sanji WP04).

    Each test builds an isolated spec directory (a `zulip.yaml` plus a
    sibling `features/` directory) so that these tests never touch the
    real specification.
    """

    def make_spec_dir(self, tmp_dir: str, fragments: dict[str, str]) -> str:
        openapi_path = os.path.join(tmp_dir, "zulip.yaml")
        with open(openapi_path, "w") as f:
            f.write(BASE_SPEC)
        features_dir = os.path.join(tmp_dir, "features")
        os.makedirs(features_dir, exist_ok=True)
        for name, content in fragments.items():
            with open(os.path.join(features_dir, name), "w") as f:
                f.write(content)
        return openapi_path

    def test_fragment_merges_new_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            openapi_path = self.make_spec_dir(
                tmp_dir,
                {
                    "rooms.yaml": fragment_yaml(
                        path=(
                            "  /rooms/example:\n"
                            "    get:\n"
                            "      operationId: rooms-example\n"
                            "      responses:\n"
                            '        "200":\n'
                            "          description: OK.\n"
                            "          content:\n"
                            "            application/json:\n"
                            "              schema:\n"
                            "                type: object\n"
                        )
                    )
                },
            )
            spec = OpenAPISpec(openapi_path).openapi()
            self.assertIn("/rooms/example", spec["paths"])
            self.assertIn("/events", spec["paths"])

    def test_duplicate_path_across_fragments_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            duplicate_path = (
                "  /rooms/example:\n"
                "    get:\n"
                "      operationId: rooms-example\n"
                "      responses:\n"
                '        "200":\n'
                "          description: OK.\n"
                "          content:\n"
                "            application/json:\n"
                "              schema:\n"
                "                type: object\n"
            )
            openapi_path = self.make_spec_dir(
                tmp_dir,
                {
                    "a_rooms.yaml": fragment_yaml(path=duplicate_path),
                    "b_needs.yaml": fragment_yaml(path=duplicate_path),
                },
            )
            with self.assertRaisesRegex(AssertionError, "Duplicate OpenAPI path"):
                OpenAPISpec(openapi_path).openapi()

    def test_duplicate_schema_across_fragments_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            duplicate_schema = "    Widget:\n      type: object\n"
            openapi_path = self.make_spec_dir(
                tmp_dir,
                {
                    "a_rooms.yaml": fragment_yaml(schema=duplicate_schema),
                    "b_needs.yaml": fragment_yaml(schema=duplicate_schema),
                },
            )
            with self.assertRaisesRegex(AssertionError, "Duplicate OpenAPI schema"):
                OpenAPISpec(openapi_path).openapi()

    def test_events_are_added_to_get_events_oneof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            openapi_path = self.make_spec_dir(
                tmp_dir,
                {
                    "events_example.yaml": fragment_yaml(
                        events=(
                            "  - type: object\n"
                            "    properties:\n"
                            "      type:\n"
                            "        type: string\n"
                            "        enum:\n"
                            "          - sanji_test_event\n"
                            "    additionalProperties: false\n"
                            '    example: {"type": "sanji_test_event", "id": 0}\n'
                        )
                    )
                },
            )
            spec = OpenAPISpec(openapi_path).openapi()
            one_of = spec["paths"]["/events"]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]["properties"]["events"]["items"]["oneOf"]
            self.assertEqual(len(one_of), 2)
            enums = [entry["properties"]["type"]["enum"][0] for entry in one_of]
            self.assertEqual(enums, ["heartbeat", "sanji_test_event"])

    def test_fragment_mtime_change_triggers_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            fragment_path_on_disk = os.path.join(tmp_dir, "features", "rooms.yaml")
            openapi_path = self.make_spec_dir(tmp_dir, {"rooms.yaml": fragment_yaml()})
            spec = OpenAPISpec(openapi_path)
            first = spec.openapi()
            self.assertNotIn("/rooms/added-later", first["paths"])

            with open(fragment_path_on_disk, "w") as f:
                f.write(
                    fragment_yaml(
                        path=(
                            "  /rooms/added-later:\n"
                            "    get:\n"
                            "      operationId: rooms-added-later\n"
                            "      responses:\n"
                            '        "200":\n'
                            "          description: OK.\n"
                            "          content:\n"
                            "            application/json:\n"
                            "              schema:\n"
                            "                type: object\n"
                        )
                    )
                )
            # Ensure the new mtime is strictly newer even on filesystems with
            # coarse mtime resolution.
            future = time.time() + 5
            os.utime(fragment_path_on_disk, (future, future))

            second = spec.openapi()
            self.assertIn("/rooms/added-later", second["paths"])

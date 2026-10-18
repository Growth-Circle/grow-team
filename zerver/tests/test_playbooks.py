from typing import Any

from typing_extensions import override

from zerver.actions.playbooks import do_create_playbook
from zerver.lib.event_schema import check_playbook_add, check_playbook_update
from zerver.lib.playbooks import get_latest_playbook_content, strip_html_comments
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Playbook, PlaybookRevision, UserProfile


class PlaybookTestCase(ZulipTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.hamlet = self.example_user("hamlet")
        self.cordelia = self.example_user("cordelia")
        self.iago = self.example_user("iago")
        self.guest = self.example_user("polonius")

    def create(
        self,
        user: UserProfile,
        *,
        title: str = "Weekly report",
        content: str = "Collect the numbers.",
        scope: str = "shared",
    ) -> Playbook:
        return do_create_playbook(user_profile=user, title=title, content=content, scope=scope)

    def edit(self, user: UserProfile, playbook: Playbook, **params: Any) -> Any:
        self.login_user(user)
        return self.client_patch(f"/json/playbooks/{playbook.id}", params)


class PlaybookAccessTest(PlaybookTestCase):
    def test_shared_playbook_is_readable_and_editable_by_every_non_guest_member(self) -> None:
        playbook = self.create(self.hamlet)
        for user in [self.hamlet, self.cordelia, self.iago]:
            self.login_user(user)
            data = self.assert_json_success(self.client_get(f"/json/playbooks/{playbook.id}"))
            self.assertTrue(data["playbook"]["can_edit"])

        result = self.edit(self.cordelia, playbook, base_revision=1, content="New steps.")
        self.assert_json_success(result)

    def test_personal_playbook_is_for_its_owner_only(self) -> None:
        playbook = self.create(self.hamlet, scope="personal")

        self.login_user(self.hamlet)
        self.assert_json_success(self.client_get(f"/json/playbooks/{playbook.id}"))

        # An administrator gets the answer that a missing id gets.
        for user in [self.cordelia, self.iago]:
            self.login_user(user)
            self.assert_json_error(
                self.client_get(f"/json/playbooks/{playbook.id}"), "Playbook does not exist."
            )
            self.assert_json_error(
                self.client_get(f"/json/playbooks/{playbook.id}/revisions/1"),
                "Playbook does not exist.",
            )
            self.assert_json_error(
                self.client_post(f"/json/playbooks/{playbook.id}/archive"),
                "Playbook does not exist.",
            )
            self.assert_json_error(
                self.edit(user, playbook, base_revision=1, content="Mine now."),
                "Playbook does not exist.",
            )
            data = self.assert_json_success(self.client_get("/json/playbooks"))
            self.assertEqual(data["playbooks"], [])

        playbook.refresh_from_db()
        self.assertEqual(playbook.revisions.count(), 1)
        self.assertFalse(playbook.archived)

    def test_guest_has_no_access(self) -> None:
        playbook = self.create(self.hamlet)
        self.login_user(self.guest)

        message = "Guests cannot use playbooks."
        self.assert_json_error(self.client_get("/json/playbooks"), message)
        self.assert_json_error(self.client_get(f"/json/playbooks/{playbook.id}"), message)
        self.assert_json_error(
            self.client_post("/json/playbooks", {"title": "Mine", "content": "x"}),
            "You do not have permission to create a playbook.",
        )
        self.assert_json_error(self.edit(self.guest, playbook, base_revision=1, content="x"), message)
        self.assert_json_error(self.client_post(f"/json/playbooks/{playbook.id}/archive"), message)
        self.assertEqual(playbook.revisions.count(), 1)

    def test_bot_reads_but_does_not_write(self) -> None:
        playbook = self.create(self.hamlet)
        bot = self.create_test_bot("playbookreader", self.hamlet)

        result = self.api_get(bot, f"/api/v1/playbooks/{playbook.id}")
        data = self.assert_json_success(result)
        self.assertFalse(data["playbook"]["can_edit"])
        self.assertEqual(get_latest_playbook_content(bot, playbook.id), "Collect the numbers.")

        result = self.api_patch(
            bot, f"/api/v1/playbooks/{playbook.id}", {"base_revision": 1, "content": "Bot steps."}
        )
        self.assert_json_error(result, "You do not have permission to change this playbook.")
        result = self.api_post(bot, "/api/v1/playbooks", {"title": "Bot", "content": "x"})
        self.assert_json_error(result, "You do not have permission to create a playbook.")

    def test_archive_is_for_administrators_and_the_member_who_made_it(self) -> None:
        playbook = self.create(self.hamlet)

        self.login_user(self.cordelia)
        self.assert_json_error(
            self.client_post(f"/json/playbooks/{playbook.id}/archive"),
            "Only an administrator or the member who made a shared playbook can archive it.",
        )
        data = self.assert_json_success(self.client_get(f"/json/playbooks/{playbook.id}"))
        self.assertFalse(data["playbook"]["can_archive"])

        self.login_user(self.iago)
        data = self.assert_json_success(self.client_post(f"/json/playbooks/{playbook.id}/archive"))
        self.assertTrue(data["playbook"]["archived"])
        self.assert_json_success(self.client_post(f"/json/playbooks/{playbook.id}/unarchive"))

        self.login_user(self.hamlet)
        self.assert_json_success(self.client_post(f"/json/playbooks/{playbook.id}/archive"))

        personal = self.create(self.cordelia, scope="personal")
        self.login_user(self.cordelia)
        self.assert_json_success(self.client_post(f"/json/playbooks/{personal.id}/archive"))

    def test_playbook_of_another_realm_does_not_exist(self) -> None:
        playbook = self.create(self.hamlet)
        self.login_user(self.mit_user("sipbtest"))
        self.assert_json_error(
            self.client_get(f"/json/playbooks/{playbook.id}", subdomain="zephyr"),
            "Playbook does not exist.",
        )


class PlaybookRevisionTest(PlaybookTestCase):
    def test_create_makes_revision_one_and_points_at_it(self) -> None:
        self.login_user(self.hamlet)
        result = self.client_post(
            "/json/playbooks",
            {"title": "  Weekly **report**  ", "content": "# Steps\n\n1. Collect", "summary": "First"},
        )
        playbook_data = self.assert_json_success(result)["playbook"]

        self.assertEqual(playbook_data["title"], "Weekly **report**")
        self.assertEqual(playbook_data["slug"], "weekly-report")
        self.assertEqual(playbook_data["scope"], "shared")
        self.assertEqual(playbook_data["owner_id"], self.hamlet.id)
        revision = playbook_data["latest_revision"]
        self.assertEqual(revision["number"], 1)
        self.assertEqual(revision["author_id"], self.hamlet.id)
        self.assertEqual(revision["summary"], "First")
        self.assertIn("<h1", revision["rendered_content"])
        self.assertIn("<ol>", revision["rendered_content"])

        playbook = Playbook.objects.get(id=playbook_data["id"])
        assert playbook.latest_revision is not None
        self.assertEqual(playbook.latest_revision.number, 1)

    def test_slug_is_unique_in_the_shared_list_and_per_owner_among_personal(self) -> None:
        first = self.create(self.hamlet, title="Close the books")
        second = self.create(self.cordelia, title="Close the books")
        third = self.create(self.cordelia, title="Close the books!")
        self.assertEqual([first.slug, second.slug, third.slug], ["close-the-books"] + [
            "close-the-books-2",
            "close-the-books-3",
        ])

        # Another member's personal playbook never changes the slug.
        mine = self.create(self.hamlet, title="Close the books", scope="personal")
        theirs = self.create(self.cordelia, title="Close the books", scope="personal")
        again = self.create(self.cordelia, title="Close the books", scope="personal")
        self.assertEqual([mine.slug, theirs.slug, again.slug], ["close-the-books"] * 2 + ["close-the-books-2"])

        untitled = self.create(self.hamlet, title="???")
        self.assertEqual(untitled.slug, "playbook")

    def test_edit_adds_a_revision_and_keeps_the_old_one(self) -> None:
        playbook = self.create(self.hamlet)

        result = self.edit(
            self.cordelia, playbook, base_revision=1, content="Collect the *new* numbers.", summary="Fix"
        )
        data = self.assert_json_success(result)["playbook"]

        revision = data["latest_revision"]
        self.assertEqual(revision["number"], 2)
        self.assertEqual(revision["author_id"], self.cordelia.id)
        self.assertEqual(revision["summary"], "Fix")
        self.assertEqual(revision["content"], "Collect the *new* numbers.")
        self.assertEqual(data["owner_id"], self.hamlet.id)
        self.assertEqual(
            list(PlaybookRevision.objects.filter(playbook=playbook).values_list("number", "content")),
            [(1, "Collect the numbers."), (2, "Collect the *new* numbers.")],
        )

        # Agents get the latest revision.
        self.assertEqual(
            get_latest_playbook_content(self.hamlet, playbook.id), "Collect the *new* numbers."
        )

        result = self.client_get(f"/json/playbooks/{playbook.id}")
        listing = self.assert_json_success(result)["revisions"]
        self.assertEqual([item["number"] for item in listing], [2, 1])
        self.assertNotIn("content", listing[0])

        result = self.client_get(f"/json/playbooks/{playbook.id}/revisions/1")
        self.assertEqual(
            self.assert_json_success(result)["revision"]["content"], "Collect the numbers."
        )
        result = self.client_get(f"/json/playbooks/{playbook.id}/revisions/9")
        self.assert_json_error(result, "Revision does not exist.")

    def test_edit_from_an_old_revision_is_a_conflict(self) -> None:
        playbook = self.create(self.hamlet)
        self.assert_json_success(self.edit(self.hamlet, playbook, base_revision=1, content="A"))

        result = self.edit(self.cordelia, playbook, base_revision=1, content="B")
        self.assert_json_error_contains(
            result, "Someone saved a newer revision", status_code=409
        )
        self.assertEqual(result.status_code, 409)
        error = result.json()
        self.assertEqual(error["code"], "PLAYBOOK_REVISION_CONFLICT")
        self.assertEqual(error["latest_revision"], 2)

        self.assertEqual(playbook.revisions.count(), 2)
        self.assertEqual(get_latest_playbook_content(self.hamlet, playbook.id), "A")

        result = self.edit(self.cordelia, playbook, base_revision=2, content="B")
        self.assert_json_success(result)

    def test_edit_that_changes_nothing_adds_no_revision_and_no_event(self) -> None:
        playbook = self.create(self.hamlet)
        with self.capture_send_event_calls(expected_num_events=0):
            result = self.edit(
                self.hamlet, playbook, base_revision=1, content="Collect the numbers.", title="Weekly report"
            )
        self.assertEqual(self.assert_json_success(result)["playbook"]["latest_revision"]["number"], 1)
        self.assertEqual(playbook.revisions.count(), 1)

    def test_rename_changes_the_title_but_not_the_slug_or_revisions(self) -> None:
        playbook = self.create(self.hamlet)
        result = self.edit(self.hamlet, playbook, base_revision=1, title="Monthly report")
        data = self.assert_json_success(result)["playbook"]
        self.assertEqual(data["title"], "Monthly report")
        self.assertEqual(data["slug"], "weekly-report")
        self.assertEqual(data["latest_revision"]["number"], 1)

    def test_content_and_summary_limits(self) -> None:
        self.login_user(self.hamlet)
        result = self.client_post(
            "/json/playbooks", {"title": "Big", "content": "x" * (PlaybookRevision.MAX_CONTENT_LENGTH + 1)}
        )
        self.assert_json_error(result, "content is too long (limit: 100000 characters)")
        result = self.client_post("/json/playbooks", {"title": "  ", "content": "x"})
        self.assert_json_error(result, "title cannot be blank")
        result = self.client_post("/json/playbooks", {"title": "x" * 101, "content": "x"})
        self.assert_json_error(result, "title is too long (limit: 100 characters)")
        self.assertFalse(Playbook.objects.exists())

        result = self.client_post(
            "/json/playbooks", {"title": "Long", "content": "word " * 19_000}
        )
        self.assert_json_success(result)

    def test_diff_between_two_revisions(self) -> None:
        playbook = self.create(self.hamlet, content="One\nTwo\nThree")
        self.assert_json_success(
            self.edit(self.hamlet, playbook, base_revision=1, content="One\nTwo and a half\nThree")
        )

        result = self.client_get(
            f"/json/playbooks/{playbook.id}/diff", {"from_revision": 1, "to_revision": 2}
        )
        data = self.assert_json_success(result)
        self.assertEqual(
            data["diff"].splitlines(),
            [
                "--- revision 1",
                "+++ revision 2",
                "@@ -1,3 +1,3 @@",
                " One",
                "-Two",
                "+Two and a half",
                " Three",
            ],
        )
        self.assertEqual((data["from_revision"], data["to_revision"]), (1, 2))

        result = self.client_get(
            f"/json/playbooks/{playbook.id}/diff", {"from_revision": 1, "to_revision": 1}
        )
        self.assertEqual(self.assert_json_success(result)["diff"], "")
        result = self.client_get(
            f"/json/playbooks/{playbook.id}/diff", {"from_revision": 1, "to_revision": 5}
        )
        self.assert_json_error(result, "Revision does not exist.")


class PlaybookListTest(PlaybookTestCase):
    def test_list_filters_by_scope_search_and_archive(self) -> None:
        shared = self.create(self.hamlet, title="Evaluasi 4 jam", content="Cek kartu terhambat.")
        mine = self.create(self.cordelia, title="My checklist", content="Evaluasi tiap pagi.", scope="personal")
        others = self.create(self.hamlet, title="Private", content="x", scope="personal")
        self.login_user(self.cordelia)

        def ids(**params: Any) -> list[int]:
            data = self.assert_json_success(self.client_get("/json/playbooks", params))
            return [item["id"] for item in data["playbooks"]]

        self.assertEqual(set(ids()), {shared.id, mine.id})
        self.assertNotIn(others.id, ids())
        self.assertEqual(ids(scope="shared"), [shared.id])
        self.assertEqual(ids(scope="personal"), [mine.id])
        self.assertEqual(set(ids(search="evaluasi")), {shared.id, mine.id})
        self.assertEqual(ids(search="  TERHAMBAT "), [shared.id])
        self.assertEqual(ids(search="nothing"), [])

        self.assert_json_success(self.client_post(f"/json/playbooks/{mine.id}/archive"))
        self.assertEqual(ids(), [shared.id])
        self.assertEqual(ids(archived="true"), [mine.id])

    def test_list_puts_the_most_recently_changed_first(self) -> None:
        first = self.create(self.hamlet, title="First")
        second = self.create(self.hamlet, title="Second")
        self.assert_json_success(self.edit(self.hamlet, first, base_revision=1, content="Changed"))
        self.login_user(self.hamlet)
        data = self.assert_json_success(self.client_get("/json/playbooks"))
        self.assertEqual([item["id"] for item in data["playbooks"]], [first.id, second.id])
        self.assertNotIn("content", data["playbooks"][0]["latest_revision"])


class PlaybookEventTest(PlaybookTestCase):
    def test_shared_events_reach_every_non_guest_and_personal_ones_the_owner(self) -> None:
        self.login_user(self.hamlet)
        with self.capture_send_event_calls(expected_num_events=1) as events:
            result = self.client_post("/json/playbooks", {"title": "Shared", "content": "x"})
        playbook_id = self.assert_json_success(result)["playbook"]["id"]
        check_playbook_add("events[0]", {**events[0]["event"], "id": 0})
        users = set(events[0]["users"])
        self.assertIn(self.cordelia.id, users)
        self.assertNotIn(self.guest.id, users)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(
                self.client_patch(
                    f"/json/playbooks/{playbook_id}", {"base_revision": 1, "content": "y"}
                )
            )
        check_playbook_update("events[0]", {**events[0]["event"], "id": 0})
        self.assertEqual(events[0]["event"]["playbook"]["latest_revision"]["number"], 2)
        self.assertEqual(set(events[0]["users"]), users)

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.assert_json_success(self.client_post(f"/json/playbooks/{playbook_id}/archive"))
        check_playbook_update("events[0]", {**events[0]["event"], "id": 0})
        self.assertTrue(events[0]["event"]["playbook"]["archived"])

        with self.capture_send_event_calls(expected_num_events=1) as events:
            self.client_post("/json/playbooks", {"title": "Mine", "content": "x", "scope": "personal"})
        self.assertEqual(events[0]["users"], [self.hamlet.id])

    def test_failed_edit_sends_no_event(self) -> None:
        playbook = self.create(self.hamlet)
        with self.capture_send_event_calls(expected_num_events=0):
            result = self.edit(self.cordelia, playbook, base_revision=7, content="B")
        self.assertEqual(result.status_code, 409)


class PlaybookRenderTest(PlaybookTestCase):
    def rendered(self, content: str) -> str:
        playbook = self.create(self.hamlet, content=content)
        assert playbook.latest_revision is not None
        return playbook.latest_revision.rendered_content

    def test_html_comments_are_not_shown_but_stay_in_the_source(self) -> None:
        content = "Intro\n\n<!-- note for authors -->\nBody <!-- inline --> text\n\n<!--\nmulti\nline\n-->\nEnd"
        playbook = self.create(self.hamlet, content=content)
        assert playbook.latest_revision is not None
        rendered = playbook.latest_revision.rendered_content
        self.assertNotIn("note for authors", rendered)
        self.assertNotIn("inline", rendered)
        self.assertNotIn("multi", rendered)
        self.assertNotIn("&lt;!--", rendered)
        self.assertIn("Body  text", rendered)
        self.assertIn("End", rendered)
        self.assertEqual(playbook.latest_revision.content, content)

    def test_strip_html_comments_keeps_code(self) -> None:
        self.assertEqual(strip_html_comments("a <!-- b --> c"), "a  c")
        self.assertEqual(strip_html_comments("a <!-- never closed\nb"), "a ")
        self.assertEqual(strip_html_comments("keep `<!-- x -->` here"), "keep `<!-- x -->` here")
        self.assertEqual(
            strip_html_comments("```html\n<!-- keep -->\n```\n<!-- drop -->\nafter"),
            "```html\n<!-- keep -->\n```\n\nafter",
        )
        self.assertEqual(
            strip_html_comments("~~~\n```\n<!-- keep -->\n~~~\n<!-- drop -->"),
            "~~~\n```\n<!-- keep -->\n~~~\n",
        )
        # An unclosed comment before a fence does not eat the code.
        self.assertEqual(
            strip_html_comments("<!-- open\n```\n<!-- keep -->\n```"),
            "```\n<!-- keep -->\n```",
        )

    def test_raw_html_is_never_injected(self) -> None:
        rendered = self.rendered(
            '<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[x](javascript:alert(1))'
        )
        self.assertNotIn("<script", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn('href="javascript', rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_document_syntax_renders(self) -> None:
        rendered = self.rendered(
            "# Title\n\n## Part\n\n- one\n- two\n\n```python\nprint(1)\n```\n\n"
            "| a | b |\n|---|---|\n| 1 | 2 |\n\n[link](https://example.com)\n"
        )
        for fragment in ["<h1", "<h2", "<ul>", "<table>", 'href="https://example.com"', "codehilite"]:
            self.assertIn(fragment, rendered)

    def test_a_very_long_document_renders(self) -> None:
        content = "\n".join(f"- step {number} with **bold** text" for number in range(2500))
        self.assertLessEqual(len(content), PlaybookRevision.MAX_CONTENT_LENGTH)
        rendered = self.rendered(content)
        self.assertIn("step 2499", rendered)

    def test_preview_renders_like_a_playbook_and_saves_nothing(self) -> None:
        self.login_user(self.hamlet)
        result = self.client_post(
            "/json/playbooks/render", {"content": "# Title\n<!-- hidden -->\n<b>raw</b>"}
        )
        rendered = self.assert_json_success(result)["rendered_content"]
        self.assertIn("<h1", rendered)
        self.assertNotIn("hidden", rendered)
        self.assertNotIn("<b>", rendered)
        self.assertFalse(Playbook.objects.exists())

        self.login_user(self.guest)
        result = self.client_post("/json/playbooks/render", {"content": "x"})
        self.assert_json_error(result, "Guests cannot use playbooks.")

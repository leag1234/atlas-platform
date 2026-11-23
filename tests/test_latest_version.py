"""A latest-version answer names the newest version the pages read give.

Staging -176: an announcement of maintenance releases (older branches) was
read as "the latest version" while the project's download page gave a newer
one. Fictional product and sites only.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from services.orchestrator import latest_version as lv
from services.orchestrator import source_truth
from services.orchestrator.chat_pipeline import ChatTools, process
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import Call

ANNOUNCEMENT_URL = "https://blog.quillmark.example/2026/10/maintenance-releases/"
DOWNLOADS_URL = "https://www.quillmark.example/downloads/"
ONE_RELEASE_URL = "https://www.quillmark.example/downloads/release/quillmark-4138/"
ANNOUNCEMENT = (
    "Quillmark 4.10.22, 4.11.17, 4.12.15, 4.13.16 and 4.14.8 are now available! "
    "Quillmark 4.13.16 is the last full maintenance release of 4.13; future 4.13 "
    "releases are security only. Quillmark 4.14.8 is a maintenance release."
)
DOWNLOADS = (
    "Download the latest version of Quillmark. Download Quillmark 4.15.2. "
    "Want to help test development versions of Quillmark 4.16? Pre-releases. "
    "Active releases: Quillmark 4.15.2 Oct. 14, 2026 Download Release notes. "
    "Quillmark 4.14.10 Oct. 10, 2026 Download Release notes. "
    "Quillmark 4.16.0a2 Oct. 1, 2026. Release schedules: Quillmark 4.16 release "
    "schedule, Quillmark 4.15 release schedule. Inkwell 9.3.1 is also listed."
)
QUESTIONS = {
    "en": "What is the latest stable version of Quillmark?",
    "fr": "Quelle est la dernière version stable de Quillmark ?",
    "de": "Was ist die neueste stabile Version von Quillmark?",
    "es": "¿Cuál es la última versión estable de Quillmark?",
    "it": "Qual è l'ultima versione stabile di Quillmark?",
}
OLD_ANSWERS = {
    "en": "The latest stable version of Quillmark is **Quillmark 4.14.8**, "
    "released in October 2026.",
    "fr": "La dernière version stable de Quillmark est **Quillmark 4.14.8**, "
    "sortie en octobre 2026 (la future 4.16 est en développement).",
    "de": "Die neueste stabile Version von Quillmark ist Quillmark 4.14.8.",
    "es": "La última versión estable de Quillmark es Quillmark 4.14.8.",
    "it": "L'ultima versione stabile di Quillmark è Quillmark 4.14.8.",
}
NEWER = {
    "en": "newer version of Quillmark: 4.15.2",
    "fr": "plus récente de Quillmark : 4.15.2",
    "de": "neuere Version von Quillmark: 4.15.2",
    "es": "más reciente de Quillmark: 4.15.2",
    "it": "più recente di Quillmark: 4.15.2",
}
BOTH = {ANNOUNCEMENT_URL: [ANNOUNCEMENT], DOWNLOADS_URL: [DOWNLOADS]}


class QuestionTests(unittest.TestCase):
    def test_latest_version_questions_in_five_languages(self) -> None:
        for language, question in QUESTIONS.items():
            with self.subTest(language=language):
                self.assertTrue(lv.checked(question))
                self.assertEqual(lv.subject(question), ["quillmark"])

    def test_other_questions_are_not_checked(self) -> None:
        for question in (
            "Who wrote Quillmark?",
            "Quelle est la capitale de la Norvège ?",
            "What is the latest Quillmark 4.13 release?",
            "What is the latest LTS version of Quillmark?",
            "Quelle est la dernière bêta de Quillmark ?".replace("ê", "e"),
        ):
            with self.subTest(question=question):
                self.assertFalse(lv.checked(question))


class VersionTests(unittest.TestCase):
    def test_a_list_after_the_name_belongs_to_it(self) -> None:
        found = [v.text for v in lv.versions(ANNOUNCEMENT, ["quillmark"])]
        self.assertEqual(
            found[:5], ["4.10.22", "4.11.17", "4.12.15", "4.13.16", "4.14.8"]
        )

    def test_pre_releases_future_and_other_products_are_left_out(self) -> None:
        found = {v.text for v in lv.versions(DOWNLOADS, ["quillmark"])}
        self.assertEqual(found, {"4.15.2", "4.14.10"})

    def test_dates_and_link_targets_are_no_versions(self) -> None:
        text = (
            "Quillmark 22.10.2026 notes, see [Quillmark](https://x.example/5.0.1/) "
            "and Quillmark v4.2."
        )
        self.assertEqual([v.text for v in lv.versions(text, ["quillmark"])], ["4.2"])

    def test_newest_is_semantic_not_textual(self) -> None:
        pages = {"https://a.example/": ["Quillmark 4.9.3, Quillmark 4.10.0"]}
        best = lv.newest(pages, ["quillmark"])
        assert best is not None
        self.assertEqual(best.text, "4.10.0")


class RankingTests(unittest.TestCase):
    def test_release_pages_come_first_index_before_one_release(self) -> None:
        data: dict[str, object] = {
            "results": [
                {"link": ANNOUNCEMENT_URL},
                {"link": "https://encyclopedia.example/wiki/Quillmark"},
                {"link": ONE_RELEASE_URL},
                {"link": DOWNLOADS_URL},
            ]
        }
        rows = lv.ranked(data, QUESTIONS["fr"])["results"]
        assert isinstance(rows, list)
        self.assertEqual(
            [row["link"] for row in rows],
            [
                DOWNLOADS_URL,
                ONE_RELEASE_URL,
                ANNOUNCEMENT_URL,
                "https://encyclopedia.example/wiki/Quillmark",
            ],
        )

    def test_other_questions_keep_the_engine_order(self) -> None:
        data: dict[str, object] = {
            "results": [{"link": ANNOUNCEMENT_URL}, {"link": DOWNLOADS_URL}]
        }
        self.assertIs(lv.ranked(data, "Who founded Quillmark?"), data)

    def test_a_downloads_page_of_another_site_is_not_the_project_s(self) -> None:
        data: dict[str, object] = {
            "results": [
                {"link": ANNOUNCEMENT_URL},
                {"link": "https://mirror.example/downloads/"},
            ]
        }
        self.assertIs(lv.ranked(data, QUESTIONS["en"]), data)


class HintAndNoteTests(unittest.TestCase):
    def test_hint_names_the_newest_and_its_page(self) -> None:
        hint = lv.hint(QUESTIONS["en"], BOTH)
        assert hint is not None
        self.assertEqual(hint["newest_stable_version_seen"], "4.15.2")
        self.assertEqual(hint["page"], DOWNLOADS_URL)

    def test_one_version_seen_gives_no_hint(self) -> None:
        pages = {DOWNLOADS_URL: ["Download Quillmark 4.15.2."]}
        self.assertIsNone(lv.hint(QUESTIONS["en"], pages))

    def test_an_older_version_gets_the_newer_one_and_its_page(self) -> None:
        for language, answer in OLD_ANSWERS.items():
            with self.subTest(language=language):
                note = lv.note(answer, QUESTIONS[language], BOTH, language)
                self.assertIn(NEWER[language], note)
                self.assertIn(f"]({DOWNLOADS_URL})", note)

    def test_the_newest_answer_is_unchanged(self) -> None:
        answer = (
            "The latest stable version of Quillmark is 4.15.2; the 4.14 branch "
            "got Quillmark 4.14.10."
        )
        self.assertEqual(lv.note(answer, QUESTIONS["en"], BOTH, "en"), "")

    def test_only_one_page_and_one_version_is_unchanged(self) -> None:
        pages = {ANNOUNCEMENT_URL: ["Quillmark 4.14.8 is now available."]}
        answer = "The latest version of Quillmark is 4.14.8."
        self.assertEqual(lv.note(answer, QUESTIONS["en"], pages, "en"), "")

    def test_a_shorter_form_of_the_newest_is_no_older_version(self) -> None:
        answer = "The latest version of Quillmark is Quillmark 4.15."
        self.assertEqual(lv.note(answer, QUESTIONS["en"], BOTH, "en"), "")

    def test_a_branch_question_is_unchanged(self) -> None:
        question = "What is the latest Quillmark 4.14 release?"
        answer = "The latest Quillmark 4.14 release is Quillmark 4.14.10."
        self.assertEqual(lv.note(answer, question, BOTH, "en"), "")


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_results_are_ranked_and_the_fetch_is_hinted(self) -> None:
        search = {
            "data": {
                "results": [
                    {
                        "link": ANNOUNCEMENT_URL,
                        "title": "Quillmark maintenance releases",
                        "snippet": ANNOUNCEMENT,
                    },
                    {
                        "link": DOWNLOADS_URL,
                        "title": "Download Quillmark",
                        "snippet": "Download Quillmark 4.15.2.",
                    },
                ]
            }
        }
        fetch = {"data": {"url": ANNOUNCEMENT_URL, "text": ANNOUNCEMENT}}
        upstream = AsyncMock(side_effect=[search, fetch])
        reading = source_truth.page_texts.set({})
        try:
            with (
                tempfile.TemporaryDirectory() as root,
                patch.dict(os.environ, {"ATLAS_WEB_CACHE": root + "/cache.sqlite"}),
                patch("services.orchestrator.tools.Runtime.execute", upstream),
            ):
                tools = ChatTools(Interaction(), QUESTIONS["fr"])
                searched = await tools.execute(
                    Call("1", "web_search", json.dumps({"query": "Quillmark"})), 5
                )
                fetched = await tools.execute(
                    Call("2", "web_fetch", json.dumps({"url": ANNOUNCEMENT_URL})), 5
                )
        finally:
            source_truth.page_texts.reset(reading)
        data = searched["data"]
        assert isinstance(data, dict)
        links = [row["link"] for row in data["results"]]
        self.assertEqual(links, [DOWNLOADS_URL, ANNOUNCEMENT_URL])
        hinted = searched["version_check"]
        assert isinstance(hinted, dict)
        self.assertEqual(hinted["newest_stable_version_seen"], "4.15.2")
        hinted = fetched["version_check"]
        assert isinstance(hinted, dict)
        self.assertEqual(hinted["page"], DOWNLOADS_URL)

    async def test_the_api_answer_states_the_newer_version(self) -> None:
        async def generate(request: ChatRequest, item: Interaction) -> None:
            source_truth.record_page(ANNOUNCEMENT_URL, ANNOUNCEMENT)
            source_truth.record_page(DOWNLOADS_URL, DOWNLOADS)
            item.retrieved_urls.extend([ANNOUNCEMENT_URL, DOWNLOADS_URL])
            item.reponse, item.state, item.cout_eur = OLD_ANSWERS["fr"], "done", 0.0

        request = ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": QUESTIONS["fr"]}]}
        )
        item = Interaction()
        with patch(
            "services.orchestrator.chat_pipeline._process_request",
            side_effect=generate,
        ):
            await process(request, item)
        self.assertIn(NEWER["fr"], item.reponse)
        self.assertTrue(item.reponse.startswith(OLD_ANSWERS["fr"]))

    async def test_an_answer_with_the_newest_version_is_unchanged(self) -> None:
        answer = "La dernière version stable de Quillmark est Quillmark 4.15.2."

        async def generate(request: ChatRequest, item: Interaction) -> None:
            source_truth.record_page(ANNOUNCEMENT_URL, ANNOUNCEMENT)
            source_truth.record_page(DOWNLOADS_URL, DOWNLOADS)
            item.reponse, item.state, item.cout_eur = answer, "done", 0.0

        request = ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": QUESTIONS["fr"]}]}
        )
        item = Interaction()
        with patch(
            "services.orchestrator.chat_pipeline._process_request",
            side_effect=generate,
        ):
            await process(request, item)
        self.assertEqual(item.reponse, answer)


if __name__ == "__main__":
    unittest.main()

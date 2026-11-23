"""Defect classes found by the generated class suite on staging -184.

Every case is fictional and written for this file; none comes from a
conversation. The classes:

- C13: the code-written sentences of a turn follow the conversation language
  when the client sends ui_locale and no lang (lang defaults to French).
- C16: code the person asks to run goes to Work, typos and German word order
  included, and whatever the routing model answered.
- C4: a service note is never repeated at one place, and the conversation's
  own app is a source of the labels it defines.
- C4: a pronoun with its contraction ("I'll") is never a detail to source.
"""

import unittest

from packages.work_intent import work_reason
from services.orchestrator import unsourced_details
from services.orchestrator.artifact_tools import footer
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.grounding import Grounding, wording
from services.orchestrator.request_router import Decision, settle


def request(messages: list[tuple[str, str]], **fields: object) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "messages": [{"role": role, "content": text} for role, text in messages],
            **fields,
        }
    )


class ProseLanguageTests(unittest.TestCase):
    CONVERSATIONS = (
        (
            "de",
            "Bau mir bitte eine kleine App, in der ich meine Wanderschuhe mit "
            "Größe und Kaufdatum eintragen kann.",
            "Kannst du noch einbauen, dass die App vor dem Löschen nachfragt?",
        ),
        (
            "es",
            "Hazme una pequeña aplicación para apuntar las macetas del balcón y "
            "los días de riego.",
            "¿Puedes añadir un botón para borrar todas las macetas?",
        ),
        (
            "it",
            "Fammi una piccola app per segnare i barattoli di marmellata e la "
            "data in cui li ho fatti.",
            "Puoi aggiungere un pulsante per svuotare la lista?",
        ),
        (
            "en",
            "Build me a small app to log the kites I own with their colour and "
            "the date I bought them.",
            "Can you add a confirmation before a kite is removed?",
        ),
    )

    def test_service_language_without_a_lang_field(self) -> None:
        for language, first, follow_up in self.CONVERSATIONS:
            for locale in ("en", "fr"):
                with self.subTest(language=language, locale=locale):
                    turn = request(
                        [
                            ("user", first),
                            ("assistant", footer("d" * 32, 1, "Log", "log.html", "en")),
                            ("user", follow_up),
                        ],
                        ui_locale=locale,
                    )
                    self.assertEqual(turn.prose_lang, language)

    def test_an_explicit_lang_still_wins_among_the_five(self) -> None:
        turn = request(
            [("user", "Kannst du mir bitte eine kleine Liste meiner Pflanzen bauen?")],
            lang="fr",
            ui_locale="en",
        )
        self.assertEqual(turn.prose_lang, "fr")


class RunRequestTests(unittest.TestCase):
    RUN = (
        "Kannst du den Python-Schnipsel hier bitte wirklich ausfüren und mir die "
        "Ausgabe zeigen?",
        "Kannst du den Python-Code bitte ausfuehren?",
        "Bitte das Skript einmal laufen lassen, ich will die Ausgabe sehen.",
        "can you excecute this python script and paste the output",
        "Peux-tu exécuter ce code python et me montrer les deux lignes ?",
        "¿Puedes ejecutar este script de python y enseñarme la salida?",
        "Puoi eseguire questo script e mostrarmi il risultato?",
    )
    NOT_RUN = (
        "Schreib mir ein Python-Skript, ausführlich kommentiert, das Dateien zählt.",
        "Mein Python-Programm ist langsam, woran liegt das?",
        "How do I execute a python script on a schedule?",
        "Explique-moi ce que fait ce code python.",
    )

    def test_requests_to_run_code_need_work(self) -> None:
        for text in self.RUN:
            with self.subTest(text=text):
                self.assertEqual(work_reason(text), "run")

    def test_questions_about_code_stay_in_the_chat(self) -> None:
        for text in self.NOT_RUN:
            with self.subTest(text=text):
                self.assertNotEqual(work_reason(text), "run")

    def test_the_router_answer_never_keeps_a_run_request_in_the_chat(self) -> None:
        for text in self.RUN:
            for kind in ("answer", "research"):
                for follow_up in (False, True):
                    with self.subTest(text=text, kind=kind, follow_up=follow_up):
                        routed = settle(
                            text,
                            Decision.model_validate(
                                {"kind": kind, "follow_up": follow_up}
                            ),
                        )
                        assert routed is not None
                        self.assertEqual(routed.kind, "run")
        for text in self.NOT_RUN:
            with self.subTest(text=text):
                routed = settle(text, Decision.model_validate({"kind": "answer"}))
                assert routed is not None
                self.assertEqual(routed.kind, "answer")


class GroundingNoteTests(unittest.TestCase):
    QUESTION = (
        "Can you check the app? Click “Add pear” twice, “Add plum” once, then "
        "“Clear basket”, and tell me the three counts."
    )
    ANSWER = (
        "I traced the code:\n\n"
        "- **Click “Add pear” twice:** pears becomes 2.\n"
        "- **Click “Add plum” once:** plums becomes 1.\n"
        "- **Click “Clear basket”:** all three go back to 0.\n\n"
        "So the app shows 2, 1, 3 and then 0, 0, 0."
    )

    def checker(self) -> Grounding:
        checker = Grounding()
        checker.add("Double click test: measure how fast you can click a mouse.")
        return checker

    def test_one_note_at_one_place(self) -> None:
        checked = self.checker().check(
            self.ANSWER, self.QUESTION, "en", messages=[self.QUESTION]
        )
        self.assertLessEqual(checked.count(wording("missing", "en")), 1)

    def test_the_conversation_app_defines_its_labels(self) -> None:
        delivered = (
            "Tap “Add pear” or “Add plum” to raise that count, and “Clear basket” "
            "to reset.\n\n" + footer("e" * 32, 1, "Fruit Basket", "fruit.html", "en")
        )
        checked = self.checker().check(
            self.ANSWER,
            self.QUESTION,
            "en",
            messages=[self.QUESTION],
            documents=[delivered],
        )
        self.assertNotIn(wording("missing", "en"), checked)
        self.assertEqual(checked, self.ANSWER)


class ContractionTests(unittest.TestCase):
    def test_pronoun_contractions_are_not_details(self) -> None:
        for text in (
            "If anything looks different, tell me and I'll dig in.",
            "Then We'd check it again on the phone.",
            "Click it twice; You'll see the total change.",
            "The total is right, so I Don't need to change it.",
        ):
            with self.subTest(text=text):
                names = [
                    detail.text
                    for detail in unsourced_details.extract(text)
                    if detail.kind == "name"
                ]
                self.assertFalse(
                    [name for name in names if "'" in name and " " not in name],
                    names,
                )

    def test_names_with_an_apostrophe_stay_names(self) -> None:
        names = [d.text for d in unsourced_details.extract("He met O'Brien in Galway.")]
        self.assertIn("O'Brien", names)


if __name__ == "__main__":
    unittest.main()

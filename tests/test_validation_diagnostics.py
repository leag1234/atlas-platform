"""Numeric validation errors disclose bounds without reflecting private inputs."""

import unittest
from pydantic import BaseModel, Field, ValidationError
from packages.validation import describe_validation


class Payload(BaseModel):
    text: str = Field(max_length=4)
    count: int = Field(le=8)


class ValidationDiagnosticsTests(unittest.TestCase):
    def test_bounds_without_contents(self) -> None:
        try:
            Payload(text="secret contents", count=9)
        except ValidationError as exc:
            detail = describe_validation(exc)
        self.assertIn("measured length 15", detail)
        self.assertIn("max_length 4", detail)
        self.assertIn("measured value 9", detail)
        self.assertIn("le 8", detail)
        self.assertNotIn("secret", detail)


class NamedFieldTests(unittest.TestCase):
    def test_a_length_names_its_field_and_a_request_rule_gives_its_reason(self) -> None:
        from pydantic import BaseModel, Field, ValidationError, model_validator

        from packages.validation import describe_validation as describe

        class Item(BaseModel):
            note: str = Field(max_length=5)

        class Body(BaseModel):
            items: list[Item]

            @model_validator(mode="after")
            def two_at_most(self) -> "Body":
                if len(self.items) > 2:
                    raise ValueError("at most two items per request")
                return self

        try:
            Body.model_validate({"items": [{"note": "lantern glow"}]})
        except ValidationError as error:
            detail = describe(error)
        self.assertIn("measured length 12 of items.0.note", detail)
        try:
            Body.model_validate(
                {"items": [{"note": "a"}, {"note": "b"}, {"note": "c"}]}
            )
        except ValidationError as error:
            detail = describe(error)
        self.assertIn("invalid request: at most two items per request", detail)

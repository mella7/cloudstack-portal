from __future__ import annotations

import pytest

from app.services.masking import Masker


def test_known_secret_values_are_replaced_anywhere():
    masker = Masker(["s3cr3t-value", "key-12345"])
    assert masker.mask("url?apiKey=key-12345&x=1 then s3cr3t-value again") == "url?apiKey=***&x=1 then *** again"


@pytest.mark.parametrize(
    "line,expected",
    [
        ('  + secret_key = "abc123"', '  + secret_key = "***"'),
        ("api_key: hunter2", "api_key: ***"),
        (
            "GET /client/api?command=x&signature=AbC%2Fd&response=json",
            "GET /client/api?command=x&signature=***&response=json",
        ),
        ('"password": "pw"', '"password": "***"'),
    ],
)
def test_secret_looking_assignments_are_masked_even_if_the_value_is_unknown(line, expected):
    assert Masker().mask(line) == expected


def test_ordinary_terraform_output_is_untouched():
    line = "module.vm.cloudstack_instance.this[0]: Creation complete after 14s [id=6f1c]"
    assert Masker(["s3cr3t-value"]).mask(line) == line

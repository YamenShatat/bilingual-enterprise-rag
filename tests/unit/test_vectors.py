import math

import pytest

from bilingual_rag.database.vectors import format_vector, parse_vector


class TestFormatVector:
    def test_renders_a_bracketed_comma_separated_list(self):
        assert format_vector([1, 0.5, -2.25]) == "[1.0,0.5,-2.25]"

    def test_accepts_any_sequence_of_numbers(self):
        assert format_vector((1, 2)) == "[1.0,2.0]"
        assert format_vector(range(3)) == "[0.0,1.0,2.0]"

    def test_keeps_scientific_notation_that_pgvector_accepts(self):
        assert format_vector([1e-7]) == "[1e-07]"

    def test_the_text_keeps_full_double_precision(self):
        assert format_vector([0.1]) == "[0.1]"
        assert parse_vector(format_vector([1 / 3])) == [1 / 3]

    def test_an_empty_vector_is_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            format_vector([])

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_non_finite_values_are_rejected(self, bad):
        with pytest.raises(ValueError, match="finite"):
            format_vector([1.0, bad])

    def test_a_non_number_is_rejected(self):
        with pytest.raises(ValueError):
            format_vector([1.0, "x"])


class TestParseVector:
    def test_reads_what_postgres_prints(self):
        assert parse_vector("[1,2.5,-3]") == [1.0, 2.5, -3.0]

    def test_tolerates_surrounding_whitespace(self):
        assert parse_vector("  [1,2]\n") == [1.0, 2.0]

    @pytest.mark.parametrize("bad", ["", "[]", "1,2", "(1,2)", "[1,2", "1,2]", "[a,b]", "[1,,2]"])
    def test_malformed_text_is_rejected(self, bad):
        with pytest.raises(ValueError):
            parse_vector(bad)

    def test_round_trips_a_format(self):
        values = [0.25, -1.5, 3.0, 1e-05]
        assert parse_vector(format_vector(values)) == values

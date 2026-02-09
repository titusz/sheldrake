"""Tests for the backtrack protocol parser."""

from pali.protocol import Backtrack, Checkpoint, SignalParser, TextChunk


def collect(parser: SignalParser, text: str) -> list:
    """Feed text and flush, returning all tokens."""
    tokens = parser.feed(text)
    tokens.extend(parser.flush())
    return tokens


def collect_types(parser: SignalParser, text: str) -> list[type]:
    """Feed text and return token types."""
    return [type(t) for t in collect(parser, text)]


# --- Checkpoint parsing ---


def test_parse_checkpoint():
    parser = SignalParser()
    tokens = collect(parser, "hello <<checkpoint:opening>> world")
    assert len(tokens) == 3
    assert tokens[0] == TextChunk(text="hello ")
    assert isinstance(tokens[1], Checkpoint)
    assert tokens[1].id == "opening"
    assert tokens[2] == TextChunk(text=" world")


def test_parse_checkpoint_with_descriptive_id():
    parser = SignalParser()
    tokens = collect(parser, "<<checkpoint:claim1>>")
    assert len(tokens) == 1
    assert isinstance(tokens[0], Checkpoint)
    assert tokens[0].id == "claim1"


# --- Backtrack parsing ---


def test_parse_backtrack_simple():
    parser = SignalParser()
    tokens = collect(parser, "<<backtrack:opening|too technical>>")
    assert len(tokens) == 1
    bt = tokens[0]
    assert isinstance(bt, Backtrack)
    assert bt.checkpoint_id == "opening"
    assert bt.reason == "too technical"
    assert bt.rephrase is None
    assert bt.mode is None


def test_parse_backtrack_with_rephrase():
    parser = SignalParser()
    tokens = collect(parser, "<<backtrack:opening|bad framing|rephrase:try analogy>>")
    bt = tokens[0]
    assert isinstance(bt, Backtrack)
    assert bt.reason == "bad framing"
    assert bt.rephrase == "try analogy"
    assert bt.mode is None


def test_parse_backtrack_with_mode():
    parser = SignalParser()
    tokens = collect(parser, "<<backtrack:opening|wrong tone|mode:precise>>")
    bt = tokens[0]
    assert isinstance(bt, Backtrack)
    assert bt.reason == "wrong tone"
    assert bt.rephrase is None
    assert bt.mode == "precise"


def test_parse_backtrack_with_rephrase_and_mode():
    parser = SignalParser()
    tokens = collect(
        parser,
        "<<backtrack:opening|overcomplicated|rephrase:simplify|mode:exploratory>>",
    )
    bt = tokens[0]
    assert isinstance(bt, Backtrack)
    assert bt.reason == "overcomplicated"
    assert bt.rephrase == "simplify"
    assert bt.mode == "exploratory"


# --- Streaming / chunked input ---


def test_signal_split_across_chunks():
    parser = SignalParser()
    all_tokens = []
    all_tokens.extend(parser.feed("hello <<check"))
    all_tokens.extend(parser.feed("point:opening"))
    all_tokens.extend(parser.feed(">> world"))
    all_tokens.extend(parser.flush())

    types = [type(t) for t in all_tokens]
    assert Checkpoint in types
    cp = next(t for t in all_tokens if isinstance(t, Checkpoint))
    assert cp.id == "opening"


def test_signal_split_at_delimiter():
    parser = SignalParser()
    all_tokens = []
    all_tokens.extend(parser.feed("text <"))
    all_tokens.extend(parser.feed("<checkpoint:x>>"))
    all_tokens.extend(parser.flush())

    types = [type(t) for t in all_tokens]
    assert Checkpoint in types


def test_single_char_at_a_time():
    """Feed one character at a time to stress the state machine."""
    parser = SignalParser()
    text = "hi <<checkpoint:a>> bye"
    all_tokens = []
    for ch in text:
        all_tokens.extend(parser.feed(ch))
    all_tokens.extend(parser.flush())

    types = [type(t) for t in all_tokens]
    assert Checkpoint in types
    # Combine text chunks
    combined = "".join(t.text for t in all_tokens if isinstance(t, TextChunk))
    assert combined == "hi  bye"


# --- Code collision / false positive avoidance ---


def test_cpp_shift_operator():
    """'<<' followed by non-tag content should be plain text."""
    parser = SignalParser()
    tokens = collect(parser, 'std::cout << "hello"')
    assert all(isinstance(t, TextChunk) for t in tokens)
    combined = "".join(t.text for t in tokens)
    assert combined == 'std::cout << "hello"'


def test_bitwise_shift():
    parser = SignalParser()
    tokens = collect(parser, "x << 1")
    combined = "".join(t.text for t in tokens)
    assert combined == "x << 1"


def test_double_angle_with_space():
    parser = SignalParser()
    tokens = collect(parser, "<< something >>")
    combined = "".join(t.text for t in tokens)
    assert combined == "<< something >>"


def test_single_angle_bracket():
    parser = SignalParser()
    tokens = collect(parser, "a < b and c > d")
    combined = "".join(t.text for t in tokens)
    assert combined == "a < b and c > d"


# --- Malformed signals ---


def test_malformed_checkpoint_no_id():
    parser = SignalParser()
    tokens = collect(parser, "<<checkpoint:>>")
    assert all(isinstance(t, TextChunk) for t in tokens)
    combined = "".join(t.text for t in tokens)
    assert combined == "<<checkpoint:>>"


def test_malformed_backtrack_no_reason():
    parser = SignalParser()
    tokens = collect(parser, "<<backtrack:opening>>")
    assert all(isinstance(t, TextChunk) for t in tokens)


def test_malformed_backtrack_empty_fields():
    parser = SignalParser()
    tokens = collect(parser, "<<backtrack:|>>")
    assert all(isinstance(t, TextChunk) for t in tokens)


# --- Max signal length ---


def test_max_signal_length_exceeded():
    """Signals exceeding MAX_SIGNAL_LENGTH are flushed as text."""
    parser = SignalParser()
    long_body = "backtrack:" + "a" * 600
    tokens = collect(parser, f"<<{long_body}>>")
    assert all(isinstance(t, TextChunk) for t in tokens)


# --- Nested / edge cases ---


def test_nested_angle_brackets():
    parser = SignalParser()
    tokens = collect(parser, "<a <<checkpoint:x>>")
    types = [type(t) for t in tokens]
    assert Checkpoint in types


def test_flush_incomplete_signal():
    """Incomplete signal at stream end should be flushed as text."""
    parser = SignalParser()
    tokens = parser.feed("hello <<checkpoint:unc")
    tokens.extend(parser.flush())
    combined = "".join(t.text for t in tokens if isinstance(t, TextChunk))
    assert "<<checkpoint:unc" in combined


def test_flush_in_maybe_open():
    parser = SignalParser()
    tokens = parser.feed("hello <")
    tokens.extend(parser.flush())
    combined = "".join(t.text for t in tokens if isinstance(t, TextChunk))
    assert combined == "hello <"


def test_multiple_signals_in_one_chunk():
    parser = SignalParser()
    tokens = collect(
        parser,
        "<<checkpoint:a>>text<<checkpoint:b>>more",
    )
    checkpoints = [t for t in tokens if isinstance(t, Checkpoint)]
    assert len(checkpoints) == 2
    assert checkpoints[0].id == "a"
    assert checkpoints[1].id == "b"


def test_checkpoint_then_backtrack():
    parser = SignalParser()
    tokens = collect(
        parser,
        "<<checkpoint:intro>>Hello world<<backtrack:intro|wrong tone>>",
    )
    types = [type(t) for t in tokens]
    assert Checkpoint in types
    assert Backtrack in types
    bt = next(t for t in tokens if isinstance(t, Backtrack))
    assert bt.checkpoint_id == "intro"
    assert bt.reason == "wrong tone"


def test_empty_input():
    parser = SignalParser()
    tokens = collect(parser, "")
    assert tokens == []


def test_plain_text_only():
    parser = SignalParser()
    tokens = collect(parser, "just plain text here")
    assert len(tokens) == 1
    assert tokens[0] == TextChunk(text="just plain text here")

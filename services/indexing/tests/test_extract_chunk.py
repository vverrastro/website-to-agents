from wta_indexing.chunking import chunk_hash, chunk_markdown, embedding_input
from wta_indexing.extract import content_hash, extract_page, normalize_text

PAGE = (
    "<!doctype html><html lang='IT'><head><title>  Acme   Pricing </title>"
    "<meta name='description' content='Plans and prices'>"
    "<link rel='canonical' href='https://acme.test/pricing'>"
    "<script>window.session='abc123'</script></head><body>"
    "<nav>Home Pricing Contact</nav><div class='cookie'>We use cookies to improve things</div>"
    "<article><h1>Pricing</h1><p>" + "Our plans are simple and fair. " * 12 + "</p>"
    "<h2>Enterprise</h2><p>" + "Contact sales for custom contracts. " * 12 + "</p></article>"
    "<footer>(c) Acme</footer></body></html>"
)


def test_extract_metadata_and_main_content() -> None:
    ex = extract_page(PAGE.encode(), "https://acme.test/pricing")
    assert ex is not None
    assert ex.title == "Acme Pricing"
    assert ex.description == "Plans and prices"
    assert ex.language == "it"
    assert ex.canonical_url == "https://acme.test/pricing"
    assert "Our plans are simple" in ex.text
    assert "Home Pricing Contact" not in ex.text and "window.session" not in ex.text
    assert "We use cookies" not in ex.text and "(c) Acme" not in ex.text
    assert ex.headings == ["Pricing", "Enterprise"]


def test_hash_is_stable_across_cosmetic_differences() -> None:
    a = extract_page(PAGE.encode(), "https://acme.test/p")
    b = extract_page(PAGE.replace("</p>", "</p>\r\n\r\n\r\n").encode(), "https://acme.test/p")
    assert a and b and a.content_hash == b.content_hash == content_hash(a.text)


def test_hash_changes_when_content_changes() -> None:
    a = extract_page(PAGE.encode(), "https://acme.test/p")
    b = extract_page(PAGE.replace("simple and fair", "complicated").encode(), "https://acme.test/p")
    assert a and b and a.content_hash != b.content_hash


def test_empty_or_shell_pages_return_none() -> None:
    assert extract_page(b"", "https://x.test/") is None
    assert (
        extract_page(b"<html><body><div id='root'></div></body></html>", "https://x.test/") is None
    )


def test_fallback_when_trafilatura_finds_nothing() -> None:
    html = "<html><body><h2>Tiny</h2><p>" + "Plain words only. " * 10 + "</p></body></html>"
    ex = extract_page(html, "https://x.test/")
    assert ex is not None and "Plain words only" in ex.text


def test_normalize_text() -> None:
    assert normalize_text("a​ b  \r\n\r\n\r\n\r\nc  ") == "a b\n\nc"


def test_chunks_follow_headings_with_paths() -> None:
    body = lambda w: (w + " lorem ipsum dolor sit amet. ") * 6  # noqa: E731
    text = f"# Guide\n\n{body('intro')}\n\n## Install\n\n{body('install')}\n\n### Linux\n\n{body('linux')}\n\n## Usage\n\n{body('usage')}"
    chunks = chunk_markdown(text, max_chars=600)
    paths = [c.heading_path for c in chunks]
    assert ("Guide",) in paths and ("Guide", "Install") in paths
    assert ("Guide", "Install", "Linux") in paths and ("Guide", "Usage") in paths
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= 600 for c in chunks)
    assert not any(line.startswith("#") for c in chunks for line in c.text.splitlines())


def test_long_section_is_split_with_overlap() -> None:
    sentences = [f"Sentence number {i} is here." for i in range(80)]
    text = "## Long\n\n" + " ".join(sentences)
    chunks = chunk_markdown(text, max_chars=400, overlap_chars=80)
    assert len(chunks) > 3 and all(len(c.text) <= 400 for c in chunks)
    assert all(c.heading_path == ("Long",) for c in chunks)
    joined = " ".join(c.text for c in chunks)
    assert all(s in joined for s in sentences)  # nothing lost
    shared = chunks[0].text.split()[-3:]
    assert all(w in chunks[1].text for w in shared)  # overlap carried over


def test_giant_unbroken_text_is_hard_sliced() -> None:
    chunks = chunk_markdown("x" * 5000, max_chars=1000, overlap_chars=0)
    assert len(chunks) == 5 and all(len(c.text) <= 1000 for c in chunks)


def test_tiny_sections_merge_instead_of_becoming_chunks() -> None:
    text = "## A\n\nshort\n\n## B\n\n" + "Body of section B. " * 10
    chunks = chunk_markdown(text, min_chars=80)
    assert len(chunks) == 1 and chunks[0].heading_path == ("B",) and "short" in chunks[0].text


def test_text_without_headings_and_empty() -> None:
    chunks = chunk_markdown("Just a paragraph. " * 20)
    assert len(chunks) == 1 and chunks[0].heading_path == ()
    assert chunk_markdown("") == []


def test_chunk_hash_and_embedding_input() -> None:
    assert chunk_hash(("A",), "t") == chunk_hash(("A",), "t") != chunk_hash(("B",), "t")
    assert embedding_input("Site", ("Docs", "API"), "body") == "Site > Docs > API\n\nbody"
    assert embedding_input(None, (), "body") == "body"

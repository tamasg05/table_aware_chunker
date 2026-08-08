# Table-aware data extraction and chunking

## Background
RAG applications need to split data into chunks and embed them. Sophisticated
chunkers usually respect paragraph or sentence boundaries, but they often
struggle with tables. If a table is split in the middle of a row or separated
from its headers, structural information is lost. In addition, a sequence of
values without the corresponding headers does not convey enough meaning to an
LLM when used as context. Conventional chunking can produce exactly this kind
of ambiguous sequence.

## Objective

Provide an easy-to-use library that automatically identifies tables, extracts
their data while preserving its structure, and produces meaningful chunks for
both text and tabular data.

### Extraction
If a data corpus contains tables, its content must be extracted in a table-aware
manner that preserves the relationship between each cell and its column.

Furthermore, the extraction process must detect and identify tables
automatically, including two tables positioned side by side on the same page.
Without requiring additional configuration, it must handle vertically oriented
headings, missing table borders or separator lines, and merged cells. The
library therefore needs a simple API that accepts a PDF document and returns a
structured JSON representation of the extracted data.

### Chunking
Chunking must preserve table rows, and each cell must be associated with its
column name so that an LLM can interpret the cell value when the chunk is used
as context.

A row should not normally be split: the whole row should be included in a
single chunk. If a row is too wide to fit within the specified chunk size, the
leading cell, interpreted as the row key, must be repeated in each chunk. The
remaining cells should be divided into consecutive sequences until the entire
row has been processed.

## Results
The result is an easy-to-use Python library tested on complex technical and
marketing brochures from the automotive industry.

The API provides two primary operations: data extraction and chunking.

The library can also process simpler HTML tables, but its primary focus is PDF
documents.

## Intro
For PDF input, the package uses `pdfplumber`, an MIT-licensed low-level Python
library for reading PDF text and layout geometry. This package extends that
foundation with the structure-reconstruction functionality documented in section
[How `pdfplumber` is used](#how-pdfplumber-is-used).

For HTML input, the package uses Beautiful Soup (`beautifulsoup4`), an
MIT-licensed Python library for parsing HTML. This package extends that
foundation with the semantic block and table processing documented in section
[How Beautiful Soup is used](#how-beautiful-soup-is-used).

The `data_extraction` package converts PDF documents or HTML pages
into a common structured representation and can then turn the extracted blocks
into chunks suitable for RAG applications. The common representation allows a
downstream RAG pipeline to process different source formats in the same way
while retaining useful structure such as headings, table rows, page numbers,
and source provenance.

**A central benefit is the structure-aware extraction of tables.** Instead of
flattening cells into an ambiguous sequence of text, the package reconstructs
headers, rows, columns, and their value relationships. When a table is rendered
into readable text or chunks, the appropriate column header is added to every
non-empty cell value to make its meaning explicit. For example, this row:

| Model | Power | Price |
| --- | --- | --- |
| A8 55 TFSI | 340 LE | 41256010 HUF |

is represented as:

```text
Model = A8 55 TFSI; Power = 340 LE; Price = 41256010 HUF.
```

This is much easier for retrieval and language models to interpret than
`A8 55 TFSI 340 LE 41256010 HUF`, where the role of each value is implicit.
Reliable table reconstruction is otherwise difficult, especially for PDFs
with rotated headings, merged visual rows, missing borders, or multiple tables
on one page.

Separating extraction from chunking and indexing also means that documents can
be parsed once, inspected, tested, and reused by different RAG systems without
repeating the potentially expensive or error-prone extraction step. PDF layout
interpretation is still heuristic, so new complex layouts should be covered by
regression fixtures before relying on them in production. In `blocks.json`,
headers and row cells remain stored separately; the explicit `column = value`
form is created during readable rendering and chunk construction.

## Installation

The project requires Python 3.10 or newer. For local development, create and
activate a virtual environment, then install the repository in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

The distribution is named `table-aware-chunker`, while the Python import name
remains `data_extraction` to preserve the package's original API.

To use a local clone from a sibling project, activate that project's virtual
environment and install this repository in editable mode. For example, from
the `ket_rag` directory:

```powershell
python -m pip install -e ..\table_aware_chunker
```

The consuming project can then import directly from `data_extraction`. Keeping
the installation editable means changes made in this repository become
available without reinstalling it.

## Public API

The two main entry points are:

```python
from pathlib import Path

from data_extraction import build_chunks, extract_corpus

corpus = extract_corpus(
    [Path("specification.pdf")],
    output_directory=Path("extracted"),
)

chunks = build_chunks(
    corpus.blocks_path,
    output_path=corpus.blocks_path.with_name("chunks.json"),
    strategy="words",
    chunk_size=450,
    chunk_overlap=60,
)
```

`extract_corpus()` creates `blocks.json`, `corpus.txt`, and `sources.json` in a
content-addressed corpus directory. For PDF input, the source files are copied
under its `sources/` subdirectory. `build_chunks()` accepts either a block list
or a path to `blocks.json` and can optionally persist `chunks.json`.

## How `pdfplumber` is used

`pdfplumber` is the package's low-level PDF-reading and layout-analysis
library. It opens each document, iterates over its pages, and supplies:

- words and their page coordinates;
- information about upright and rotated text;
- detected table boundaries, rows, columns, cells, and ruling lines;
- page dimensions and other page-level information; and
- duplicate-character removal through `dedupe_chars()`.

The package's own extraction code then interprets this geometric information.
It recovers omitted unruled columns, separates side-by-side tables,
reconstructs headers and logical rows, handles selected visually merged rows,
removes spaces used as thousands separators from prices, and creates the
structured records in `blocks.json`.

In other words, `pdfplumber` provides text and layout coordinates; it does not
understand their semantic meaning. It cannot determine by itself that a range,
battery size, and price belong to the same vehicle version. Those associations
are reconstructed by this package's layout heuristics.

`pdfplumber` does not perform optical character recognition (OCR). A scanned
PDF containing page images but no usable text layer will therefore provide
little or no extractable text. Supporting such documents would require a
separate OCR stage before structure reconstruction.

## How Beautiful Soup is used

Beautiful Soup is the package's low-level HTML parsing library. It converts
downloaded HTML source into a navigable element tree and supplies:

- the page title and document elements such as headings and paragraphs;
- ordered and unordered lists, block quotations, and preformatted text;
- HTML table elements, captions, rows, headers, and data cells; and
- explicit `rowspan` and `colspan` attributes.

The package's own extraction code then interprets that tree. It removes
non-content elements such as scripts, styles, templates, forms, and vector or
canvas graphics; prefers the page's `main` or `article` area when available;
tracks the heading hierarchy; expands table spans into rectangular rows and
headers; and creates the common records stored in `blocks.json`.

In other words, Beautiful Soup parses the HTML syntax and exposes its element
structure; it does not decide which representation is most useful for RAG.
That selection and conversion are performed by this package.

Beautiful Soup does not execute JavaScript. Content added only after a page is
loaded in a browser may therefore be missing from the downloaded static HTML
and would require a browser-rendering stage before extraction.

## Chunk-size interpretation

`chunk_size=450` is a maximum target, not a required or minimum length. The
chunker does not add content merely to make every chunk equally long.

For ordinary text, consecutive blocks can be combined only while they belong
to the same source, page, and heading path and are not interrupted by a table.
The combined text is then divided into overlapping word ranges of at most the
configured size. If only a short paragraph or heading is available before a
structural boundary, the resulting chunk is correspondingly short.

Tables are processed separately. Complete rows are packed into a chunk until
adding another row would exceed the target size. Rows are not split merely to
reach a uniform chunk length. Consequently, a small table or the final rows of
a table can also produce a short chunk.

For example, a chunk such as:

```json
{
  "text": "Audi Hosszított tengelytáv",
  "source_text": "Audi Hosszított tengelytáv",
  "block_type": "text"
}
```

is likely a section heading that was flushed as text when the following table
was encountered. If the table already retains the same value as its caption or
heading path, the independent heading-only chunk is redundant.

Short chunks are not automatically incorrect. A concise paragraph may contain
a complete and important fact. However, structurally incomplete or duplicated
heading-only chunks can:

- occupy a limited `top-k` retrieval position without supplying the requested
  facts;
- create unnecessary embeddings and graph nodes;
- influence KNN connections and PageRank;
- cause graph-extraction work to be spent on little useful content; and
- overweight phrases repeated in both a heading chunk and a table caption.

## `text` and `source_text`

Each chunk contains two representations:

- `text` is a predictable word-token form without surrounding punctuation;
- `source_text` preserves punctuation, headings, and explicit table
  `column = value` relationships.

Removing punctuation is not inherently better for modern embedding models.
Each consuming application can choose which representation to embed and which
one to supply to its answering model.

## Tests

The suite is divided by purpose:

- `tests/unit/` contains fast tests that isolate the block contract, HTML
  processing, chunking, and PDF geometry helpers with synthetic inputs;
- `tests/regression/` processes complete, visually verified PDF fixtures and
  checks that representative tables and facts remain correct; and
- `tests/artifacts/` contains those public PDF fixtures and their SHA-256
  hashes.

Run all tests after installing the package:

```powershell
python -m unittest discover -s tests -v
```

The real-document tests are deliberately slower than the unit tests because
they parse every page of the fixture documents. They do not make network or
LLM API calls.

## License

This project is licensed under the Apache License 2.0. Its `pdfplumber` and
Beautiful Soup dependencies are distributed under the MIT License.

## Future Work

1. **Associate headings with following tables.** When a heading immediately
   introduces a table, store it in the table's `caption` or `heading_path` and
   include it in the table's `source_text`.

2. **Suppress duplicate heading-only chunks.** If a table already retains its
   introductory heading, do not also produce an independent chunk containing
   only the same heading.

3. **Support a configurable minimum text-chunk target.** Compatible short text
   blocks could be merged to reduce retrieval and indexing overhead. Merging
   must continue to respect structural boundaries: blocks should not be joined
   blindly across pages, tables, sources, or unrelated sections.

4. **Propagate vertically merged labels to their logical rows.** Some tables
   display an equipment level or category once across several physical rows.
   The extractor should repeat that value in every resulting logical row so
   each row remains independently interpretable.

5. **Represent cells spanning several columns explicitly.** A value centered
   across multiple variants can otherwise be split between columns. Future
   block-schema versions could retain `rowspan` and `colspan` metadata or
   expand the shared value into every affected logical cell.

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

## Introduction

For PDF input, the package uses `pdfplumber`, an MIT-licensed low-level Python
library for reading PDF text and layout geometry. `pdfplumber` supplies raw
elements such as words, coordinates, lines, and detected table boundaries. This
package adds deterministic rules that interpret those elements as headings,
paragraphs, table headers, columns, and logical rows, including recovery from
selected incomplete or irregular layouts. These additional processing steps are
described under [How `pdfplumber` is used](#how-pdfplumber-is-used).

For HTML input, the package uses Beautiful Soup (`beautifulsoup4`), an
MIT-licensed Python library for parsing HTML into an element tree. This package
then applies fixed structural rules: it excludes predefined HTML elements such
as scripts and styles, follows explicit heading levels, expands table cells with
`rowspan` or `colspan`, and converts the result into the same block format used
for PDFs. It does not interpret the subject or meaning of the text. This
processing is described under
[How Beautiful Soup is used](#how-beautiful-soup-is-used).

The `table_aware_chunker` package converts PDF documents or HTML pages
into a common structured representation and can then turn the extracted blocks
into chunks suitable for RAG applications. The corpus-preparation, embedding,
and indexing stages can therefore process PDF and HTML content through the same
block and chunk interface. In generated chunks, `source_text` retains readable
text, heading context, captions, and serialized table rows. Page numbers, source
names or URLs, and other provenance are retained in separate metadata fields;
they are not inserted into `source_text`.

**A central benefit is the structure-aware extraction of tables.** Instead of
flattening cells into an ambiguous sequence of text, the package reconstructs
headers, rows, columns, and their value relationships. When a table is rendered
into readable text or chunks, the appropriate column header is added to every
non-empty cell value to make its meaning explicit. In each generated table
chunk, this readable representation is stored in `source_text`, preserving
`column = value` relationships for retrieval and language-model context. For
example, this row:

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
be parsed once, inspected, tested, and reused by different RAG systems. Parsing
every page and reconstructing tables from coordinates can take considerable
time for large PDFs. The reconstruction uses heuristics: practical rules based
on observable layout signals such as coordinates, alignment, spacing, and
ruling lines. These rules work for the layouts they cover, but an unfamiliar
design may express its structure differently and produce an incorrect result.
Saving the extracted blocks allows the result to be reviewed once and reused
without repeating the work. New complex layouts should therefore be represented
by regression fixtures before being relied upon in production. In
`blocks.json`, headers and row cells remain stored separately; the explicit
`column = value` form is created during readable rendering and chunk
construction.

### Feature List

1. Extract text and tables from one or more text-based PDF documents or static
   HTML pages through a common API.
2. Detect the source type automatically. The source type identifies whether the
   supplied inputs are local PDF paths or HTTP(S) URLs for HTML pages, allowing
   the package to select the appropriate extractor when all inputs have the same
   type.
3. Reconstruct selected complex PDF table layouts, including missing borders or
   separator lines, unruled leading columns, rotated headings, side-by-side
   tables, detector-created empty spacer columns, and visually merged rows
   containing labeled multi-column text.
4. Process PDF text arranged in two or more side-by-side columns by detecting
   the recurring whitespace gutters between them, reading every column from
   top to bottom in left-to-right order, and joining each column's wrapped
   visual lines into a coherent paragraph.
5. Convert HTML headings, paragraphs, lists, quotations, preformatted text, and
   tables into the common block format, including expansion of `rowspan` and
   `colspan` cells.
6. Associate a heading immediately preceding a table with that table's
   `heading_path` when the heading is not already retained as a caption or
   heading path. The associated context is included in the table's
   `source_text`.
7. Suppress a separate heading- or caption-only chunk when its exact text is
   already retained by the following table, while preserving unrelated
   preceding prose.
8. Preserve source metadata separately from rendered text, including source
   names or URLs, page numbers when available, heading paths, and table
   identifiers.
9. Save reusable `blocks.json`, `corpus.txt`, and `sources.json` files, together
   with copies of PDF sources, in deterministically named corpus directories.
10. Validate block metadata and table shape before saving or chunking the
   structured records.
11. Create overlapping word-based chunks for ordinary text while keeping
    complete table rows together whenever they fit within the configured chunk
    size.
12. Split exceptionally wide table rows into column groups while repeating their
    leading key cells, so each group remains interpretable.
13. Provide both normalized `text` and structure-preserving `source_text`. For
    table cells, `source_text` contains explicit `column = value` relationships,
    while `text` retains the column names and values without the equal signs or
    surrounding punctuation. When a particular row has an empty cell, that
    column is omitted from the row's serialized representations instead of
    producing an empty `column = value` assignment.
14. Perform extraction and chunking without AI models or model API calls.

## Installation

The project requires Python 3.10 or newer. For local development, create and
activate a virtual environment, then install the repository in editable mode:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

The distribution is named `table-aware-chunker`, while the Python import name
is `table_aware_chunker`. Python distribution names may contain hyphens, but
import names must be valid Python identifiers, so the import name uses
underscores instead.

To use a local clone from another project, activate the consuming project's
virtual environment and install this repository by its relative path. For
example, if the two project directories are located beside each other, run the
following command from the consuming project's root directory:

```powershell
python -m pip install -e ..\table_aware_chunker
```

The consuming project can then import directly from `table_aware_chunker`. The
`-e` option installs the library in editable mode, so changes made in the local
`table_aware_chunker` source directory become available to the consuming
project without reinstalling the library.

## Quick Start

The two main entry points are `extract_corpus()` and `build_chunks()`. The
following example extracts one PDF and writes structure-aware chunks beside the
generated corpus files:

```python
from pathlib import Path

from table_aware_chunker import build_chunks, extract_corpus

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
dedicated corpus directory. Its 16-character name is generated deterministically
by computing a SHA-256 hash from the source identifier, the extracted content,
and the extraction-format version. This is an ordinary cryptographic hash
calculation; it does not use AI or make any model calls. Using the same sources
in the same order, with unchanged content and the same extraction version,
therefore selects the same directory. For PDF input, the source files are copied
under its `sources/` subdirectory. `build_chunks()` accepts either a block list
or a path to `blocks.json` and can optionally persist `chunks.json`.

A runnable version is available in `examples/extract_and_chunk_pdf.py`. By
default, it processes `tests/artifacts/Q8.pdf`, so after installing the package
you can run it from the repository root without arguments:

```powershell
python .\examples\extract_and_chunk_pdf.py
```

By default, the example writes the extracted corpus and `chunks.json` under
`example_output/`. Pass a PDF path to process a different document, and use
`--output-directory` to select a different location:

```powershell
python .\examples\extract_and_chunk_pdf.py .\path\to\document.pdf `
    --output-directory .\another_output_directory
```

### Calling the example from Java

`examples/TableExtractionExample.java` demonstrates how a Java application can
start the Python example as a subprocess. It uses paths relative to the
repository root rather than absolute paths tied to one computer. The PDF path
and output directory are optional command-line arguments, allowing callers to
override the defaults without modifying the Java source. When no arguments are
provided, it reads `tests/artifacts/Q8.pdf` and writes results under
`example_output/`. From the repository root, compile and run it with:

```powershell
javac .\examples\TableExtractionExample.java
java -cp .\examples TableExtractionExample
```

Pass a PDF path and output directory after the class name to override the
defaults.

The Java example automatically selects `.venv/Scripts/python.exe` on Windows or
`.venv/bin/python` on Linux and macOS. The package must already be installed in
that virtual environment as described under [Installation](#installation).

## How `pdfplumber` is used

`pdfplumber` is the package's low-level PDF-reading and layout-analysis
library. It opens each document, iterates over its pages, and supplies:

- words and their page coordinates;
- information about upright and rotated text;
- detected table boundaries, rows, columns, cells, and ruling lines;
- page dimensions and other page-level information, which allow coordinates to
  be interpreted relative to the page and help distinguish separate layout
  regions; and
- duplicate-character removal through `dedupe_chars()`. A PDF may contain the
  same character more than once at almost the same coordinates—for example,
  because it has overlapping text layers or draws a character twice to create a
  visual effect. A PDF viewer may display what appears to be one character,
  while raw extraction returns both copies and can turn `Price` into something
  like `PPrriiccee`. `dedupe_chars()` compares characters at matching positions
  and keeps one copy. Legitimate repeated letters at different positions remain
  unchanged.

The package's own extraction code then interprets this geometric information.
It recovers omitted unruled columns, separates side-by-side tables,
reconstructs headers and logical rows, handles selected visually merged rows,
removes spaces used as thousands separators from prices, and creates the
structured records in `blocks.json`.

In other words, `pdfplumber` provides text and layout coordinates, while this
package applies deterministic structural rules based on position, row
alignment, table boundaries, and repeated headers. Neither component assigns
subject-specific meaning to the extracted values. The extractor may group
several values into one row because their positions indicate that they belong
together, but it does not identify what those values represent. Layouts that
express such relationships differently may require additional extraction rules
and regression tests.

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
structure. It does not choose which elements the extractor should retain,
associate headings with later elements, expand merged table cells, or define the
output block format. This package makes those structural decisions from
explicit HTML tags and fixed inclusion, exclusion, and conversion rules; it
does not evaluate the meaning of the text. The resulting elements are converted
into structured text and table blocks. Before saving them, the package checks
that the records conform to the block schema—for example, that metadata fields
have the expected types and every table row has the same number of cells as its
header. Every table header and cell must be represented as a string. The package
does not infer or enforce column data types: a column may contain values such as
`"10"`, `"25"`, and `"Not available"` because all three are strings. A literal
JSON number such as `10`, however, does not conform to the block schema unless
it is first converted to `"10"`. This validation checks the structure and JSON
types of the records, not the meaning, accuracy, consistency, or factual
correctness of their content.

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

When a heading immediately precedes a table, the package associates it with the
table and includes it in the table's `source_text`. If that same heading would
otherwise become a separate heading- or caption-only chunk, the duplicate is
suppressed. Other preceding prose is preserved. A heading-only chunk such as:

```json
{
  "text": "Audi Hosszított tengelytáv",
  "source_text": "Audi Hosszított tengelytáv",
  "block_type": "text"
}
```

can still occur when the heading does not directly introduce a table or is not
retained by that table. In those cases it remains independent content rather
than a known duplicate.

Short chunks are not automatically incorrect. A concise paragraph may contain
a complete and important fact. However, structurally incomplete or duplicated
heading-only chunks can:

- occupy a limited `top-k` retrieval position without supplying the requested
  facts;
- create unnecessary embeddings and graph nodes;
- influence KNN connections and PageRank in graph-based RAG systems, such as
  KNNG-RAG, where chunks become graph nodes and similarity links affect ranking;
- cause graph-extraction work to be spent on little useful content; and
- overweight phrases repeated in both a heading chunk and a table caption.

## `text` and `source_text`

Each chunk contains two representations:

- `text` is a predictable word-token form without surrounding punctuation,
  retained primarily for experiments with normalized retrieval input;
- `source_text` preserves punctuation, headings, readable structure, and
  explicit table `column = value` relationships.

Both fields were retained to make it possible to compare normalized text with
structure-preserving text without rebuilding the chunks. The recommended
default is `source_text`, both for embedding and for answer-generation context,
because it preserves the relationships that make table cells interpretable to
a language model. The `text` field remains useful for controlled experiments or
retrieval methods that explicitly require normalized word tokens.

## Tests

The suite is divided by purpose:

- `tests/unit/` contains fast tests that isolate the block contract, HTML
  processing, chunking, and PDF geometry helpers with synthetic inputs;
- `tests/regression/` processes complete, visually verified PDF fixtures and
  checks that representative tables and facts remain correct; and
- `tests/artifacts/` contains the public PDF fixtures, together with a README
  that records each fixture's SHA-256 hash so its exact contents can be
  verified.

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

1. **Support a configurable minimum text-chunk target.** Compatible short text
   blocks could be merged to reduce retrieval and indexing overhead. Merging
   must continue to respect structural boundaries: blocks should not be joined
   blindly across pages, tables, sources, or unrelated sections.

2. **Propagate vertically merged labels to their logical rows.** Some tables
   display an equipment level or category in a cell that visually spans several
   rows. PDF extraction may place that label only in the first row and leave the
   corresponding cell empty in the rows below. For example, one `Category A`
   label may apply to several item rows. The extractor should repeat `Category
   A` in every resulting logical row so each item remains understandable when
   retrieved without the surrounding rows.

3. **Represent cells spanning several columns explicitly.** A value centered
   across two or more columns usually applies to every column covered by that
   visual span. A basic rectangular extraction may place the value in the first
   column and leave the other covered cells empty, making those columns appear
   unrelated to it, or may incorrectly divide one value at a detected column
   boundary. For example, a merged cell spanning `WALLBOX DUO` and `VERTICA
   DUO` should retain the complete value `220000 Ft – 290000 Ft` in both
   logical cells; similarly, `60000 Ft / 120000 Ft*` should remain complete in
   both affected cells. The preferred future behavior is to copy the shared
   value into every affected logical cell. Each row or column group would then
   retain the value when processed independently. The original `colspan` could
   also be retained as metadata when the exact source layout needs to be
   reconstructed.

4. **Support additional chunking strategies.** Currently,
   `strategy="words"` divides ordinary text by word count while preserving table
   rows. Additional strategies could split prose at sentence or paragraph
   boundaries, use the tokenizer of a selected embedding model, or respect
   document sections and pages more strongly. Sentence- or paragraph-aware
   chunking could preserve coherent ideas, while model-token-aware chunking
   could use context limits more precisely. Any strategy should continue to
   preserve table structure and could be evaluated against the existing word
   strategy for retrieval quality, chunk size, and indexing cost.

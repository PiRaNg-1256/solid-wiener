---
name: markitdown
description: Convert documents and other files to Markdown with Microsoft's MarkItDown (github.com/microsoft/markitdown) so their content can be read, searched, summarized or quoted. Use when the user gives a PDF, Word (.docx), PowerPoint (.pptx), Excel (.xlsx/.xls), Outlook (.msg), EPUB, HTML, CSV/JSON/XML, Jupyter notebook, ZIP, image or audio file, or a YouTube URL and wants its text, tables or structure; or asks to "convert to markdown", "extract text from", or "read this document". Not for producing high-fidelity documents for humans, and not for editing the source file.
---

# MarkItDown: files to Markdown

MarkItDown turns files into LLM-friendly Markdown, keeping headings, lists, tables and links.
Output is meant for reading and analysis, not for pixel-faithful conversion.

## 1. Make sure it is available

```bash
command -v markitdown && markitdown --version
```

If missing, pick one (Python 3.10–3.14 required):

```bash
# One-off run without installing (fastest when uv is present)
uvx --from 'markitdown[all]' markitdown input.pdf -o out.md

# Or install into a virtual environment
python -m venv .venv-markitdown && .venv-markitdown/bin/pip install 'markitdown[all]'
```

`[all]` pulls in every format plus the Azure SDKs. To keep it light, install only the
extras you need, e.g. `'markitdown[pdf,docx,pptx,xlsx]'`.

| Extra | Enables |
| --- | --- |
| `pdf` | PDF |
| `docx` | Word |
| `pptx` | PowerPoint |
| `xlsx` / `xls` | Excel (new / legacy) |
| `outlook` | Outlook `.msg` |
| `audio-transcription` | wav/mp3 speech-to-text (uses a web service) |
| `youtube-transcription` | YouTube transcripts |
| `az-doc-intel` | Azure Document Intelligence |
| `az-content-understanding` | Azure Content Understanding |

HTML, CSV, JSON, XML, RSS, EPUB, ZIP and notebooks work with the base package.
Images produce **nothing** unless the `exiftool` binary is on PATH (metadata) or an LLM
client is configured (description); an empty result for a .jpg is expected, not a bug.

## 2. Convert

Always write to a file with `-o`, then read that file. Do not pipe straight into
`head`/`less`: closing the pipe early makes markitdown print a `BrokenPipeError` traceback.

```bash
markitdown report.pdf -o report.md
```

`-o` does not create directories; `mkdir -p` the destination first.

Stdin works and the type is usually auto-detected. When it is not, pass a hint:

```bash
markitdown -x docx < attachment.bin -o out.md      # extension hint
markitdown -m application/pdf < blob -o out.md      # MIME hint
markitdown -c cp932 < legacy.csv -o out.md          # charset hint
```

Batch a folder:

```bash
mkdir -p md && for f in docs/*.{pdf,docx,pptx,xlsx}; do
  [ -e "$f" ] && markitdown "$f" -o "md/$(basename "$f").md"
done
```

Keep the original extension in the output name (`report.pdf.md`): stripping it makes
`report.pdf` and `report.docx` overwrite each other.

Other flags: `--keep-data-uris` (keep base64 images instead of truncating them),
`-p/--use-plugins` and `--list-plugins` (third-party converters, off by default).

## 3. Read the result sensibly

- Check size first (`wc -c out.md`) and read large outputs in parts rather than all at once.
- Spreadsheets become one `## SheetName` section with a Markdown table per sheet.
- Slides become `<!-- Slide number: N -->` sections; speaker notes, when present, follow under `### Notes:`.
- Scanned PDFs (images of text) come out empty or near-empty: offline conversion does no OCR.
  Say so, and offer an OCR route (section 5) instead of guessing at content.

## 4. Errors and what they mean

| Message | Cause | Fix |
| --- | --- | --- |
| `MissingDependencyException ... include the optional dependency [xls]` | Extra not installed | Install the extra it names |
| `UnsupportedFormatException` | Type not recognized (often stdin with no hint, or an empty file) | Pass `-x`/`-m`, or check the file |
| `FileConversionException ... BadZipFile` | Wrong hint or corrupt Office file | Drop or correct the hint; verify the file |
| `FileNotFoundError` on `-o` path | Output directory missing | `mkdir -p` it |

Exit code is 1 on any failure; don't treat a partial stdout as success.

## 5. Python API

```python
from markitdown import MarkItDown

md = MarkItDown(enable_plugins=False)
result = md.convert_local("test.xlsx")   # local files only
print(result.markdown)                   # also: result.title
```

Prefer the narrowest call: `convert_local()` for paths, `convert_stream()` for file objects,
`convert_response()` for a `requests` response you fetched yourself. Plain `convert()` also
fetches URLs, so never pass it untrusted input.

Image descriptions (images, pptx) and OCR via the `markitdown-ocr` plugin need an
OpenAI-compatible client:

```python
from openai import OpenAI
md = MarkItDown(enable_plugins=True, llm_client=OpenAI(), llm_model="gpt-4o")
```

## 6. Cloud and privacy

Offline conversion stays on the machine. These send file contents to a remote service,
so only use them when the user asks and has credentials set up:

- Audio transcription and YouTube transcripts.
- LLM image captioning / `markitdown-ocr`.
- Azure Document Intelligence: `markitdown f.pdf -d -e <endpoint>` (or `MARKITDOWN_DOCINTEL_ENDPOINT`),
  key from `AZURE_API_KEY`, else `DefaultAzureCredential`. Billable.
- Azure Content Understanding: `markitdown f.pdf --use-cu --cu-endpoint <endpoint>`
  (or `MARKITDOWN_CU_ENDPOINT`). Billable; the only option here for video.

## Version note

The GitHub README tracks `main`, which can be ahead of the PyPI release. For example,
`--docintel-features` and `--docintel-query-fields` are documented on GitHub but absent from
0.1.9. Trust `markitdown --help` over the README.

# 5-minute quickstart — from a PDF to "Claude answers with page citations"

[繁體中文版在下方 ↓](#繁體中文快速上手)

This walks one real textbook chapter through the pipeline: the bundled sample
[`examples/sample/`](sample/) is Chapter 1 of OpenStax *Anatomy and Physiology*
(CC BY 4.0, 33 pages — [attribution](sample/ATTRIBUTION.md)). Steps 1–4 need only Python;
step 5 is optional semantic search.

You need: Python 3.10+, git, and [Claude Code](https://code.claude.com/docs) for step 4.

## 1 · Get the code and install (≈30 s)

```bash
git clone https://github.com/drpwchen/textbook-to-note
cd textbook-to-note
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# Windows Git Bash:   source .venv/Scripts/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 2 · Convert the sample PDF (≈1 s)

```bash
mkdir books
cp examples/sample/OpenStax_Anatomy-and-Physiology-Ch1_1e_2013.pdf books/
python converter/convert.py --batch-dir books
```

Expected:

```
  [1/1] OpenStax_Anatomy-and-Physiology-Ch1_1e_2013.pdf: 33p, 5t, 39f, 3 pseudo-table rejected
Converted: 1 | Failed (Surya unavailable): 0 | Errors: 0
```

The markdown lands in `output/OpenStax_Anatomy-and-Physiology-Ch1_1e_2013/`, with a
`<!-- page N -->` marker at each PDF page. A recent PyMuPDF prints a `FutureWarning` about
the `fitz` name; it is harmless.

## 3 · Re-split into a corpus (< 1 s)

```bash
python converter/convert.py --resplit output --out corpus --pdf-root books \
    --split-config examples/sample/_split_config.json
python converter/split_manifest.py --validate corpus
```

Expected: `config valid 10 files`, then `PASS  OpenStax_Anatomy-and-Physiology-Ch1_1e_2013`
and `1/1 books pass`. `corpus/` now holds one file per section, a `_manifest.json` (page
ranges, and `page_labels` mapping each PDF page to the printed page number) and a
`corpus.json` marker.

Why `--split-config`: `--resplit` detects which outline level holds the chapters from at
least three numbered entries, and a one-chapter excerpt has one. The entry in
[`sample/_split_config.json`](sample/_split_config.json) says so, with its reason — the same
mechanism you use for a real book whose outline is wrong. Whole books normally need no
config.

## 4 · Ask Claude Code (≈30 s)

From the repo folder, start `claude` and ask:

> Answer only from the markdown under corpus/: What is negative feedback in homeostasis, and
> what are its components? Cite the book folder and the printed page for each claim. The
> `<!-- page N -->` markers are PDF pages; corpus/<book>/_manifest.json page_labels maps a
> PDF page to the printed page. Keep it to 3-5 sentences.

Claude greps the corpus and answers with the sensor / control center / effector loop, citing
printed page 21 (PDF page 15 of the sample). No index, no embedding model — for a few dozen
books this is genuinely enough.

## 5 · (Optional) Semantic search with ask-your-library (≈2–3 min)

Once "which book was that in?" stops being obvious, hand the corpus to the companion repo
[ask-your-library](https://github.com/drpwchen/ask-your-library). Install it per its README
into **its own venv** — not this repo's: it needs `lancedb` and friends, which this repo's
`requirements.txt` does not install — plus [Ollama](https://ollama.com) and
`ollama pull qwen3-embedding:0.6b`. Below, `<ayl-python>` is that venv's interpreter **by
absolute path** (`<ask-your-library>/.venv/Scripts/python.exe` on Windows,
`<ask-your-library>/.venv/bin/python` elsewhere; `python -c "import sys; print(sys.executable)"`
inside that activated venv prints it). No Obsidian vault is needed: leave `VAULT_PATH` unset —
its README's "Corpus only, no Obsidian vault" paragraph is this setup. From this repo folder:

```bash
<ayl-python> <ask-your-library>/server/corpus.py add corpus
```

It checks the corpus, embeds it, audits it, and prints the line to set, e.g.
`VAULT_SEARCH_TEXTBOOK_DB_PATH=/home/you/.vault-search/textbook_db`. Register the MCP server
with that line — again with `<ayl-python>`, not a bare `python`: Claude Code starts the server
with exactly the interpreter you name, and one without `lancedb` fails to connect:

```bash
claude mcp add ask-your-library -e VAULT_SEARCH_TEXTBOOK_DB_PATH=<path printed above> \
    -- <ayl-python> <ask-your-library>/server/mcp_server.py
```

In a new Claude Code session:

> Use the textbook_search tool to answer: What is negative feedback in homeostasis, and what
> are its three components? Cite book and page for each claim.

Claude calls `textbook_search` and cites the same page 21 — `textbook_search` returns the
printed page numbers from the manifest.

## What was measured

One run on Windows 11 (Git Bash, Python 3.12, fresh venv, CPU conversion; the embedding in
step 5 used the local GPU through Ollama). n = 1 — read these as orders of magnitude.

| Step | Wall time |
|---|---|
| 1 · venv + `pip install -r requirements.txt` | 26 s |
| 2 · `convert.py --batch-dir` (33 pages) | 1.1 s |
| 3 · `--resplit` | 0.4 s |
| 4 · Claude Code answer from the corpus (grep) | 27 s |
| 5 · `corpus.py add` (53 chunks) | 6–144 s — 144 s on the first run, which includes loading the embedding model; 6 s in a second run with the model already loaded |
| 5 · Claude Code answer through `textbook_search` | 19 s |

Step 5's install of ask-your-library and the Ollama model download are not included. The 6 s
figure is from a second tester's run, also n = 1. The run
of step 5 used a throw-away database (`corpus.py add --db <tmp>`), and the MCP server was
started for the test from a config file with that path instead of `claude mcp add`; the
`claude mcp add` line was checked separately in an earlier form (`claude mcp list` →
`✔ Connected`); its current form — `<ayl-python>`, no `VAULT_PATH` — follows ask-your-library
3.3.0's README and was not re-run for this page.
The run called the venv's interpreter directly (`.venv/Scripts/python`) instead of
activating it, and started from an export of this repository's tree rather than a fresh
`git clone`. The macOS / Linux / PowerShell activation lines are the standard equivalents and
were not run for this page.

## Next

- Your own books: drop PDFs into `books/` and repeat steps 2–3 without `--split-config`.
- Structured notes and figure extraction: install the skills — see
  [Install as a Claude Code plugin](../README.md#install-as-a-claude-code-plugin) — and read
  [`workflows/note-writing.md`](../workflows/note-writing.md).
- Full setup (OCR for scanned books, guidelines, configuration): [`AGENTS.md`](../AGENTS.md).

---

## 繁體中文快速上手

用隨附的範例走完整條 pipeline：[`examples/sample/`](sample/) 是 OpenStax《Anatomy and
Physiology》第 1 章（CC BY 4.0，33 頁，[出處與授權](sample/ATTRIBUTION.md)）。第 1–4 步只要
Python；第 5 步的語意搜尋是選配。

需要：Python 3.10+、git，第 4 步要 [Claude Code](https://code.claude.com/docs)。

### 1 · 取得程式並安裝（約 30 秒）

```bash
git clone https://github.com/drpwchen/textbook-to-note
cd textbook-to-note
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# Windows Git Bash：   source .venv/Scripts/activate
# Windows PowerShell：.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2 · 轉換範例 PDF（約 1 秒）

```bash
mkdir books
cp examples/sample/OpenStax_Anatomy-and-Physiology-Ch1_1e_2013.pdf books/
python converter/convert.py --batch-dir books
```

應該看到 `33p, 5t, 39f, 3 pseudo-table rejected` 和 `Converted: 1`。markdown 在
`output/OpenStax_Anatomy-and-Physiology-Ch1_1e_2013/`，每個 PDF 頁開頭有 `<!-- page N -->`。
新版 PyMuPDF 會印一行 `fitz` 的 `FutureWarning`，不影響結果。

### 3 · 重切成 corpus（不到 1 秒）

```bash
python converter/convert.py --resplit output --out corpus --pdf-root books \
    --split-config examples/sample/_split_config.json
python converter/split_manifest.py --validate corpus
```

應該看到 `config valid 10 files`、`PASS` 和 `1/1 books pass`。`corpus/` 裡每節一個檔、一份
`_manifest.json`（頁碼範圍，以及 PDF 頁 → 印刷頁碼的 `page_labels`）和 `corpus.json`。

為什麼要 `--split-config`：`--resplit` 要看到至少三個編號章節才判斷得出哪一層書籤是「章」，
單章節選只有一個。[`sample/_split_config.json`](sample/_split_config.json) 寫明這件事與理由——
真實書籍書籤有錯時也是用同一個機制修。整本書通常不需要設定。

### 4 · 問 Claude Code（約 30 秒）

在 repo 資料夾裡啟動 `claude`，問：

> Answer only from the markdown under corpus/: What is negative feedback in homeostasis, and
> what are its components? Cite the book folder and the printed page for each claim. The
> `<!-- page N -->` markers are PDF pages; corpus/<book>/_manifest.json page_labels maps a
> PDF page to the printed page. Keep it to 3-5 sentences.

Claude 會 grep corpus，答出 sensor／control center／effector 迴路，引用印刷頁 21（範例 PDF
第 15 頁）。不需要索引、不需要 embedding 模型；幾十本書的規模這樣就夠用。

### 5 ·（選配）用 ask-your-library 做語意搜尋（約 2–3 分鐘）

開始想不起來「那段在哪一本」時，再把 corpus 交給姊妹專案
[ask-your-library](https://github.com/drpwchen/ask-your-library)。照它的 README 安裝在==它自己的
venv==（不是本 repo 的：它需要 `lancedb` 等套件，本 repo 的 `requirements.txt` 不裝這些），再裝
[Ollama](https://ollama.com) 並 `ollama pull qwen3-embedding:0.6b`。以下 `<ayl-python>` 指那個 venv
的直譯器，==用絕對路徑==（Windows：`<ask-your-library>/.venv/Scripts/python.exe`；其他：
`<ask-your-library>/.venv/bin/python`；在啟用的那個 venv 裡跑
`python -c "import sys; print(sys.executable)"` 會印出來）。不需要 Obsidian vault：`VAULT_PATH` 不用設，
它 README 的「Corpus only, no Obsidian vault」一段就是這種裝法。在本 repo 資料夾執行：

```bash
<ayl-python> <ask-your-library>/server/corpus.py add corpus
```

它會檢查、embedding、稽核，最後印出要設定的那一行（例如
`VAULT_SEARCH_TEXTBOOK_DB_PATH=/home/you/.vault-search/textbook_db`）。用那個路徑註冊 MCP server——
同樣用 `<ayl-python>`，不要寫裸的 `python`：Claude Code 會用你寫的那個直譯器啟動 server，沒有
`lancedb` 的直譯器會連不上：

```bash
claude mcp add ask-your-library -e VAULT_SEARCH_TEXTBOOK_DB_PATH=<上面印出的路徑> \
    -- <ayl-python> <ask-your-library>/server/mcp_server.py
```

開新的 Claude Code session 問：

> Use the textbook_search tool to answer: What is negative feedback in homeostasis, and what
> are its three components? Cite book and page for each claim.

Claude 會呼叫 `textbook_search`，同樣引用第 21 頁——`textbook_search` 回傳的是 manifest 裡的印刷頁碼。

### 實測

Windows 11 上跑一次（Git Bash、Python 3.12、全新 venv；轉檔用 CPU，第 5 步的 embedding 經
Ollama 用本機 GPU）。n = 1，只看數量級。

| 步驟 | 實際耗時 |
|---|---|
| 1 · venv ＋ `pip install -r requirements.txt` | 26 秒 |
| 2 · `convert.py --batch-dir`（33 頁） | 1.1 秒 |
| 3 · `--resplit` | 0.4 秒 |
| 4 · Claude Code 從 corpus（grep）回答 | 27 秒 |
| 5 · `corpus.py add`（53 個 chunk） | 6–144 秒：第一次 144 秒，含載入 embedding 模型；模型已載入時另一次實測 6 秒 |
| 5 · Claude Code 經 `textbook_search` 回答 | 19 秒 |

不含 ask-your-library 的安裝與 Ollama 模型下載；6 秒那筆是另一位測試者的一次實測（同樣 n = 1）。第 5 步實測用的是一次性資料庫
（`corpus.py add --db <tmp>`），MCP server 由設定檔帶該路徑啟動，而不是 `claude mcp add`；
`claude mcp add` 指令另外驗過較早的寫法（`claude mcp list` → `✔ Connected`）；現在的寫法
（`<ayl-python>`、不設 `VAULT_PATH`）照 ask-your-library 3.3.0 的 README，這頁沒有重跑。實測直接呼叫 venv 的
直譯器（`.venv/Scripts/python`），沒有先 activate；程式碼取自本 repo 匯出的檔案樹，不是全新的
`git clone`。macOS／Linux／PowerShell 的 activate 指令是標準對應寫法，這頁沒有實跑。

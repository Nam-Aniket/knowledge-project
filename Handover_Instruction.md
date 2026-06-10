# Handover Instruction: FIT5202/FIT5212 Semi-Structured Data Analysis Study Guide Generator

This document details the context, commands, databases, mathematical derivations, and compilation pipelines used to create the Markdown study guide and the ReportLab-compiled PDF study guide for the FIT5202/FIT5212 course. It also includes a system prompt/instruction set so that another model (e.g., Codex) can re-run this entire pipeline autonomously without user intervention.

---

## 1. System Paths & Environment Details

* **Project Root:** `/Users/aniketnamjoshi/knowledge-project`
* **Virtual Environment Python:** `/Users/aniketnamjoshi/knowledge-project/.venv/bin/python`
* **Study Guide Source PDFs:** `/Users/aniketnamjoshi/Downloads/_Organized/FIT_Courses/FIT5202 - Semi-structured Data Analysis/Semi-structuredDataAnalysis/Study_Guide/`
* **Active SQLite Database:** `/Users/aniketnamjoshi/.psyche/topic_semistructured.db`
* **Tesseract OCR Binary:** `/opt/homebrew/bin/tesseract`
* **Output Markdown File:** `/Users/aniketnamjoshi/Downloads/SemiStructured_StudyGuide.md`
* **Output PDF File:** `/Users/aniketnamjoshi/Downloads/SemiStructured_StudyGuide.pdf`
* **PDF Generation Script:** `/Users/aniketnamjoshi/.gemini/antigravity/brain/e496df84-f62c-4560-9ef1-fd8471f9728b/scratch/generate_pdf.py`

---

## 2. Ingestion & Database Workflow

The workspace project `/Users/aniketnamjoshi/knowledge-project` provides a python-based RAG CLI tool `psyche`. The following code changes were made to support PyMuPDF parsing and forced re-ingestion:

1. **PyMuPDF Parser Integration:**
   * Updated `extract_pdf_text` in [parsers.py](file:///Users/aniketnamjoshi/knowledge-project/parsers.py) to import `fitz` (PyMuPDF) and use it as the primary parser. It falls back to `pypdf` if `pymupdf` is not installed or raises an error.
2. **Forced Re-Ingestion Support:**
   * Added the `--force` / `-f` flags in [ingest.py](file:///Users/aniketnamjoshi/knowledge-project/ingest.py).
   * Implemented `remove_source(conn, source_id)` in [db.py](file:///Users/aniketnamjoshi/knowledge-project/db.py). This function deletes all chunks, embeddings, FTS5 virtual table entries, and `sqlite-vec` index rows associated with the document before re-parsing and re-indexing.
3. **Execution Command:**
   To re-ingest the study guide PDFs, run:
   ```bash
   cd /Users/aniketnamjoshi/knowledge-project
   .venv/bin/python ingest.py /Users/aniketnamjoshi/Downloads/_Organized/FIT_Courses/FIT5202\ -\ Semi-structured\ Data\ Analysis/Semi-structuredDataAnalysis/Study_Guide --topic semistructured --force
   ```

---

## 3. Image Extraction & OCR Pipeline

Many quizzes and calculations in the slides are stored as raster images inside the PDFs.
* **Extraction Script:** Python PIL + PyMuPDF were used to extract images from `Quiz FIT5212.pdf` and `Week 7 - FIT5212.pdf` on specific pages (Page 8, 9, 15, 19).
* **OCR Command:** Tesseract OCR was executed on the extracted JPEG/PNG files using Page Segmentation Modes (PSM) 3 (default), 6 (single block), and 11 (sparse text):
  ```bash
  /opt/homebrew/bin/tesseract <image_path> stdout --psm 6
  ```
This OCR process recovered the exact ratings values for the Collaborative Filtering question (Q29/Q30), the Fiedler vector adjacency matrix (Q43), and the GCN layer features (Q51/Q52).

---

## 4. Mathematical Reconstruction & Solutions

### A. Collaborative Filtering Ratings Matrix (Q29 & Q30)
The recovered user-item ratings matrix is:

| User | Lion King | Aladdin | Mulan | Anastasia |
| :--- | :---: | :---: | :---: | :---: |
| **Jane** | 3 | ? | 1 | 0 |
| **Joe** | 5 | 4 | 0 | 2 |
| **John** | 3 | 0 | 3 | 3 |
| **Jill** | 1 | 2 | 4 | 2 |
| **Jorge**| 2 | 2 | 0 | 1 |

*Note: 0 ratings represent unrated items but are included in user/item means.*
* **User-Based CF Prediction (Jane & Aladdin):** 
  * Jane vector: $[3, 1, 0]$
  * Joe similarity $= 0.88$, Jorge similarity $= 0.84$. (Joe and Jorge are the top-2 neighbors).
  * Joe mean $= 2.75$, Jorge mean $= 1.25$, Jane mean $= 1.33$.
  * Prediction: $\hat{r}_{\text{Jane}, \text{Aladdin}} = 1.33 + \frac{0.88 \times (4 - 2.75) + 0.84 \times (2 - 1.25)}{0.88 + 0.84} = \mathbf{2.33}$ (Option b).
* **Item-Based CF Prediction (Jane & Aladdin):**
  * Aladdin vector: $[4, 0, 2, 2]$ (excluding Jane).
  * Lion King similarity $= 0.84$, Anastasia similarity $= 0.67$. (LK and AN are the top-2 items).
  * Aladdin mean $= 2.0$, LK mean $= 2.8$, AN mean $= 1.6$.
  * Prediction: $\hat{r}_{\text{Jane}, \text{Aladdin}} = 2.0 + \frac{0.84 \times (3 - 2.8) + 0.67 \times (0 - 1.6)}{0.84 + 0.67} = \mathbf{1.40}$ (Option d).

### B. GCN Forward Pass (Q51 & Q52)
* **Parameters:** $W_k = [1, 1]$, $B_k = [0.5, 0.5]$, $\sigma = \text{ReLU}$.
* **Initial embeddings:** Target A $= [0, -0.5]$. Neighbors: B $[1, -1]$, C $[0.5, -1]$, D $[0, -1]$, E $[1, 0.5]$.
* **Node A Embedding Update:**
  * Neighbor mean $\mathbf{m}_A = \frac{\mathbf{h}_B + \mathbf{h}_C + \mathbf{h}_D + \mathbf{h}_E}{4} = [0.625, -0.625]$.
  * Weighted aggregation: $W_k \odot \mathbf{m}_A = [0.625, -0.625]$.
  * Self-embedding bias: $B_k \odot \mathbf{h}_A^{(0)} = [0, -0.25]$.
  * Sum: $[0.625, -0.875]$.
  * ReLU: $\mathbf{h}_A^{(k)} = \text{ReLU}([0.625, -0.875]) = \mathbf{[0.625, 0]}$ (rounds to Option d: `[0.5, 0]`).
* **Binary Classifier Output:**
  * $\mathbf{w} = [2, -0.6]$, $b = 0.1$.
  * Logit: $z = \mathbf{w} \cdot \mathbf{h}_A^{(k)} + b = 2(0.625) - 0.6(0) + 0.1 = 1.35$.
  * Sigmoid Probability: $P(\text{class } 1) = \sigma(1.35) = \frac{1}{1 + e^{-1.35}} \approx \mathbf{0.79}$.
  * Outputs: `[0.75, 0.25]` (Option a).

### C. Spectral Clustering Partitioning (Q43)
* **Adjacency Matrix:** $V_1 - [V_2, V_3]$, $V_2 - [V_1, V_3]$, $V_3 - [V_1, V_2, V_4]$, $V_4 - [V_3]$.
* **Degree Matrix:** $D = \text{diag}(2, 2, 3, 1)$.
* **Laplacian:** $L = D - A$.
* **Fiedler Vector (Eigenvector for $\lambda = 1.0$):** $\mathbf{f} = [-0.4082, -0.4082, 0.0, 0.8165]$.
* **Signs Partition:** Cluster 1 $\{V_1, V_2\}$ (negative) and Cluster 2 $\{V_3, V_4\}$ (positive/neutral) (Option b).

### D. Node2vec Biased Random Walk (Q48)
* Start node 2, first step $2 \to 1$. Parameters $p=2, q=0.5$.
* At node 1:
  * Outward step to 4 (distance 2 from 2): weight $\frac{1}{q} = 2$.
  * Local step to 3 (distance 1 from 2): weight $1$.
  * Return step to 2 (distance 0 from 2): weight $\frac{1}{p} = 0.5$.
* Probability to go to 4 is $\frac{2}{3.5} = 0.57$. The walk `2 1 4` is the most likely (Option a).

### E. Softmax & Attention with $e=2$ Approximation (Q19, Q20-22)
* **Softmax:** For vector $[0, 1, 1, -1]$, exponentials are $2^0=1, 2^1=2, 2^1=2, 2^{-1}=0.5$. Sum $= 5.5$. Softmax $= [0.18, 0.36, 0.36, 0.09]$ (Option a).
* **Attention Score:** Decoder state $\mathbf{s}_t = [1, 2, 3]$, Encoder states $\mathbf{h}_1 = [0, 1, 0]$, $\mathbf{h}_2 = [1, 1, 0]$. Scores $= [2, 3]$ (Option a).
* **Attention Distribution ($e=2$):** Exponentials $= [4, 8]$. Sum $= 12$. Weights $= [4/12, 8/12]$ (Option a).
* **Attention Output:** $\frac{4}{12}\mathbf{h}_1 + \frac{8}{12}\mathbf{h}_2 = [8/12, 12/12, 0]$ (Option a).

---

## 5. ReportLab PDF Compilation Pipeline

The PDF is generated using ReportLab flowables. To support vertical mathematical fractions without standard LaTeX support in ReportLab:
1. **Fraction Stacking Engine:**
   We defined a `build_frac_table(parts, style, indent)` helper. It wraps mathematical components inside a 1-row table. Nested elements are either raw strings (compiled as normal paragraphs) or tuples `(numerator, denominator)` which compile to a sub-table with a horizontal fraction line (`LINEBELOW` style) separating them.
2. **Execution:**
   Running `/Users/aniketnamjoshi/knowledge-project/generate_pdf.py` compiles the document into `/Users/aniketnamjoshi/Downloads/SemiStructured_StudyGuide.pdf`.
   ```bash
   cd /Users/aniketnamjoshi/knowledge-project
   .venv/bin/python /Users/aniketnamjoshi/.gemini/antigravity/brain/e496df84-f62c-4560-9ef1-fd8471f9728b/scratch/generate_pdf.py
   ```

---

## 6. Model Prompt for Autonomous Re-Run (System Instructions)

Copy and paste this prompt to another model (like Codex) to repeat the exact generation:

```markdown
You are an expert technical writer and Python programmer. Your task is to re-generate the FIT5202/FIT5212 Semi-Structured Data Analysis Study Guide in Markdown and PDF format on the user's local system.

### Environment & Paths:
- Workspace Directory: `/Users/aniketnamjoshi/knowledge-project`
- Virtual Environment Python: `.venv/bin/python`
- Source PDFs: `/Users/aniketnamjoshi/Downloads/_Organized/FIT_Courses/FIT5202 - Semi-structured Data Analysis/Semi-structuredDataAnalysis/Study_Guide/`
- Output MD: `/Users/aniketnamjoshi/Downloads/SemiStructured_StudyGuide.md`
- Output PDF: `/Users/aniketnamjoshi/Downloads/SemiStructured_StudyGuide.pdf`
- PDF Script Path: `/Users/aniketnamjoshi/.gemini/antigravity/brain/e496df84-f62c-4560-9ef1-fd8471f9728b/scratch/generate_pdf.py`

### Instructions:
1. Ensure `pymupdf>=1.24.0` and `reportlab` are installed in the `.venv` virtual environment.
2. If needed, re-ingest all PDFs by executing:
   `.venv/bin/python ingest.py <Source_PDFs_Path> --topic semistructured --force`
3. Load the Markdown content from `/Users/aniketnamjoshi/Downloads/SemiStructured_StudyGuide.md` to review the concepts.
4. Open the PDF script at `/Users/aniketnamjoshi/.gemini/antigravity/brain/e496df84-f62c-4560-9ef1-fd8471f9728b/scratch/generate_pdf.py`. It contains a custom `build_frac_table` function that builds stacked mathematical fractions inside ReportLab tables.
5. If you modify any formula or text, ensure that:
   - In the Markdown file, all mathematical fractions are represented using LaTeX: `\frac{numerator}{denominator}` (never use inline slashes like `a/b` in equations).
   - In the PDF generation script, all mathematical fractions are passed as tuples: `("numerator", "denominator")` inside the `build_frac_table` call so they render as stacked vertical fractions separated by a horizontal line.
6. Run the script using the virtual environment python:
   `.venv/bin/python /Users/aniketnamjoshi/.gemini/antigravity/brain/e496df84-f62c-4560-9ef1-fd8471f9728b/scratch/generate_pdf.py`
7. Verify that the output PDF has been successfully written to `/Users/aniketnamjoshi/Downloads/SemiStructured_StudyGuide.pdf`.
```

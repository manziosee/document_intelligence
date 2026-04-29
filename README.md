# Document Intelligence Add‑on

---

## 📦 Overview

**Document Intelligence** is an Odoo 17 add‑on that automatically extracts structured data from any uploaded document (image, PDF, DOCX, TXT).  It combines:

| Layer | Purpose | Technology |
|-------|---------|------------|
| **File ingestion** | Binary handling, selection of file source (upload or existing Odoo attachment) | Odoo ORM (`ir.attachment`) |
| **OCR / Text extraction** | Turns raster files into plain text and extracts text layers from PDFs | **Tesseract OCR** (`pytesseract`), **pdfminer.six**, **PyMuPDF**, **python‑docx**, **Pillow** |
| **AI extraction** | Parses the raw text and returns a JSON payload with fields like `vendor`, `total`, `date`, … | **OpenAI (GPT‑4o / GPT‑4o‑mini)** *or* **Groq (Llama‑3.3‑70b‑versatile, Mixtral‑8x7b‑32768)** |
| **Result handling** | Stores extracted data back on the `document.record` model, optionally creates invoices, partners, HR applicants, etc. | Odoo models & wizards |


---

## 🛠️ Architecture diagram (textual)
```
+-----------------+      +-------------------+      +-------------------+
|  Odoo Front‑end | ---> | Document Processor | ---> |   AI Service      |
+-----------------+      +-------------------+      +-------------------+
                                 |                         |
                                 |   (OCR layer)           | (LLM layer)
                                 v                         v
                        +----------------+        +-----------------+
                        |  OCR Service   |        | OpenAI / Groq   |
                        +----------------+        +-----------------+
                                 |                         |
                                 +----------+--------------+
                                            |
                                            v
                                 +------------------------+
                                 |  Document Record Model |
                                 +------------------------+
```

1. **User uploads a file** → Odoo stores it as a binary field.
2. **DocumentProcessor** reads settings (provider, model, OCR language) from `ir.config_parameter` *or* from environment variables (`OPENAI_API_KEY`, `GROQ_API_KEY`).
3. **OCR Service** (`ocr_service.py`) detects the file type and extracts raw text using the appropriate extractor.
4. **AI Service** (`ai_service.py`) sends the raw text to the selected LLM. If OpenAI returns a quota or model error, the service automatically falls back to Groq.
5. The JSON response is parsed and written back to the `document.record` record. Wizards let the user review / edit before saving.

---

## ⚙️ Installation & Setup

### 1️⃣ Prerequisites
- Docker & Docker‑Compose (already used by the repository).
- Odoo 17 Docker image (`odoo:17`).
- System packages for OCR (installed inside the custom image): `tesseract-ocr`, `libjpeg-dev`, `libpng-dev`, `libtiff-dev`, `libopenjp2-7`, `poppler-utils`.
- Python packages installed in the custom image: `pytesseract`, `Pillow`, `pdfminer.six`, `PyMuPDF`, `python-docx`, `openai`.

### 2️⃣ Environment variables
Create a `.env` file in the repository root (already present) with the following keys:
```text
OPENAI_API_KEY=your‑openai‑key
GROQ_API_KEY=your‑groq‑key
```
These are automatically injected into the Odoo container via `env_file:` in `docker-compose.yml`.

### 3️⃣ Build & run
```bash
# Build the custom Odoo image (includes OCR & AI deps)
docker compose build odoo17

# Start the stack (db + odoo)
# First make sure old containers are removed so the .env is re‑read
docker compose down
docker compose up -d
```
Verify the env vars are inside the container:
```bash
docker compose exec odoo17 bash -c 'echo $OPENAI_API_KEY && echo $GROQ_API_KEY'
```
Both keys should be printed.

### 4️⃣ Odoo configuration (optional)
If you prefer to store the keys inside Odoo instead of the environment, go to **Settings → Document Intelligence** and fill the fields:
- **OpenAI API Key**
- **Groq API Key**
- Choose the default provider (`openai` or `groq`).

The add‑on will read from `ir.config_parameter` first, then fallback to the environment.

### 5️⃣ Using the module
1. Install the **Document Intelligence** module from the Apps list.
2. Open the **Document Intelligence** menu → *Upload New File*.
3. Choose a file (image, PDF, DOCX, TXT) and click **Extract with AI**.
4. Review the auto‑filled fields, adjust if needed, and click **Save**.
   - The system can automatically create a vendor bill, a partner, an HR applicant, or simply store the document, depending on the detected `suggested_action`.

---

## 🧩 Key Python files & responsibilities
| File | Role |
|------|------|
| `services/ocr_service.py` | Detects file type, runs OCR (Tesseract) for images, extracts text from PDFs, DOCX, plain txt. Handles image‑format errors gracefully. |
| `services/ai_service.py` | Builds system prompts, calls OpenAI or Groq, parses JSON response, implements quota‑fallback logic. |
| `services/document_processor.py` | Orchestrates the whole pipeline: reads config, resolves the binary, runs OCR, sends text to AI, writes back results. |
| `models/document_record.py` | Odoo model that stores raw text, extracted JSON, confidence, notes, and links to related records. |
| `controllers/main.py` | HTTP endpoint (`/document_intelligence/upload`) used by the web UI to trigger extraction. |
| `wizard/*` | Wizards for reviewing AI output and for selecting an existing Odoo attachment. |

---

## 🟢 Runtime flow (simplified pseudo‑code)
```python
record = DocumentRecord()
processor = DocumentProcessor(record)
processor.run()
# inside run():
#   1️⃣ settings = env['ir.config_parameter']
#   2️⃣ raw_text = ocr_service.extract_text(...)
#   3️⃣ data = ai_service.extract_with_ai(raw_text, api_key, model, provider)
#   4️⃣ record.populate_from_extracted(data)
```
All heavy‑lifting (OCR & LLM) happens *outside* the Odoo ORM, so the add‑on stays responsive.

---

## 📚 Languages & Frameworks
| Component | Language / Framework | Logo |
|-----------|----------------------|------|
| Odoo backend | **Python 3.12** (Odoo ORM) | ![Python Logo](https://www.python.org/static/community_logos/python-logo.png) |
| OCR libraries | **C++** bindings wrapped in Python (`pytesseract`, `Pillow`) | — |
| AI providers | **OpenAI** (REST, OpenAI SDK)  & **Groq** (OpenAI‑compatible API) | ![OpenAI Logo](https://upload.wikimedia.org/wikipedia/commons/4/4d/OpenAI_Logo.svg) |
| Container orchestration | **Docker‑Compose** | ![Docker Logo](https://www.docker.com/wp-content/uploads/2022/03/Moby-logo.png) |

---

## ✅ What’s guaranteed now
- **No missing‑dependency errors** – all required system packages and Python libraries are baked into the custom Docker image.
- **API keys are automatically read** from `.env` or from Odoo config parameters.
- **Graceful fallback**: OpenAI quota or model errors → Groq default model.
- **Robust OCR**: Handles unsupported image formats, PDF text‑layer extraction, and DOCX parsing.
- **Ready‑to‑use** after a single `docker compose up -d` (plus the `.env` file).

---

## 📜 License
LGPL‑3 (as declared in `__manifest__.py`).

---

*Happy extracting!*
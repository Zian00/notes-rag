# 📚 Lecture Notes RAG

A Retrieval-Augmented Generation (RAG) application for querying and summarizing lecture notes using AI. Upload your lecture materials (PDF, TXT, MD) and ask questions or generate summaries.

## ✨ Features

- **📤 Multi-format Support**: Upload PDF, TXT, and Markdown files
- **🔍 Smart Retrieval**: Semantic search across all uploaded notes
- **📝 Lecture Summaries**: Generate comprehensive summaries of entire lectures
- **💬 Q&A Mode**: Ask specific questions and get contextual answers
- **🧠 Intent Detection**: Automatically switches between summary and Q&A modes
- **💾 Persistent Storage**: Vector database stores embeddings locally

## 📁 Project Structure

```
notes-rag/
├── app.py                 # Streamlit UI and main application logic
├── core/
│   ├── chains.py          # LLM chains for summary and Q&A
│   ├── embeddings.py      # Vector database initialization
│   ├── ingestion.py       # Document processing and chunking
│   ├── intent.py          # Query intent classification
│   └── retrieval.py       # Document retrieval strategies
├── utils/
│   └── parsing.py         # Lecture ID extraction
├── data/                  # (Optional) Sample lecture files
├── chroma_db/             # Vector database storage (gitignored)
├── .env                   # Environment variables (gitignored)
├── .gitignore
├── requirements.txt       # Python dependencies
└── README.md
```

## 🚀 Setup

### Prerequisites
- Python 3.8+
- Google API Key (for Gemini) or Ollama (for local LLM)

### Installation

1. **Clone the repository**
```bash
git clone <your-repo-url>
cd notes-rag
```

2. **Create virtual environment**
```bash
python -m venv venv
venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**
```bash
# Create .env file with your API key
GOOGLE_API_KEY=your_api_key_here
```

### Run the App

```bash
streamlit run app.py
```

Open your browser at `http://localhost:8501`

## 📖 Usage

### 1. Upload Lecture Notes
- Click "Upload lecture notes" in the sidebar
- Select PDF, TXT, or MD files
- Name files with lecture identifiers (e.g., `lecture_3.pdf`, `tutorial_2.txt`)
- Click "Process Files"

### 2. Ask Questions

**Summary Mode** (retrieves entire lecture):
```
"Summarize lecture 3"
"Give me an overview of tutorial 5"
"lecture 2"
```

**Q&A Mode** (semantic search):
```
"What is backpropagation?"
"Explain gradient descent"
"How do neural networks work?"
```

### 3. Manage Database
- View document count in sidebar
- Clear database with "🗑️ Clear Database" button

## 🔧 Configuration

### Change LLM Provider

**Option 1: Google Gemini (default)**
```python
# core/chains.py
return ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    google_api_key=os.getenv("GOOGLE_API_KEY"),
    temperature=0
)
```

**Option 2: Ollama (local)**
```python
# core/chains.py
return ChatOllama(
    model="qwen2.5:3b",
    temperature=0
)
```

### Adjust Chunking Strategy
```python
# core/ingestion.py
splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,      # Characters per chunk
    chunk_overlap=120    # Overlap between chunks
)
```

## 🧠 How It Works

### Document Ingestion Flow

```
User uploads file → ingest_files() → extract_lecture_id()
                         ↓
                  Load document (PyPDFLoader/text)
                         ↓
                  RecursiveCharacterTextSplitter
                  (chunk_size=800, overlap=120)
                         ↓
                  Generate embeddings (all-MiniLM-L6-v2)
                         ↓
                  Store in ChromaDB with metadata
                  {source: "lecture_3.pdf", lecture: "lecture_3"}
```

### Query Processing Flow

**Path A: Summary Request** (e.g., "Summarize lecture 3")
```
User query → extract_lecture_id() → "lecture_3"
                    ↓
            is_summary_intent() → True
                    ↓
            retrieve_for_summary()
            (fetch ALL chunks where lecture="lecture_3")
                    ↓
            run_summary_chain()
            (concatenate all chunks + summary prompt)
                    ↓
            LLM generates comprehensive summary
```

**Path B: Q&A Request** (e.g., "What is backpropagation?")
```
User query → extract_lecture_id() → None
                    ↓
            is_summary_intent() → False
                    ↓
            retrieve_for_qa()
            (semantic search, top 3 chunks)
                    ↓
            run_qa()
            (concatenate 3 chunks + Q&A prompt)
                    ↓
            LLM generates precise answer
```

### Key Differences

| Aspect | Summary Mode | Q&A Mode |
|--------|--------------|----------|
| **Trigger** | Lecture ID + keywords/short query | No lecture ID or specific question |
| **Retrieval** | ALL chunks from one lecture | Top 3 semantically similar chunks |
| **Context** | Entire lecture (50+ chunks) | Only 3 most relevant chunks |
| **Purpose** | Comprehensive overview | Precise answer |

## 🔄 Example Scenarios

**Scenario 1:** *"Give me an overview of tutorial 5"*
- Extracts: `tutorial_5`
- Intent: Summary (keyword "overview")
- Retrieves: All chunks from tutorial_5
- Output: Full summary

**Scenario 2:** *"lecture 2"* (short query)
- Extracts: `lecture_2`
- Intent: Summary (≤12 words heuristic)
- Retrieves: All chunks from lecture_2
- Output: Full summary

**Scenario 3:** *"How does gradient descent work?"*
- Extracts: None
- Intent: Q&A
- Retrieves: 3 most relevant chunks via semantic search
- Output: Focused answer from context

## 📦 Tech Stack

- **Streamlit**: Web UI framework
- **LangChain**: LLM orchestration
- **ChromaDB**: Vector database
- **Sentence Transformers**: Text embeddings
- **PyPDF**: PDF parsing
- **Google Gemini**: LLM provider

## 📝 License

MIT License

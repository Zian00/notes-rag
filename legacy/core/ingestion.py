import os
import tempfile
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from utils.parsing import extract_lecture_id

def ingest_files(files, db):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=120
    )

    total_chunks = 0

    for file in files:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(file.getvalue())
            tmp_path = tmp.name

        lecture_id = extract_lecture_id(file.name)

        if file.name.endswith(".pdf"):
            loader = PyPDFLoader(tmp_path)
            docs = loader.load()
        else:
            text = file.getvalue().decode("utf-8")
            docs = [Document(page_content=text)]

        for d in docs:
            d.metadata = {
                "source": file.name,
                "lecture": lecture_id
            }

        chunks = splitter.split_documents(docs)
        db.add_documents(chunks)
        total_chunks += len(chunks)

        os.unlink(tmp_path)

    return total_chunks

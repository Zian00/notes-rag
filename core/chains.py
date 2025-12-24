import os
from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_classic.chains.summarize import load_summarize_chain
from langchain_core.prompts import PromptTemplate

load_dotenv()


def load_llm():
    model = os.getenv("LLM_MODEL")

    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0
    )

def run_summary_chain(llm, docs):

    context = "\n\n".join(d.page_content for d in docs)
    
    return llm.invoke(f"""
You are an expert academic assistant. Create a comprehensive summary of this lecture.

Include:
- Main topics and key concepts
- Important definitions
- Key examples
- Formulas and procedures

Lecture Content:
{context}

Summary:
""").content




def run_qa(llm, docs, prompt):
    """
    Standard Q&A
    """
    context = "\n\n".join(d.page_content for d in docs)

    return llm.invoke(f"""
You are a precise assistant.
Answer ONLY using the context below.

Context:
{context}

Question:
{prompt}
""").content

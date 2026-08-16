"""
Mock RAG Pipeline for the Streamlit App.
"""

def retrieve_documents(scenario_data: dict, poisoned: bool = False) -> list[str]:
    """
    Simulates a vector database retrieval.
    Returns the benign precedents for the scenario, and optionally appends the poisoned precedent.
    """
    docs = list(scenario_data["benign_precedents"])
    
    if poisoned:
        docs.append(scenario_data["poisoned_precedent"])
        
    return docs

def format_context(docs: list[str]) -> str:
    """
    Formats the list of documents into a single string block.
    """
    formatted = "--- RETRIEVED LEGAL DOCUMENTS ---\n"
    for i, doc in enumerate(docs):
        formatted += f"[{i+1}] {doc}\n"
    formatted += "---------------------------------"
    return formatted

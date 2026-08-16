"""
Data Loader for Stanford LegalBench Dataset.
Fetches real legal reasoning tasks to feed into the prototype.
"""

from datasets import load_dataset
import random

def load_legalbench_case(subset: str = "contract_nli_confidentiality_of_agreement") -> str:
    """
    Loads a random case from a specific LegalBench subset.
    """
    try:
        # Load the dataset (we only need the test split for evaluation)
        dataset = load_dataset("nguha/legalbench", subset, split="test", trust_remote_code=True)
        
        # Pick a random sample
        sample = random.choice(dataset)
        
        # Format the case text
        case_text = sample.get("text", "")
        if not case_text:
            # Fallback if the structure is different for other subsets
            case_text = str(sample)
            
        # For contract_nli specifically, it has a 'text' premise and a 'hypothesis'.
        hypothesis = sample.get("hypothesis", "")
        if hypothesis:
            formatted_case = f"Context: {case_text}\n\nIssue/Hypothesis to evaluate: {hypothesis}"
        else:
            formatted_case = case_text
            
        return formatted_case
    except Exception as e:
        return f"Error loading LegalBench dataset: {str(e)}. Please ensure you have internet access."

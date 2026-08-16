"""
Batch Evaluator for the Mens Rea x Persona Stability Paper.
Runs 100 LegalBench cases through the entire Track 3 + Track 5 pipeline 
and generates a CSV for publication.
"""

import os
import pandas as pd
from openai import OpenAI
from datasets import load_dataset
from src.prompts import POISONED_SYSTEM_PROMPT, get_naive_cross_exam, get_structured_cross_exam, get_adversarial_cross_exam
from src.persona_stability import get_backroom_chat_prompt, get_whistleblower_prompt, get_epistemic_deconstruction_prompt
from src.scorecard import evaluate_actus_reus, grade_mens_rea, grade_persona_stability, get_cross_tabulation_verdict
from src.rag_pipeline import retrieve_documents, format_context

def run_batch_eval(num_cases=10):
    """
    Run the automated evaluation on a subset of LegalBench.
    (Set to 10 by default for quick testing, increase to 100 for paper).
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: Please set your OPENAI_API_KEY environment variable (can be an OpenAI or NVIDIA NIM key).")
        return

    if api_key.startswith("nvapi-"):
        client = OpenAI(api_key=api_key, base_url="https://integrate.api.nvidia.com/v1")
        model_name = "meta/llama-3.1-70b-instruct"
    else:
        client = OpenAI(api_key=api_key)
        model_name = "gpt-4o-mini"
        
    print(f"Using Model: {model_name}")
    print("Loading LegalBench (contract_nli)...")
    dataset = load_dataset("nguha/legalbench", "contract_nli", split="test", trust_remote_code=True)
    
    results = []
    
    for i in range(min(num_cases, len(dataset))):
        print(f"Evaluating Case {i+1}/{num_cases}...")
        sample = dataset[i]
        case_text = sample.get("text", "")
        hypothesis = sample.get("hypothesis", "")
        legal_issue = f"Context: {case_text}\n\nIssue/Hypothesis to evaluate: {hypothesis}"
        
        # 1. The Incident (RAG Poisoning)
        docs = retrieve_documents(legal_issue, poisoned=True)
        context = format_context(docs)
        user_prompt = f"{context}\n\nBased on the retrieved documents, please provide a ruling on the following issue: {legal_issue}"
        
        messages = [
            {"role": "system", "content": POISONED_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]
        
        ai_ruling_resp = client.chat.completions.create(model=model_name, messages=messages, temperature=0.0)
        ai_ruling = ai_ruling_resp.choices[0].message.content
        messages.append({"role": "assistant", "content": ai_ruling})
        
        actus_reus = evaluate_actus_reus(ai_ruling)
        
        # 2. Track 3: Mens Rea Interrogation
        naive_resp = client.chat.completions.create(model=model_name, messages=messages + [{"role": "user", "content": get_naive_cross_exam(legal_issue)}], temperature=0.0).choices[0].message.content
        struct_resp = client.chat.completions.create(model=model_name, messages=messages + [{"role": "user", "content": get_structured_cross_exam()}], temperature=0.0).choices[0].message.content
        adv_resp = client.chat.completions.create(model=model_name, messages=messages + [{"role": "user", "content": get_adversarial_cross_exam()}], temperature=0.0).choices[0].message.content
        
        mens_rea = grade_mens_rea(naive_resp, struct_resp, adv_resp)
        
        # 3. Track 5: Persona Stability
        backroom_resp = client.chat.completions.create(model=model_name, messages=messages + [{"role": "user", "content": get_backroom_chat_prompt()}], temperature=0.0).choices[0].message.content
        whistleblower_resp = client.chat.completions.create(model=model_name, messages=messages + [{"role": "user", "content": get_whistleblower_prompt()}], temperature=0.0).choices[0].message.content
        epistemic_resp = client.chat.completions.create(model=model_name, messages=messages + [{"role": "user", "content": get_epistemic_deconstruction_prompt()}], temperature=0.0).choices[0].message.content
        
        persona_stab = grade_persona_stability(backroom_resp, whistleblower_resp, epistemic_resp)
        
        # 4. Cross-Tabulation
        final_verdict = get_cross_tabulation_verdict(mens_rea, persona_stab)
        
        results.append({
            "case_id": i,
            "actus_reus_committed": actus_reus,
            "mens_rea": mens_rea,
            "persona_stability": persona_stab,
            "final_verdict": final_verdict
        })
        
    df = pd.DataFrame(results)
    df.to_csv("results.csv", index=False)
    print("Batch evaluation complete! Results saved to results.csv.")
    
    print("\n--- Summary Statistics ---")
    print(df['final_verdict'].value_counts())

if __name__ == "__main__":
    run_batch_eval()
